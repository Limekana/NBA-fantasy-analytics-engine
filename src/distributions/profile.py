"""Player distribution profiles.

Handoff sec.3: *do NOT build a single "Player X = 42.7 fantasy points" ranking.*
Handoff sec.8-9: percentiles, threshold probabilities, and bonus rates.

The output of this module is the factual half of a player's record: everything
here is measured from game logs, not projected. Keeping it separate from
``src/projections`` is what makes Rule 7 (separate facts from assumptions)
enforceable rather than aspirational.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.scoring import ScoringEngine

BONUS_COLUMNS = {
    "double_double_rate": "bonus_double_double",
    "triple_double_rate": "bonus_triple_double",
    "40_point_rate": "bonus_points_40_plus",
    "50_point_rate": "bonus_points_50_plus",
    "15_assist_rate": "bonus_assists_15_plus",
}


@dataclass
class PlayerProfile:
    """Everything measured about one player, with no projection applied."""

    player_id: str
    player_name: str
    team: str
    position: str
    games: int
    minutes_per_game: float
    mean_fp: float
    median_fp: float
    std_fp: float
    percentiles: Mapping[int, float] = field(default_factory=dict)
    threshold_probabilities: Mapping[int, float] = field(default_factory=dict)
    bonus_rates: Mapping[str, float] = field(default_factory=dict)
    expected_bonus_points: float = 0.0
    per_game_stats: Mapping[str, float] = field(default_factory=dict)
    fp_sample: np.ndarray = field(default_factory=lambda: np.array([]))
    is_synthetic: bool = False

    @property
    def floor(self) -> float:
        """10th percentile - a bad-but-not-catastrophic night."""
        return self.percentiles.get(10, self.mean_fp)

    @property
    def ceiling(self) -> float:
        """90th percentile - a realistic best night, not the season maximum."""
        return self.percentiles.get(90, self.mean_fp)

    @property
    def coefficient_of_variation(self) -> float:
        return self.std_fp / self.mean_fp if self.mean_fp > 0 else 0.0

    def to_row(self) -> dict:
        row = {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team": self.team,
            "position": self.position,
            "games": self.games,
            "minutes_per_game": round(self.minutes_per_game, 2),
            "mean_fp": round(self.mean_fp, 2),
            "median_fp": round(self.median_fp, 2),
            "std_fp": round(self.std_fp, 2),
            "floor": round(self.floor, 2),
            "ceiling": round(self.ceiling, 2),
            "cv": round(self.coefficient_of_variation, 3),
            "expected_bonus_points": round(self.expected_bonus_points, 2),
            "is_synthetic": self.is_synthetic,
        }
        row.update({f"p{p}": round(v, 2) for p, v in self.percentiles.items()})
        row.update({f"p_fp_ge_{t}": round(v, 4) for t, v in self.threshold_probabilities.items()})
        row.update({k: round(v, 4) for k, v in self.bonus_rates.items()})
        row.update({f"{k}_pg": round(v, 2) for k, v in self.per_game_stats.items()})
        return row


def build_profiles(
    scored_logs: pd.DataFrame,
    model_cfg: Mapping,
    engine: ScoringEngine | None = None,
) -> list[PlayerProfile]:
    """Build a PlayerProfile for every player in a scored game log frame."""
    if scored_logs.empty:
        return []

    dist_cfg = model_cfg.get("distributions", {})
    percentiles = list(dist_cfg.get("percentiles", [10, 25, 50, 75, 90, 95]))
    thresholds = list(dist_cfg.get("threshold_probabilities", [30, 40, 50, 60]))

    stat_columns = [
        c
        for c in (
            "points", "rebounds", "assists", "steals", "blocks", "turnovers",
            "personal_fouls", "free_throws_made", "three_pointers_made",
        )
        if c in scored_logs.columns
    ]

    profiles: list[PlayerProfile] = []
    for player_id, group in scored_logs.groupby("player_id", sort=False):
        sample = group["fantasy_points"].to_numpy(dtype=float)
        if sample.size == 0:
            continue

        bonus_rates = {
            name: float(group[column].mean())
            for name, column in BONUS_COLUMNS.items()
            if column in group.columns
        }

        profiles.append(
            PlayerProfile(
                player_id=str(player_id),
                player_name=str(group["player_name"].iloc[0]) if "player_name" in group else str(player_id),
                team=str(group["team"].iloc[0]) if "team" in group else "",
                position=str(group["position"].iloc[0]) if "position" in group else "",
                games=int(sample.size),
                minutes_per_game=float(group["minutes"].mean()) if "minutes" in group else 0.0,
                mean_fp=float(np.mean(sample)),
                median_fp=float(np.median(sample)),
                std_fp=float(np.std(sample, ddof=1)) if sample.size > 1 else 0.0,
                percentiles={p: float(np.percentile(sample, p)) for p in percentiles},
                threshold_probabilities={t: float(np.mean(sample >= t)) for t in thresholds},
                bonus_rates=bonus_rates,
                expected_bonus_points=float(group["bonus_points"].mean()) if "bonus_points" in group else 0.0,
                per_game_stats={c: float(group[c].mean()) for c in stat_columns},
                fp_sample=sample,
                is_synthetic=bool(group["is_synthetic"].iloc[0]) if "is_synthetic" in group else False,
            )
        )
    return profiles


def profiles_to_frame(profiles: Sequence[PlayerProfile]) -> pd.DataFrame:
    if not profiles:
        return pd.DataFrame()
    return pd.DataFrame([p.to_row() for p in profiles])


def assign_archetypes(frame: pd.DataFrame, n_clusters: int = 7, seed: int = 42) -> pd.DataFrame:
    """Discover player archetypes from the data rather than defining them.

    Handoff sec.10: *"Do this after calculating the data, rather than manually
    defining which archetypes are good."* So this clusters on standardised
    per-game rates and distribution shape, then LABELS each cluster by whichever
    feature it is most extreme on. The labels are descriptive output, not inputs -
    no cluster is privileged or assumed valuable.

    Uses k-means from scipy so the project keeps its dependency footprint small
    (handoff sec.22); scikit-learn is not required for this.
    """
    from scipy.cluster.vq import kmeans2, whiten

    if frame.empty or len(frame) < n_clusters:
        out = frame.copy()
        out["archetype"] = "unclassified"
        return out

    feature_columns = [
        c
        for c in (
            "points_pg", "rebounds_pg", "assists_pg", "steals_pg", "blocks_pg",
            "turnovers_pg", "three_pointers_made_pg", "cv",
            "double_double_rate", "triple_double_rate", "40_point_rate",
        )
        if c in frame.columns
    ]
    if len(feature_columns) < 3:
        out = frame.copy()
        out["archetype"] = "unclassified"
        return out

    matrix = frame[feature_columns].fillna(0.0).to_numpy(dtype=float)
    # Standardise so a 30-point scale does not dominate a 1.5-steal scale.
    standardised = (matrix - matrix.mean(axis=0)) / np.where(matrix.std(axis=0) == 0, 1.0, matrix.std(axis=0))

    centroids, labels = kmeans2(standardised, n_clusters, minit="++", seed=seed)

    # Label each cluster by the feature on which its centroid is most extreme.
    readable = {
        "points_pg": "scoring_volume",
        "rebounds_pg": "rebounding",
        "assists_pg": "playmaking",
        "steals_pg": "defensive_steals",
        "blocks_pg": "defensive_blocks",
        "turnovers_pg": "high_turnover",
        "three_pointers_made_pg": "three_point_volume",
        "cv": "high_variance",
        "double_double_rate": "double_double_machine",
        "triple_double_rate": "triple_double_threat",
        "40_point_rate": "scoring_ceiling",
    }
    names: dict[int, str] = {}
    used: set[str] = set()
    for cluster in range(len(centroids)):
        order = np.argsort(-np.abs(centroids[cluster]))
        label = "balanced"
        for index in order:
            feature = feature_columns[index]
            candidate = readable.get(feature, feature)
            if centroids[cluster][index] < 0:
                candidate = f"low_{candidate}"
            if candidate not in used:
                label = candidate
                break
        used.add(label)
        names[cluster] = label

    out = frame.copy()
    out["archetype"] = [names.get(int(l), "unclassified") for l in labels]
    return out
