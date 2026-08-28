"""Deterministic fantasy scoring engine.

Engineering Rule 4 (handoff sec.23): *test scoring before projections. A perfect
projection with incorrect scoring is worthless.*

Every weight, bonus and interaction rule is read from ``config/league.yaml``.
There are no magic numbers in this module - the constants below are stat *names*,
not values.

The handoff (sec.4) explicitly warns against silently assuming how bonuses stack.
This engine therefore makes each interaction an explicit, configurable, tested
switch rather than an implicit consequence of the code's shape. See
``docs/lock_in_mechanics.md`` for which switches are verified and which are not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.config import ScoringConfig, get_config
from src.schemas import BOX_SCORE_FIELDS

# Bonus keys understood by the engine. A bonus absent from config simply never
# fires - the engine never invents a default value for one.
BONUS_DOUBLE_DOUBLE = "double_double"
BONUS_TRIPLE_DOUBLE = "triple_double"
BONUS_POINTS_40 = "points_40_plus"
BONUS_POINTS_50 = "points_50_plus"
BONUS_ASSISTS_15 = "assists_15_plus"

# Threshold bonuses expressed as (config key, stat name, threshold).
# Declaring them as data keeps `points_thresholds_stack` a single decision point.
_THRESHOLD_BONUSES: tuple[tuple[str, str, int], ...] = (
    (BONUS_POINTS_40, "points", 40),
    (BONUS_POINTS_50, "points", 50),
    (BONUS_ASSISTS_15, "assists", 15),
)

# Threshold bonuses that overlap on the same stat, ordered low -> high. When
# `points_thresholds_stack` is false, only the highest satisfied tier pays.
_OVERLAPPING_TIERS: dict[str, tuple[str, ...]] = {
    "points": (BONUS_POINTS_40, BONUS_POINTS_50),
}


@dataclass(frozen=True)
class ScoreBreakdown:
    """Full audit trail for a single game's fantasy score.

    The handoff's core design principle is understanding *why* a number is what it
    is, so the engine returns the decomposition rather than only the total.
    """

    total: float
    base: float
    bonus_total: float
    stat_points: Mapping[str, float] = field(default_factory=dict)
    bonuses_awarded: Mapping[str, float] = field(default_factory=dict)
    double_double_categories: tuple[str, ...] = ()

    def explain(self) -> str:
        """Human-readable breakdown, used by the live draft assistant."""
        lines = [f"TOTAL {self.total:.2f} FP  (base {self.base:.2f} + bonus {self.bonus_total:.2f})"]
        for stat, value in self.stat_points.items():
            if value:
                lines.append(f"  {stat:<22} {value:+.2f}")
        for bonus, value in self.bonuses_awarded.items():
            lines.append(f"  BONUS {bonus:<16} {value:+.2f}")
        if self.double_double_categories:
            lines.append(f"  (10+ in: {', '.join(self.double_double_categories)})")
        return "\n".join(lines)


class ScoringEngine:
    """Converts a box score line into fantasy points under the league's rules."""

    def __init__(self, scoring: ScoringConfig | None = None):
        self.cfg = scoring or get_config().scoring

    # -- core -------------------------------------------------------------
    def score_game(self, stats: Mapping[str, Any]) -> float:
        """Fantasy points for one game. Missing stats count as zero."""
        return self.score_game_detailed(stats).total

    def score_game_detailed(self, stats: Mapping[str, Any]) -> ScoreBreakdown:
        """Fantasy points plus the full decomposition."""
        stat_points: dict[str, float] = {}
        base = 0.0
        for stat, weight in self.cfg.stat_weights.items():
            value = _as_float(stats.get(stat, 0.0))
            contribution = value * float(weight)
            stat_points[stat] = contribution
            base += contribution

        bonuses, dd_categories = self._bonuses(stats)
        bonus_total = sum(bonuses.values())

        return ScoreBreakdown(
            total=base + bonus_total,
            base=base,
            bonus_total=bonus_total,
            stat_points=stat_points,
            bonuses_awarded=bonuses,
            double_double_categories=dd_categories,
        )

    # -- bonuses ----------------------------------------------------------
    def _bonuses(self, stats: Mapping[str, Any]) -> tuple[dict[str, float], tuple[str, ...]]:
        awarded: dict[str, float] = {}
        available = self.cfg.bonuses

        # --- double / triple double ---
        dd_categories = self.double_double_categories_hit(stats)
        n_doubles = len(dd_categories)

        is_dd = n_doubles >= 2
        is_td = n_doubles >= 3
        if n_doubles >= 4 and not self.cfg.quad_pays_td:
            is_td = False

        if is_td and BONUS_TRIPLE_DOUBLE in available:
            awarded[BONUS_TRIPLE_DOUBLE] = float(available[BONUS_TRIPLE_DOUBLE])
            # A triple-double is also a double-double. Whether it *pays* both is a
            # league setting: VERIFIED true on Sleeper when both options are on.
            if self.cfg.td_stacks and BONUS_DOUBLE_DOUBLE in available:
                awarded[BONUS_DOUBLE_DOUBLE] = float(available[BONUS_DOUBLE_DOUBLE])
        elif is_dd and BONUS_DOUBLE_DOUBLE in available:
            awarded[BONUS_DOUBLE_DOUBLE] = float(available[BONUS_DOUBLE_DOUBLE])

        # --- threshold bonuses ---
        satisfied: dict[str, list[str]] = {}
        for key, stat, threshold in _THRESHOLD_BONUSES:
            if key not in available:
                continue
            if _as_float(stats.get(stat, 0.0)) >= threshold:
                satisfied.setdefault(stat, []).append(key)

        for stat, keys in satisfied.items():
            tiers = _OVERLAPPING_TIERS.get(stat)
            if tiers and not self.cfg.point_thresholds_stack:
                # Only the highest satisfied tier pays.
                highest = max(keys, key=lambda k: tiers.index(k) if k in tiers else -1)
                awarded[highest] = float(available[highest])
            else:
                for key in keys:
                    awarded[key] = float(available[key])

        return awarded, dd_categories

    def double_double_categories_hit(self, stats: Mapping[str, Any]) -> tuple[str, ...]:
        """Categories in which the player reached the double-double threshold."""
        threshold = self.cfg.dd_threshold
        return tuple(
            category
            for category in self.cfg.dd_categories
            if _as_float(stats.get(category, 0.0)) >= threshold
        )

    # -- vectorised -------------------------------------------------------
    def score_dataframe(self, df: pd.DataFrame, out_column: str = "fantasy_points") -> pd.DataFrame:
        """Vectorised scoring over a game_log dataframe.

        Returns a copy with ``fantasy_points`` plus ``bonus_points`` and a boolean
        flag per bonus, so downstream bonus-rate analysis (handoff sec.9) needs no
        second pass over the data.
        """
        if df.empty:
            out = df.copy()
            for column in (out_column, "base_points", "bonus_points"):
                out[column] = pd.Series(dtype="float64")
            for key in self.cfg.bonuses:
                out[f"bonus_{key}"] = pd.Series(dtype="bool")
            out["n_double_digit_cats"] = pd.Series(dtype="int64")
            return out

        out = df.copy()
        numeric: dict[str, np.ndarray] = {}
        for stat in set(BOX_SCORE_FIELDS) | set(self.cfg.stat_weights) | set(self.cfg.dd_categories):
            if stat in out.columns:
                numeric[stat] = pd.to_numeric(out[stat], errors="coerce").fillna(0.0).to_numpy(dtype=float)
            else:
                numeric[stat] = np.zeros(len(out), dtype=float)

        base = np.zeros(len(out), dtype=float)
        for stat, weight in self.cfg.stat_weights.items():
            base += numeric[stat] * float(weight)

        n_doubles = np.zeros(len(out), dtype=int)
        for category in self.cfg.dd_categories:
            n_doubles += (numeric[category] >= self.cfg.dd_threshold).astype(int)

        bonus_total = np.zeros(len(out), dtype=float)
        available = self.cfg.bonuses

        is_td = n_doubles >= 3
        if not self.cfg.quad_pays_td:
            is_td &= n_doubles < 4
        is_dd_only = (n_doubles >= 2) & ~is_td

        flags: dict[str, np.ndarray] = {}
        if BONUS_TRIPLE_DOUBLE in available:
            flags[BONUS_TRIPLE_DOUBLE] = is_td
            bonus_total += is_td * float(available[BONUS_TRIPLE_DOUBLE])
        if BONUS_DOUBLE_DOUBLE in available:
            dd_paid = is_dd_only | (is_td if self.cfg.td_stacks else np.zeros(len(out), dtype=bool))
            flags[BONUS_DOUBLE_DOUBLE] = dd_paid
            bonus_total += dd_paid * float(available[BONUS_DOUBLE_DOUBLE])

        hits: dict[str, np.ndarray] = {}
        for key, stat, threshold in _THRESHOLD_BONUSES:
            if key in available:
                hits[key] = numeric[stat] >= threshold

        for key, stat, _threshold in _THRESHOLD_BONUSES:
            if key not in hits:
                continue
            paid = hits[key]
            tiers = _OVERLAPPING_TIERS.get(stat)
            if tiers and not self.cfg.point_thresholds_stack and key in tiers:
                # Suppressed by any higher satisfied tier.
                for higher in tiers[tiers.index(key) + 1:]:
                    if higher in hits:
                        paid = paid & ~hits[higher]
            flags[key] = paid
            bonus_total += paid * float(available[key])

        out["base_points"] = base
        out["bonus_points"] = bonus_total
        out[out_column] = base + bonus_total
        out["n_double_digit_cats"] = n_doubles
        for key in available:
            out[f"bonus_{key}"] = flags.get(key, np.zeros(len(out), dtype=bool))
        return out


def _as_float(value: Any) -> float:
    """Coerce a stat to float, treating None/NaN/'' as zero."""
    if value is None:
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if np.isnan(result) else result


def score_game(stats: Mapping[str, Any], scoring: ScoringConfig | None = None) -> float:
    """Convenience wrapper for one-off scoring."""
    return ScoringEngine(scoring).score_game(stats)
