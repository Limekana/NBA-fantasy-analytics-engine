"""Baseline 2026-27 projection.

Handoff sec.6: *"Do not immediately build a neural network. Start with a
transparent weighted projection... The exact weights should be empirically tested
rather than assumed."*

So this is deliberately a simple, inspectable model:

    per-36 rates, blended across seasons with recency weights
      -> shrunk toward the positional mean for small samples
      -> multiplied by an age-curve factor
      -> scaled by projected minutes
      -> passed through the REAL scoring engine

Every step is a config value in ``config/model.yaml`` and every step is
reversible, so ``src/backtest.py`` can score alternative weightings rather than
anyone arguing about them. Projected *rates* are then converted to fantasy points
by the same engine that scores real games - never by a separate approximation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.projections.games_played import _interpolate_curve
from src.schemas import BOX_SCORE_FIELDS

RATE_STATS = list(BOX_SCORE_FIELDS)


@dataclass
class PlayerProjection:
    """Projected 2026-27 per-game line, with its assumptions attached."""

    player_id: str
    player_name: str
    team: str
    position: str
    projected_minutes: float
    projected_stats: Mapping[str, float]
    age: float
    role_uncertainty: float
    assumption_notes: str = ""
    seasons_used: tuple[str, ...] = ()
    is_synthetic: bool = False

    def to_row(self) -> dict:
        row = {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team": self.team,
            "position": self.position,
            "age": self.age,
            "projected_minutes": round(self.projected_minutes, 1),
            "role_uncertainty": round(self.role_uncertainty, 3),
            "seasons_used": "|".join(self.seasons_used),
            "assumption_notes": self.assumption_notes,
        }
        row.update({f"proj_{k}": round(v, 2) for k, v in self.projected_stats.items()})
        return row


def compute_per36(logs: pd.DataFrame) -> pd.DataFrame:
    """Per-36-minute rates per player per season.

    Per-36 is the right unit because it separates *role* (minutes, which we
    project explicitly) from *productivity* (rates, which are the stable skill
    signal). Mixing the two is how naive projections get burned by minute changes.
    """
    if logs.empty:
        return pd.DataFrame()

    grouped = logs.groupby(["player_id", "season"], sort=False)
    totals = grouped[RATE_STATS + ["minutes"]].sum()
    counts = grouped.size().rename("games")
    frame = totals.join(counts).reset_index()

    minutes = frame["minutes"].replace(0, np.nan)
    for stat in RATE_STATS:
        frame[f"{stat}_per36"] = (frame[stat] / minutes * 36.0).fillna(0.0)
    frame["minutes_per_game"] = (frame["minutes"] / frame["games"]).fillna(0.0)
    return frame


def project_players(
    per36: pd.DataFrame,
    players: pd.DataFrame,
    model_cfg: Mapping,
    assumptions: Mapping,
) -> list[PlayerProjection]:
    """Produce a projected per-game line for every player."""
    if per36.empty:
        return []

    proj_cfg = model_cfg.get("projection", {})
    season_weights = {str(k): float(v) for k, v in proj_cfg.get("season_weights", {}).items()}
    shrinkage_k = float(proj_cfg.get("shrinkage_games_k", 20))
    age_curve = proj_cfg.get("age_curve", {})
    minutes_cfg = proj_cfg.get("minutes", {})
    prior_weight = float(minutes_cfg.get("prior_weight", 0.7))
    role_weight = float(minutes_cfg.get("role_weight", 0.3))
    max_minutes = float(minutes_cfg.get("max_minutes", 38.0))
    min_minutes = float(minutes_cfg.get("min_minutes", 8.0))

    role_minutes = assumptions.get("role_minutes") or {}
    injuries = assumptions.get("injuries") or {}
    role_uncertainty_overrides = assumptions.get("role_uncertainty") or {}
    excluded = set(assumptions.get("exclude") or [])

    meta = players.set_index("player_id") if "player_id" in players.columns else pd.DataFrame()

    # Positional means, used as the shrinkage target for small samples.
    positional_means = _positional_means(per36, meta)

    projections: list[PlayerProjection] = []
    for player_id, group in per36.groupby("player_id", sort=False):
        info = meta.loc[player_id] if player_id in meta.index else None
        name = str(info["player_name"]) if info is not None and "player_name" in info else str(player_id)
        if name in excluded:
            continue

        position = str(info["position"]) if info is not None and "position" in info else ""
        age = float(info["age"]) if info is not None and "age" in info and pd.notna(info["age"]) else 27.0
        # Age forward one year: history is last season, we project the next one.
        projected_age = age + 1.0

        available = {str(row["season"]): row for _, row in group.iterrows()}
        weights = {s: w for s, w in season_weights.items() if s in available}
        if not weights:
            # Season labels did not match config; fall back to using everything.
            weights = {s: 1.0 for s in available}
        total_weight = sum(weights.values()) or 1.0

        notes: list[str] = []
        blended: dict[str, float] = {}
        total_games = sum(float(available[s]["games"]) for s in weights)
        for stat in RATE_STATS:
            value = sum(
                (w / total_weight) * float(available[s][f"{stat}_per36"]) for s, w in weights.items()
            )
            # Shrink small samples toward the positional mean.
            target = positional_means.get(position, {}).get(stat, value)
            value = (total_games * value + shrinkage_k * target) / (total_games + shrinkage_k)
            blended[stat] = value

        if total_games < shrinkage_k:
            notes.append(f"ASSUMPTION: only {int(total_games)} games of history; shrunk toward {position or 'league'} mean")

        age_factor = _interpolate_curve(age_curve, projected_age)
        blended = {k: v * age_factor for k, v in blended.items()}
        if abs(age_factor - 1.0) > 0.005:
            notes.append(f"ASSUMPTION: age {projected_age:.0f} curve factor {age_factor:.3f}")

        # --- minutes ---
        historical_minutes = sum(
            (w / total_weight) * float(available[s]["minutes_per_game"]) for s, w in weights.items()
        )
        role_entry = role_minutes.get(name)
        if role_entry and role_entry.get("minutes") is not None:
            role_target = float(role_entry["minutes"])
            projected_minutes = prior_weight * historical_minutes + role_weight * role_target
            note = role_entry.get("note", "")
            notes.append(f"ASSUMPTION: role minutes {role_target} ({note})" if note else f"ASSUMPTION: role minutes {role_target}")
        else:
            projected_minutes = historical_minutes

        injury_entry = injuries.get(name)
        if injury_entry and injury_entry.get("ramp_minutes_penalty"):
            penalty = float(injury_entry["ramp_minutes_penalty"])
            projected_minutes -= penalty
            notes.append(f"ASSUMPTION: -{penalty} min injury ramp")

        projected_minutes = float(np.clip(projected_minutes, min_minutes, max_minutes))

        projected_stats = {stat: value * projected_minutes / 36.0 for stat, value in blended.items()}

        # Role uncertainty: explicit override, else experience-based default.
        if name in role_uncertainty_overrides:
            uncertainty = float(role_uncertainty_overrides[name])
        elif total_games < 30:
            uncertainty = 0.55
        else:
            uncertainty = 0.25

        projections.append(
            PlayerProjection(
                player_id=str(player_id),
                player_name=name,
                team=str(info["team"]) if info is not None and "team" in info else "",
                position=position,
                projected_minutes=projected_minutes,
                projected_stats=projected_stats,
                age=projected_age,
                role_uncertainty=uncertainty,
                assumption_notes="; ".join(notes),
                seasons_used=tuple(sorted(weights)),
                is_synthetic=bool(info["is_synthetic"]) if info is not None and "is_synthetic" in info else False,
            )
        )
    return projections


def _positional_means(per36: pd.DataFrame, meta: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Mean per-36 rate by position, weighted by games, for shrinkage."""
    if per36.empty or meta.empty or "position" not in meta.columns:
        return {}
    joined = per36.merge(
        meta[["position"]].reset_index(), on="player_id", how="left"
    )
    means: dict[str, dict[str, float]] = {}
    for position, group in joined.groupby("position", sort=False):
        means[str(position)] = {
            stat: float(np.average(group[f"{stat}_per36"], weights=group["games"].clip(lower=1)))
            for stat in RATE_STATS
        }
    return means
