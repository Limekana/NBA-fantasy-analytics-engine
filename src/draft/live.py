"""Live draft assistant.

Handoff sec.21: *"Keep the reasoning visible. The system should never output only
PICK PLAYER X. It should explain the calculation."*

So every recommendation carries its arithmetic. The core idea is that the best
pick is rarely the highest-ranked player left - it is the player whose value you
would lose by waiting. That is the difference between:

    value now                  what this player is worth
    value if you wait          what you would likely get at your next pick instead

and the recommendation maximises the difference, not the raw ranking. A star who
will certainly still be there in two picks is worth less *right now* than a
slightly worse player who certainly will not be.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.adp.loader import normalise_name
from src.draft.simulator import DraftSimulator, board_to_players, picks_for_slot


@dataclass
class Recommendation:
    player_name: str
    position: str
    model_rank: int
    tier: str
    value: float
    adp: float | None
    availability_next_pick: float
    urgency: float
    reasoning: list[str]


def _fmt(value, spec: str = ".1f", missing: str = "n/a") -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return missing
    return format(value, spec)


class DraftAssistant:
    """Interactive-draft decision support."""

    def __init__(self, board: pd.DataFrame, league_cfg, model_cfg: Mapping, rng=None):
        self.board = board.reset_index(drop=True)
        self._lookup_cache: dict[str, list] | None = None
        self.league = league_cfg
        self.model_cfg = model_cfg
        self.simulator = DraftSimulator(model_cfg, league_cfg, rng=rng)

    def _remaining(self, drafted_names: Sequence[str]) -> pd.DataFrame:
        return self.board[~self.board.index.isin(self._taken_index(drafted_names))]

    def _taken_index(self, drafted_names: Sequence[str]) -> set:
        """Board rows matching the drafted names, matched forgivingly.

        Names are matched on their normalised form, so mid-draft typing does not
        have to reproduce accents, hyphens, punctuation or suffixes: "Luka
        Doncic" finds "Luka Dončić", "PJ Washington" finds "P.J. Washington".
        A strict comparison would fail silently and, far worse, leave an already
        drafted player on the board to be recommended again.
        """
        lookup = self._name_lookup()
        taken: set = set()
        for name in drafted_names:
            key = normalise_name(name)
            if key in lookup:
                taken.update(lookup[key])
        return taken

    def _name_lookup(self) -> dict[str, list]:
        if self._lookup_cache is None:
            lookup: dict[str, list] = {}
            for index, name in self.board["player_name"].items():
                lookup.setdefault(normalise_name(name), []).append(index)
            self._lookup_cache = lookup
        return self._lookup_cache

    def unmatched_names(self, drafted_names: Sequence[str]) -> list[tuple[str, list[str]]]:
        """Drafted names that matched nobody, each with close suggestions.

        This is the failure that actually costs picks: a mistyped name leaves a
        player on the board, and the assistant confidently recommends someone who
        is already gone. It must be reported loudly, never swallowed.
        """
        lookup = self._name_lookup()
        board_names = list(self.board["player_name"])
        problems: list[tuple[str, list[str]]] = []
        for name in drafted_names:
            if normalise_name(name) in lookup:
                continue
            suggestions = difflib.get_close_matches(str(name), board_names, n=3, cutoff=0.6)
            problems.append((str(name), suggestions))
        return problems

    def recommend(
        self,
        my_slot: int,
        current_pick: int,
        drafted_names: Sequence[str] = (),
        my_roster: Sequence[str] = (),
        top_n: int = 5,
        n_simulations: int | None = None,
    ) -> dict:
        """Produce a full recommendation package for the pick on the clock."""
        remaining = self._remaining(drafted_names)
        if remaining.empty:
            return {"error": "No players remain on the board."}

        rounds = int(self.league.draft.get("rounds", self.league.roster_size))
        my_picks = picks_for_slot(my_slot, self.league.teams, rounds)
        future = [p for p in my_picks if p > current_pick]
        next_pick = future[0] if future else None

        # Availability at our NEXT pick: the number that decides "now or wait".
        availability: dict[str, float] = {}
        if next_pick is not None:
            players = board_to_players(remaining)
            result = self.simulator.simulate(
                players,
                n_simulations=n_simulations,
                already_drafted=[],
                start_pick=current_pick,
                target_picks=[next_pick],
            )
            availability = result.availability

        top = remaining.head(max(top_n * 4, 20)).copy()
        key = "player_id" if "player_id" in top.columns else "player_name"
        top["avail_next"] = top[key].astype(str).map(availability).fillna(1.0 if next_pick is None else 0.5)

        # Expected value if we pass: what we would likely still get next time.
        value_column = "projected_season_value"
        fallback_value = self._expected_best_available(top, value_column)

        recommendations: list[Recommendation] = []
        for row in top.itertuples(index=False):
            value = float(getattr(row, value_column, 0.0))
            avail = float(getattr(row, "avail_next"))
            # Urgency = value lost by waiting. If they survive we get them anyway;
            # if not, we fall back to the best player likely to still be there.
            urgency = (1.0 - avail) * max(value - fallback_value, 0.0)

            reasoning = self._explain(row, value, avail, fallback_value, urgency, next_pick, my_roster)
            recommendations.append(
                Recommendation(
                    player_name=str(getattr(row, "player_name")),
                    position=str(getattr(row, "position", "")),
                    model_rank=int(getattr(row, "model_rank", 0)),
                    tier=str(getattr(row, "tier", "")),
                    value=value,
                    adp=float(getattr(row, "adp")) if not pd.isna(getattr(row, "adp", np.nan)) else None,
                    availability_next_pick=avail,
                    urgency=urgency,
                    reasoning=reasoning,
                )
            )

        best_value = max(recommendations, key=lambda r: r.value)
        most_urgent = max(recommendations, key=lambda r: r.urgency)
        # Recommend on urgency when it meaningfully separates candidates,
        # otherwise just take the best player.
        pick = most_urgent if most_urgent.urgency > 0.02 * best_value.value else best_value

        falls = [r for r in recommendations if r.adp is not None and r.adp - current_pick >= 8]
        best_fall = max(falls, key=lambda r: r.value) if falls else None

        return {
            "unmatched_drafted": self.unmatched_names(drafted_names),
            "current_pick": current_pick,
            "next_pick": next_pick,
            "picks_until_next": (next_pick - current_pick) if next_pick else None,
            "top_available": sorted(recommendations, key=lambda r: -r.value)[:top_n],
            "most_urgent": sorted(recommendations, key=lambda r: -r.urgency)[:top_n],
            "best_value_fall": best_fall,
            "recommendation": pick,
            "positional_need": self._positional_need(my_roster),
            "expected_fallback_value": fallback_value,
        }

    def _expected_best_available(self, top: pd.DataFrame, value_column: str) -> float:
        """Expected value of the best player still there at our next pick."""
        if top.empty:
            return 0.0
        rows = top.sort_values(value_column, ascending=False)
        survival = 1.0
        expected = 0.0
        for row in rows.itertuples(index=False):
            probability = float(getattr(row, "avail_next"))
            expected += survival * probability * float(getattr(row, value_column, 0.0))
            survival *= 1.0 - probability
            if survival < 1e-4:
                break
        return expected

    def _positional_need(self, my_roster: Sequence[str]) -> dict:
        """Which starting slots are still unfilled.

        Handoff sec.18 warns against overvaluing position because of the two UTIL
        slots, so this reports need without letting it dominate the ranking.
        """
        counts: dict[str, int] = {}
        for entry in my_roster:
            position = str(entry).strip().upper()
            counts[position] = counts.get(position, 0) + 1
        need = {}
        for slot, required in self.league.starting_slots.items():
            if slot == "UTIL":
                continue
            have = counts.get(slot, 0)
            if have < required:
                need[slot] = required - have
        return need

    def _explain(self, row, value, avail, fallback, urgency, next_pick, my_roster) -> list[str]:
        """The visible arithmetic behind a recommendation."""
        lines = [
            f"Model rank {int(getattr(row, 'model_rank', 0))} (tier {getattr(row, 'tier', '?')}), "
            f"projected season value {value:.0f}.",
            f"Projected {_fmt(getattr(row, 'projected_fp_game', np.nan), '.1f')} FP/game "
            f"over {_fmt(getattr(row, 'projected_games', np.nan), '.0f')} games; "
            f"floor {_fmt(getattr(row, 'floor', np.nan), '.0f')} / "
            f"ceiling {_fmt(getattr(row, 'ceiling', np.nan), '.0f')}.",
            f"Lock-In weekly value {_fmt(getattr(row, 'lockin_value', np.nan), '.1f')} "
            f"({_fmt(getattr(row, 'lock_in_advantage', np.nan), '+.1f')} vs raw FP/game) "
            f"on {_fmt(getattr(row, 'games_per_week', np.nan), '.2f')} games/week.",
        ]

        adp = getattr(row, "adp", np.nan)
        if not pd.isna(adp):
            gap = getattr(row, "adp_vs_model", np.nan)
            flag = getattr(row, "value_flag", "")
            lines.append(f"ADP {adp:.0f} vs model rank {int(getattr(row,'model_rank',0))} "
                         f"(gap {_fmt(gap, '+.0f')}) -> {flag}.")
        else:
            lines.append("No ADP loaded for this player - market cost unknown.")

        if next_pick is not None:
            lines.append(
                f"P(still available at pick {next_pick}) = {avail:.0%}. "
                f"Waiting is worth {fallback:.0f} in expectation, so passing costs "
                f"{urgency:.0f} ({(1-avail):.0%} chance of losing {max(value-fallback,0):.0f})."
            )

        risk = getattr(row, "risk", np.nan)
        if not pd.isna(risk):
            lines.append(f"Risk {risk:.2f} (availability, variance, role). "
                         f"Archetype: {getattr(row, 'archetype', 'n/a')}.")

        notes = getattr(row, "assumption_notes", "")
        if notes:
            lines.append(f"Assumptions in play: {notes}")
        return lines


def format_recommendation(package: dict) -> str:
    """Render the assistant's output as the handoff's example terminal report."""
    if "error" in package:
        return package["error"]

    out: list[str] = []

    unmatched = package.get("unmatched_drafted") or []
    if unmatched:
        out.append("!" * 72)
        out.append("WARNING - these drafted names matched NOBODY on the board:")
        for name, suggestions in unmatched:
            hint = f"   did you mean: {', '.join(suggestions)}" if suggestions else "   (no close match found)"
            out.append(f"  x {name}")
            out.append(hint)
        out.append("")
        out.append("They are still being treated as AVAILABLE, so a recommendation")
        out.append("below may already be off the board. Fix the spelling and re-run.")
        out.append("!" * 72)

    pick = package["current_pick"]
    next_pick = package["next_pick"]
    out.append("=" * 72)
    header = f"PICK {pick}"
    if next_pick:
        out.append(f"{header}   (next pick: {next_pick}, {package['picks_until_next']} picks away)")
    else:
        out.append(f"{header}   (final pick)")
    out.append("=" * 72)

    out.append("\nTOP AVAILABLE")
    for index, rec in enumerate(package["top_available"], 1):
        adp = f"ADP {rec.adp:.0f}" if rec.adp is not None else "ADP n/a"
        out.append(
            f"  {index}. {rec.player_name:<26} {rec.position:<3} tier {rec.tier:<2} "
            f"value {rec.value:7.0f}  {adp:<9} avail@next {rec.availability_next_pick:5.0%}"
        )

    out.append("\nMOST URGENT (likely gone before your next pick)")
    for index, rec in enumerate(package["most_urgent"][:3], 1):
        out.append(
            f"  {index}. {rec.player_name:<26} urgency {rec.urgency:7.1f}  "
            f"avail@next {rec.availability_next_pick:5.0%}"
        )

    fall = package.get("best_value_fall")
    out.append("\nBEST VALUE FALL")
    if fall:
        out.append(f"  {fall.player_name}   ADP: {fall.adp:.0f}   Model: {fall.model_rank}")
        out.append(f"  Availability until next pick: {fall.availability_next_pick:.0%}")
    else:
        out.append("  None - nobody is falling meaningfully past their ADP.")

    need = package["positional_need"]
    out.append("\nPOSITIONAL NEED")
    out.append("  " + (", ".join(f"{k} x{v}" for k, v in need.items()) if need else "None"))

    rec = package["recommendation"]
    out.append("\nRECOMMENDATION")
    out.append(f"  >>> {rec.player_name} ({rec.position}, tier {rec.tier})")
    out.append("\n  WHY:")
    for line in rec.reasoning:
        out.append(f"    - {line}")
    out.append("=" * 72)
    return "\n".join(out)
