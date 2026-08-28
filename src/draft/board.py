"""Draft board construction: ranking, tiers, scarcity and replacement level.

Handoff sec.16-18. Two ideas do the real work here.

**Tiers are gaps, not buckets.** A tier should mean "these players are
interchangeable"; the useful information is where that stops being true. So tiers
are cut at unusually large drops in projected value rather than every N players.
A cliff between picks 14 and 15 is exactly the thing that should change your
behaviour at pick 13, and fixed-size buckets hide it.

**Replacement level, not raw value, drives positional scarcity.** A centre worth
900 points is only worth drafting early if the centre you could get 30 picks
later is much worse. With two UTIL slots and multi-position eligibility, this
league's scarcity is genuinely mild - the handoff's warning not to overvalue
position is correct, and this module quantifies rather than assumes it.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd

VALUE_COLUMN = "projected_season_value"


def assign_tiers(board: pd.DataFrame, model_cfg: Mapping, value_column: str = VALUE_COLUMN) -> pd.DataFrame:
    """Cut tiers where the value drop is unusually large.

    A new tier starts when the gap to the next player exceeds
    ``gap_sd_multiple`` standard deviations of the local gap distribution, so the
    cut points come from the shape of this year's player pool rather than a
    number someone picked in advance.
    """
    if board.empty:
        out = board.copy()
        out["tier"] = pd.Series(dtype="object")
        return out

    tier_cfg = model_cfg.get("tiers", {})
    gap_multiple = float(tier_cfg.get("gap_sd_multiple", 1.0))
    min_size = int(tier_cfg.get("min_tier_size", 3))
    max_tiers = int(tier_cfg.get("max_tiers", 12))

    out = board.sort_values(value_column, ascending=False).reset_index(drop=True)
    values = out[value_column].to_numpy(dtype=float)
    if len(values) < 2:
        out["tier"] = "A"
        return out

    gaps = -np.diff(values)                      # positive: value falls as rank grows
    threshold = float(np.mean(gaps) + gap_multiple * np.std(gaps))

    tiers: list[int] = []
    current, size = 0, 0
    for index in range(len(values)):
        tiers.append(current)
        size += 1
        if index < len(gaps) and gaps[index] > threshold and size >= min_size and current + 1 < max_tiers:
            current += 1
            size = 0

    out["tier"] = [_tier_label(t) for t in tiers]
    return out


def _tier_label(index: int) -> str:
    """0 -> A, 1 -> B, ... 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def replacement_levels(
    board: pd.DataFrame, league_cfg, value_column: str = VALUE_COLUMN
) -> dict[str, float]:
    """Value of the last startable player at each position.

    The replacement baseline is the number of players at a position that the
    league as a whole will start, which is what determines whether waiting is
    painful. UTIL slots are apportioned across positions rather than assigned to
    one, since anybody can fill them.
    """
    if board.empty or "position" not in board.columns:
        return {}

    teams = league_cfg.teams
    starting = league_cfg.starting_slots
    eligibility = league_cfg.position_eligibility

    # Dedicated slots each position can claim.
    demand: dict[str, float] = {}
    for position, slots in eligibility.items():
        dedicated = sum(int(starting.get(slot, 0)) for slot in slots if slot not in ("UTIL",))
        # Flex slots (G/F) are shared by two positions; halve them.
        shared = 0.0
        for slot in slots:
            if slot in ("G", "F"):
                shared += int(starting.get(slot, 0)) * 0.5
        exact = sum(int(starting.get(slot, 0)) for slot in slots if slot in (position,))
        demand[position] = exact + shared

    util_slots = int(starting.get("UTIL", 0))
    if util_slots and demand:
        # UTIL goes to whoever is most plentiful; spread evenly as a neutral prior.
        share = util_slots / len(demand)
        for position in demand:
            demand[position] += share

    levels: dict[str, float] = {}
    for position, per_team in demand.items():
        pool = board[board["position"] == position].sort_values(value_column, ascending=False)
        if pool.empty:
            continue
        index = max(int(round(per_team * teams)) - 1, 0)
        index = min(index, len(pool) - 1)
        levels[position] = float(pool.iloc[index][value_column])
    return levels


def add_value_over_replacement(
    board: pd.DataFrame, league_cfg, value_column: str = VALUE_COLUMN
) -> pd.DataFrame:
    """Add VOR and a positional scarcity measure."""
    out = board.copy()
    levels = replacement_levels(out, league_cfg, value_column)
    if not levels:
        out["replacement_value"] = np.nan
        out["vor"] = np.nan
        return out

    out["replacement_value"] = out["position"].map(levels)
    out["vor"] = out[value_column] - out["replacement_value"]

    # Scarcity: how far above the league-wide replacement baseline this
    # position's own baseline sits. High == the position runs dry sooner.
    overall = float(np.mean(list(levels.values())))
    scarcity = {position: (overall - level) / overall if overall else 0.0 for position, level in levels.items()}
    out["positional_scarcity"] = out["position"].map(scarcity)
    return out


def build_draft_board(
    valuations_frame: pd.DataFrame,
    league_cfg,
    model_cfg: Mapping,
    consensus: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Assemble the final draft board."""
    from src.adp import join_adp

    if valuations_frame.empty:
        return valuations_frame

    board = valuations_frame.sort_values(VALUE_COLUMN, ascending=False).reset_index(drop=True)
    board["model_rank"] = np.arange(1, len(board) + 1)
    board = assign_tiers(board, model_cfg)
    board["model_rank"] = np.arange(1, len(board) + 1)   # re-index after tier sort
    board = add_value_over_replacement(board, league_cfg)

    if consensus is not None:
        board = join_adp(board, consensus)
    else:
        for column in ("adp", "adp_sources", "adp_spread", "adp_vs_model"):
            board[column] = np.nan
        board["value_flag"] = "no_adp"

    # Column order: the handoff's required fields first (sec.26), extras after.
    preferred = [
        "model_rank", "player_name", "team", "position", "tier",
        "projected_fp_game", "projected_games", "projected_season_value",
        "median_fp", "floor", "ceiling", "std_dev",
        "double_double_rate", "triple_double_rate", "40_point_rate",
        "50_point_rate", "15_assist_rate",
        "lockin_value", "lock_in_advantage", "games_per_week",
        "adp", "adp_vs_model", "value_flag", "risk", "vor", "positional_scarcity",
        "archetype",
    ]
    ordered = [c for c in preferred if c in board.columns]
    remaining = [c for c in board.columns if c not in ordered]
    return board[ordered + remaining]
