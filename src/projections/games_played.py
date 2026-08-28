"""Games-played model.

Handoff sec.7: *"Games played should be modeled separately from per-game
production... Do not simply use last season's games played."*

That warning matters more than it first appears. Last season's games played is a
notoriously noisy estimator - it mixes a permanent trait (durability) with a pure
shock (a broken hand). Regressing it toward an age-based baseline and then
carrying an explicit uncertainty band is both more honest and more accurate.

In Lock-In the shape of this distribution matters differently than in ordinary
formats: missing a game only costs you if it drops a week's game count, so
availability interacts with the schedule rather than scaling value linearly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class GamesPlayedProjection:
    """Availability projection with explicit uncertainty and provenance."""

    player_id: str
    expected_games: float
    games_floor: float
    games_median: float
    games_ceiling: float
    availability_probability: float
    assumption_notes: str = ""

    def to_row(self) -> dict:
        return {
            "player_id": self.player_id,
            "projected_games": round(self.expected_games, 1),
            "games_floor": round(self.games_floor, 1),
            "games_median": round(self.games_median, 1),
            "games_ceiling": round(self.games_ceiling, 1),
            "availability_probability": round(self.availability_probability, 3),
            "games_assumption_notes": self.assumption_notes,
        }


def _interpolate_curve(curve: Mapping, x: float) -> float:
    """Piecewise-linear lookup over an age -> value mapping."""
    points = sorted((float(k), float(v)) for k, v in curve.items())
    if not points:
        return 1.0
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    return float(np.interp(x, [p[0] for p in points], [p[1] for p in points]))


def project_games_played(
    player_id: str,
    age: float,
    historical_availability: list[float],
    model_cfg: Mapping,
    injury_assumption: Mapping | None = None,
) -> GamesPlayedProjection:
    """Project games played for one player.

    ``historical_availability`` is a list of games-played fractions (games / 82),
    most recent season first. Older seasons are down-weighted.
    """
    cfg = model_cfg.get("games_played", {})
    season_length = int(cfg.get("season_length", 82))
    history_weight = float(cfg.get("history_weight", 0.65))
    concentration = float(cfg.get("beta_concentration", 30.0))
    floor_p = float(cfg.get("floor_percentile", 0.10))
    ceiling_p = float(cfg.get("ceiling_percentile", 0.90))

    age_baseline = _interpolate_curve(cfg.get("baseline_availability", {}), age)

    notes: list[str] = []
    if historical_availability:
        # Recency weights: 1.0, 0.6, 0.36, ...
        weights = np.array([0.6**i for i in range(len(historical_availability))], dtype=float)
        weights /= weights.sum()
        observed = float(np.dot(weights, np.array(historical_availability, dtype=float)))
        # Shrink toward the age baseline: one season of games played is a weak
        # signal about a player's durability.
        availability = history_weight * observed + (1 - history_weight) * age_baseline
    else:
        availability = age_baseline
        notes.append("no availability history; using age baseline")

    if injury_assumption:
        multiplier = float(injury_assumption.get("availability_multiplier", 1.0))
        games_missed = float(injury_assumption.get("games_missed", 0.0))
        availability = availability * multiplier - (games_missed / season_length)
        note = injury_assumption.get("note", "")
        notes.append(f"ASSUMPTION: {note}" if note else "ASSUMPTION: manual injury override")

    availability = float(np.clip(availability, 0.02, 0.99))

    # Beta posterior for the uncertainty band. Concentration controls how tight
    # the band is; a lower value in config/model.yaml means "we trust this less".
    alpha = availability * concentration
    beta = (1 - availability) * concentration
    distribution = stats.beta(max(alpha, 0.1), max(beta, 0.1))

    return GamesPlayedProjection(
        player_id=player_id,
        expected_games=availability * season_length,
        games_floor=float(distribution.ppf(floor_p)) * season_length,
        games_median=float(distribution.ppf(0.5)) * season_length,
        games_ceiling=float(distribution.ppf(ceiling_p)) * season_length,
        availability_probability=availability,
        assumption_notes="; ".join(notes),
    )
