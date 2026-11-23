"""Update preseason projections with games already played this season.

Minutes and per-minute production are blended separately: minutes respond fast
(role changes show up within a few games), per-minute rates slowly (early
shooting luck is noise). Weights were chosen on 2024-25 and validated on
2025-26 and the reverse: after 7/14/28 days the blend beat the preseason
projection for rest-of-season FP/game in both seasons (e.g. Spearman
0.787 -> 0.848 and 0.882 -> 0.908 after one week). See docs/assumptions.md.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.projections.baseline import RATE_STATS, PlayerProjection, compute_per36


def blend_in_season(
    projections: Sequence[PlayerProjection],
    current_logs: pd.DataFrame,
    model_cfg: Mapping,
) -> list[PlayerProjection]:
    """Return projections moved toward this season's minutes and per-minute rates.

    Players without current-season games keep their preseason projection; players
    without a preseason projection are not created here (too few games to stand alone).
    """
    cfg = model_cfg.get("in_season", {})
    if current_logs is None or current_logs.empty or not cfg.get("enabled", True):
        return list(projections)
    minutes_k = float(cfg.get("minutes_games_k", 4))
    rate_k = float(cfg.get("rate_games_k", 10))
    minutes_cfg = model_cfg.get("projection", {}).get("minutes", {})
    low, high = float(minutes_cfg.get("min_minutes", 8.0)), float(minutes_cfg.get("max_minutes", 38.0))

    logs = current_logs.copy()
    logs["player_id"] = logs["player_id"].astype(str)
    current = compute_per36(logs).groupby("player_id").agg(
        {**{stat: "sum" for stat in RATE_STATS}, "minutes": "sum", "games": "sum"})
    # Rookie priors carry their own IDs, not NBA IDs; match them by a unique name.
    from src.adp import normalise_name
    by_name = {}
    if "player_name" in logs.columns:
        names = logs.drop_duplicates("player_id").assign(key=lambda f: f.player_name.map(normalise_name))
        unique = names.groupby("key").player_id.agg(list)
        by_name = {key: ids[0] for key, ids in unique.items() if len(ids) == 1}
    projected_ids = {p.player_id for p in projections}

    updated = []
    for projection in projections:
        current_id = projection.player_id
        if current_id not in current.index:
            match = by_name.get(normalise_name(projection.player_name))
            current_id = match if match and match not in projected_ids else None
        if current_id is None or projection.projected_minutes <= 0:
            updated.append(projection)
            continue
        row = current.loc[current_id]
        games, minutes = float(row["games"]), float(row["minutes"])
        if games <= 0 or minutes <= 0:
            updated.append(projection)
            continue
        minutes_weight = games / (games + minutes_k)
        # Rate weight counts minutes actually played, in 36-minute game equivalents.
        rate_weight = minutes / (minutes + rate_k * 36.0)
        projected_minutes = float(np.clip(
            (1 - minutes_weight) * projection.projected_minutes + minutes_weight * minutes / games, low, high))
        stats = {}
        for stat, value in projection.projected_stats.items():
            prior_rate = float(value) / projection.projected_minutes
            current_rate = float(row[stat]) / minutes if stat in row.index else prior_rate
            stats[stat] = ((1 - rate_weight) * prior_rate + rate_weight * current_rate) * projected_minutes
        note = (f"In-season update: {int(games)} games this season "
                f"(minutes weight {minutes_weight:.2f}, rate weight {rate_weight:.2f})")
        updated.append(replace(
            projection, projected_minutes=projected_minutes, projected_stats=stats,
            assumption_notes="; ".join(n for n in (projection.assumption_notes, note) if n)))
    return updated
