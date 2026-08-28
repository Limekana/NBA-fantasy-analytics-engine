"""Rookie projections.

Rookies are the one group the statistical engine cannot touch: they have no NBA
game logs, so there is nothing to blend, shrink or age-adjust. Everything about
them is a prior, which makes this the most assumption-dense module in the project
and the one to treat most sceptically.

Three things make rookies genuinely different in a Lock-In league, and the model
handles them separately rather than collapsing them into one "rookie discount":

1.  **Role uncertainty is a risk.** Minutes may not materialise; a coach may bury
    them. This feeds ``risk`` and is scaled by draft position and how confident we
    are in the reporting.

2.  **Game-to-game variance is an ASSET.** Rookies are erratic, and Lock-In keeps
    the good game and discards the bad ones. A volatile rookie is worth more than
    a metronome with the same average - the opposite of the season-long intuition.

3.  **Uncertainty shrinks with pedigree.** A consensus top-three pick walking into
    a defined role has a much narrower outcome distribution than a late-lottery
    pick behind three veterans. Volatility is scaled by draft slot, so the model
    does not treat "generational prospect" and "raw project" as the same bet.

The distribution handed to the Lock-In simulator is synthesised from a gamma with
the projected mean and a confidence-scaled coefficient of variation. Gamma is used
rather than a normal because real box scores are right-skewed, and that right tail
is exactly what Lock-In monetises.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.distributions.profile import BONUS_COLUMNS, PlayerProfile
from src.projections.baseline import RATE_STATS, PlayerProjection
from src.scoring import ScoringEngine

# Coefficient of variation for a rookie's game-to-game fantasy scoring, by how
# confident we are in the projected role. Real NBA veterans sit near 0.30; rookies
# are materially noisier, and less certain ones noisier still.
CV_BY_CONFIDENCE: dict[str, float] = {
    "high": 0.38,
    "medium": 0.46,
    "low": 0.55,
}

# Role uncertainty (0 = certain, 1 = unknown), feeding the composite risk score.
ROLE_UNCERTAINTY_BY_CONFIDENCE: dict[str, float] = {
    "high": 0.35,
    "medium": 0.55,
    "low": 0.75,
}


def _confidence_of(entry: Mapping) -> str:
    value = str(entry.get("confidence", "medium")).lower()
    return value if value in CV_BY_CONFIDENCE else "medium"


def build_rookie_projections(
    assumptions: Mapping,
    model_cfg: Mapping,
    engine: ScoringEngine,
    n_samples: int = 400,
    seed: int = 20262027,
) -> tuple[list[PlayerProjection], list[PlayerProfile]]:
    """Turn the ``rookies`` block of assumptions.yaml into projections + profiles.

    Returns ``([], [])`` when no rookies are configured, so the pipeline works
    unchanged for anyone who does not want them.
    """
    rookies = assumptions.get("rookies") or {}
    if not rookies:
        return [], []

    rng = np.random.default_rng(seed)
    projections: list[PlayerProjection] = []
    profiles: list[PlayerProfile] = []

    for name, entry in rookies.items():
        if not isinstance(entry, Mapping) or "per36" not in entry:
            continue

        per36 = {stat: float(entry["per36"].get(stat, 0.0)) for stat in RATE_STATS}
        minutes = float(entry.get("minutes", 24.0))
        confidence = _confidence_of(entry)
        player_id = f"ROOKIE_{name.replace(' ', '_')}"

        projected_stats = {stat: value * minutes / 36.0 for stat, value in per36.items()}

        note_parts = ["ASSUMPTION: rookie prior, no NBA game logs exist"]
        if entry.get("note"):
            note_parts.append(str(entry["note"]))
        if confidence == "low":
            note_parts.append("VERIFY: low-confidence source (possibly a mock draft)")

        projections.append(
            PlayerProjection(
                player_id=player_id,
                player_name=name,
                team=str(entry.get("team", "")),
                position=str(entry.get("position", "")),
                projected_minutes=minutes,
                projected_stats=projected_stats,
                age=float(entry.get("age", 20.0)),
                role_uncertainty=float(
                    entry.get("role_uncertainty", ROLE_UNCERTAINTY_BY_CONFIDENCE[confidence])
                ),
                assumption_notes="; ".join(note_parts),
                seasons_used=(),
                is_synthetic=False,
            )
        )

        profiles.append(
            _synthesise_profile(
                player_id=player_id,
                name=name,
                entry=entry,
                projected_stats=projected_stats,
                minutes=minutes,
                confidence=confidence,
                engine=engine,
                model_cfg=model_cfg,
                rng=rng,
                n_samples=n_samples,
            )
        )

    return projections, profiles


def _synthesise_profile(
    player_id: str,
    name: str,
    entry: Mapping,
    projected_stats: Mapping[str, float],
    minutes: float,
    confidence: str,
    engine: ScoringEngine,
    model_cfg: Mapping,
    rng: np.random.Generator,
    n_samples: int,
) -> PlayerProfile:
    """Build a plausible game-level distribution for a player with no game log.

    The mean is anchored to the projected stat line scored through the real
    engine. The spread comes from a confidence-scaled CV, tightened for high draft
    picks whose role is more secure. Bonus rates are then measured off the
    simulated games rather than guessed at separately, which keeps them
    internally consistent with the distribution.
    """
    cv = float(entry.get("cv", CV_BY_CONFIDENCE[confidence]))

    # Pedigree tightens the distribution: a top-3 pick's floor is much better
    # supported than a late first-rounder's.
    draft_pick = entry.get("draft_pick")
    if draft_pick is not None:
        pick = float(draft_pick)
        if pick <= 3:
            cv *= 0.88
        elif pick <= 10:
            cv *= 0.95
        elif pick > 20:
            cv *= 1.08

    # Simulate whole stat lines, not just totals, so bonus rates fall out of the
    # same process that produces the scores.
    shape = max(1.0 / cv**2, 0.8)
    rows = []
    for _ in range(n_samples):
        multiplier = float(rng.gamma(shape, 1.0 / shape))
        row = {}
        for stat, value in projected_stats.items():
            noise = rng.normal(1.0, 0.22)
            row[stat] = max(0.0, value * multiplier * noise)
        rows.append(row)

    frame = pd.DataFrame(rows)
    scored = engine.score_dataframe(frame)
    sample = scored["fantasy_points"].to_numpy(dtype=float)

    # Re-centre exactly onto the deterministic projection.
    target = engine.score_game(projected_stats)
    if sample.mean() > 0:
        sample = sample * (target / sample.mean())

    dist_cfg = model_cfg.get("distributions", {})
    percentiles = list(dist_cfg.get("percentiles", [10, 25, 50, 75, 90, 95]))
    thresholds = list(dist_cfg.get("threshold_probabilities", [30, 40, 50, 60]))

    bonus_rates = {
        rate_name: float(scored[column].mean())
        for rate_name, column in BONUS_COLUMNS.items()
        if column in scored.columns
    }

    return PlayerProfile(
        player_id=player_id,
        player_name=name,
        team=str(entry.get("team", "")),
        position=str(entry.get("position", "")),
        games=int(entry.get("expected_games", 70)),
        minutes_per_game=minutes,
        mean_fp=float(np.mean(sample)),
        median_fp=float(np.median(sample)),
        std_fp=float(np.std(sample, ddof=1)),
        percentiles={p: float(np.percentile(sample, p)) for p in percentiles},
        threshold_probabilities={t: float(np.mean(sample >= t)) for t in thresholds},
        bonus_rates=bonus_rates,
        expected_bonus_points=float(scored["bonus_points"].mean()),
        per_game_stats=dict(projected_stats),
        fp_sample=sample,
        is_synthetic=False,
    )


def rookie_availability(entry: Mapping, season_length: int = 82) -> float:
    """Expected games for a rookie, as a fraction of the season.

    Rookies miss time for reasons veterans do not - G-League assignments, DNP-CDs,
    minutes restrictions and load management on non-contending teams - so the
    default sits below a healthy veteran's baseline even with no injury history.
    """
    if "expected_games" in entry:
        return float(entry["expected_games"]) / season_length
    confidence = _confidence_of(entry)
    return {"high": 0.85, "medium": 0.78, "low": 0.70}[confidence]
