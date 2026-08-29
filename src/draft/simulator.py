"""Monte Carlo draft simulator.

Handoff sec.15/17. The simulator exists to answer one question that a ranking
alone cannot: *will this player still be there at my next pick?*

That question is what converts a ranking into a decision. Taking the best player
available is only correct if the alternative would not have survived; when two
players are close in value but one will be gone in four picks and the other will
last two rounds, the ranking is the wrong guide and availability is the right one.

Opponent behaviour is stochastic and configurable (``config/model.yaml``), mixing
ADP-followers, value-followers and need-based drafters, because a simulator where
every opponent drafts perfectly to ADP would give falsely confident answers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass
class DraftPlayer:
    """Minimal player record the simulator needs."""

    player_id: str
    name: str
    position: str
    value: float
    model_rank: int
    adp: float


@dataclass
class SimulationResult:
    """Availability probabilities and roster outcomes across many drafts."""

    availability: dict[str, float]
    picked_by_round: dict[str, list[int]] = field(default_factory=dict)
    n_simulations: int = 0

    def probability_available_at(self, player_id: str) -> float:
        return self.availability.get(player_id, 0.0)


def snake_pick_order(teams: int, rounds: int) -> list[int]:
    """Team index (0-based) making each overall pick in a snake draft."""
    order: list[int] = []
    for round_index in range(rounds):
        seats = range(teams) if round_index % 2 == 0 else range(teams - 1, -1, -1)
        order.extend(seats)
    return order


def picks_for_slot(slot: int, teams: int, rounds: int) -> list[int]:
    """Overall pick numbers (1-based) belonging to a given draft slot."""
    order = snake_pick_order(teams, rounds)
    return [i + 1 for i, team in enumerate(order) if team == slot - 1]


class DraftSimulator:
    """Simulates opponent drafting to estimate player availability."""

    def __init__(self, model_cfg: Mapping, league_cfg, rng: np.random.Generator | None = None):
        self.cfg = model_cfg.get("draft_simulation", {})
        self.league = league_cfg
        self.teams = league_cfg.teams
        self.rounds = int(league_cfg.draft.get("rounds", league_cfg.roster_size))
        self.adp_noise = float(self.cfg.get("adp_noise_sd_picks", 6.0))
        self.random_rate = float(self.cfg.get("random_deviation_rate", 0.20))
        self.archetypes = self.cfg.get("manager_archetypes", {"adp_follower": 1.0})
        self.rng = rng or np.random.default_rng(20262027)
        self.eligibility = league_cfg.position_eligibility
        self.starting = league_cfg.starting_slots

    def _assign_archetypes(self) -> list[str]:
        names = list(self.archetypes)
        probabilities = np.array([float(self.archetypes[n]) for n in names], dtype=float)
        probabilities = probabilities / probabilities.sum()
        return list(self.rng.choice(names, size=self.teams, p=probabilities))

    def _score_candidates(
        self, pool: list[DraftPlayer], archetype: str, roster_counts: Mapping[str, int], pick_number: int
    ) -> np.ndarray:
        """Lower is more desirable - everything is expressed as a pseudo-pick-number."""
        if archetype == "value_follower":
            base = np.array([p.model_rank for p in pool], dtype=float)
        elif archetype == "need_based":
            base = np.array([p.adp for p in pool], dtype=float)
            # Discount positions already filled to their starting requirement.
            for index, player in enumerate(pool):
                needed = self._slots_for(player.position)
                have = roster_counts.get(player.position, 0)
                if have >= needed:
                    base[index] += 12.0 * (have - needed + 1)
        else:  # adp_follower
            base = np.array([p.adp for p in pool], dtype=float)

        noise = self.rng.normal(0.0, self.adp_noise, size=len(pool))
        return base + noise

    def _slots_for(self, position: str) -> int:
        slots = self.eligibility.get(position, [])
        return max(1, sum(int(self.starting.get(s, 0)) for s in slots if s not in ("UTIL",)))

    def simulate(
        self,
        players: Sequence[DraftPlayer],
        n_simulations: int | None = None,
        already_drafted: Sequence[str] = (),
        start_pick: int = 1,
        target_picks: Sequence[int] = (),
    ) -> SimulationResult:
        """Run many drafts and record how often each player survives to each pick.

        ``target_picks`` are the overall pick numbers we care about (ours). A
        player is 'available at pick N' if no simulated opponent took them before N.
        """
        n_simulations = int(n_simulations or self.cfg.get("n_simulations", 2000))
        drafted = set(already_drafted)
        pool_master = [p for p in players if p.player_id not in drafted]
        if not pool_master or not target_picks:
            return SimulationResult(availability={}, n_simulations=n_simulations)

        order = snake_pick_order(self.teams, self.rounds)
        first_target = min(target_picks)
        survived: dict[str, np.ndarray] = {
            p.player_id: np.zeros(len(target_picks), dtype=float) for p in pool_master
        }
        target_list = sorted(target_picks)

        for _ in range(n_simulations):
            available = list(pool_master)
            roster_counts: list[dict[str, int]] = [{} for _ in range(self.teams)]
            manager_types = self._assign_archetypes()
            taken_at: dict[str, int] = {}

            for overall in range(start_pick, len(order) + 1):
                if not available:
                    break
                if overall > max(target_list):
                    break
                if overall in target_list:
                    # Our own pick: don't model it, we're measuring what reaches us.
                    continue

                team_index = order[overall - 1]
                archetype = manager_types[team_index]

                if self.rng.random() < self.random_rate:
                    # A manager going off-board entirely.
                    choice = int(self.rng.integers(0, min(len(available), 25)))
                else:
                    scores = self._score_candidates(available, archetype, roster_counts[team_index], overall)
                    choice = int(np.argmin(scores))

                player = available.pop(choice)
                taken_at[player.player_id] = overall
                roster_counts[team_index][player.position] = roster_counts[team_index].get(player.position, 0) + 1

            for player in pool_master:
                when = taken_at.get(player.player_id)
                for index, pick in enumerate(target_list):
                    if when is None or when > pick:
                        survived[player.player_id][index] += 1.0

        availability = {
            player_id: float(counts[0] / n_simulations) for player_id, counts in survived.items()
        }
        by_pick = {
            player_id: [float(c / n_simulations) for c in counts] for player_id, counts in survived.items()
        }
        result = SimulationResult(availability=availability, n_simulations=n_simulations)
        result.picked_by_round = by_pick
        return result

    def best_available_curve(
        self,
        players: Sequence[DraftPlayer],
        target_picks: Sequence[int],
        n_simulations: int | None = None,
        already_drafted: Sequence[str] = (),
        start_pick: int = 1,
    ) -> dict[int, list[str]]:
        """Who the best player still on the board is, at each of a set of picks.

        ``simulate`` answers "will *this* player last until my pick"; this answers
        "what will my pick be worth", which is the question a pick trade turns on.
        Every pick in ``target_picks`` is filled by taking the highest-value
        player remaining rather than by the opponent model, since that is what a
        pick is: an option on the best thing left.

        Returns pick number -> the player taken there in each simulation, so the
        caller can price the pick in whatever currency it cares about.
        """
        n_simulations = int(n_simulations or self.cfg.get("n_simulations", 2000))
        drafted = set(already_drafted)
        pool_master = [p for p in players if p.player_id not in drafted]
        targets = sorted(set(int(p) for p in target_picks))
        if not pool_master or not targets:
            return {}

        order = snake_pick_order(self.teams, self.rounds)
        last_target = max(targets)
        taken_at_target: dict[int, list[str]] = {pick: [] for pick in targets}

        for _ in range(n_simulations):
            available = list(pool_master)
            roster_counts: list[dict[str, int]] = [{} for _ in range(self.teams)]
            manager_types = self._assign_archetypes()

            for overall in range(start_pick, min(len(order), last_target) + 1):
                if not available:
                    break

                if overall in taken_at_target:
                    choice = int(np.argmax([p.value for p in available]))
                    taken_at_target[overall].append(available[choice].player_id)
                    available.pop(choice)
                    continue

                team_index = order[overall - 1]
                archetype = manager_types[team_index]
                if self.rng.random() < self.random_rate:
                    choice = int(self.rng.integers(0, min(len(available), 25)))
                else:
                    scores = self._score_candidates(available, archetype, roster_counts[team_index], overall)
                    choice = int(np.argmin(scores))

                player = available.pop(choice)
                roster_counts[team_index][player.position] = (
                    roster_counts[team_index].get(player.position, 0) + 1
                )

        return taken_at_target


def board_to_players(board: pd.DataFrame) -> list[DraftPlayer]:
    """Convert a draft board frame into simulator inputs.

    Players without ADP fall back to their model rank, which assumes the market
    agrees with us about them. That is the neutral assumption, and it is flagged
    in the board's ``value_flag`` column as ``no_adp``.
    """
    players: list[DraftPlayer] = []
    for row in board.itertuples(index=False):
        adp = getattr(row, "adp", np.nan)
        model_rank = int(getattr(row, "model_rank", 999))
        if adp is None or (isinstance(adp, float) and np.isnan(adp)):
            adp = float(model_rank)
        players.append(
            DraftPlayer(
                player_id=str(getattr(row, "player_id", getattr(row, "player_name", ""))),
                name=str(getattr(row, "player_name", "")),
                position=str(getattr(row, "position", "")),
                value=float(getattr(row, "projected_season_value", 0.0)),
                model_rank=model_rank,
                adp=float(adp),
            )
        )
    return players
