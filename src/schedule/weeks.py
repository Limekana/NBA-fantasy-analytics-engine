"""Fantasy week and schedule modelling.

Handoff sec.12. In Lock-In this module is not bookkeeping - it is a valuation
input. A team playing four games in a week gives its players four draws to choose
between; a two-game week gives two. Because only the chosen game counts, the
games-per-week *distribution* matters more than the season game total.
"""
from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


def fantasy_week_start(dates: pd.Series, week_start_weekday: int = 1) -> pd.Series:
    """Map game dates to the start date of their fantasy week (default Mon-Sun)."""
    normalised = pd.to_datetime(dates, utc=True).dt.tz_localize(None).dt.normalize()
    offset = (normalised.dt.dayofweek + 1 - week_start_weekday) % 7
    return normalised - pd.to_timedelta(offset, unit="D")


def games_per_week_by_team(
    schedule: pd.DataFrame, week_start_weekday: int = 1
) -> dict[str, dict[int, float]]:
    """Empirical games-per-week PMF for each team, from a real schedule.

    Input needs ``game_date`` plus either ``home_team``/``away_team`` or ``team``.
    """
    if schedule.empty:
        return {}

    from src.schedule.calendar import weekly_counts
    counts = weekly_counts(schedule, week_start_weekday)
    return {str(team): {int(k): v / len(counts) for k, v in Counter(counts[team]).items()}
            for team in counts}


def fallback_pmf(model_cfg: Mapping) -> dict[int, float]:
    """League-average games-per-week PMF, used when no real schedule exists.

    Flagged as an ASSUMPTION everywhere it is used. Replacing it with the real
    2026-27 schedule is the single highest-leverage data upgrade for this model,
    because it is what differentiates otherwise-identical players.
    """
    raw = model_cfg.get("schedule", {}).get("fallback_games_per_week_pmf", {})
    pmf = {int(k): float(v) for k, v in raw.items()}
    total = sum(pmf.values()) or 1.0
    return {k: v / total for k, v in pmf.items()}


def team_pmf_or_fallback(
    team: str, team_pmfs: Mapping[str, Mapping[int, float]], model_cfg: Mapping
) -> tuple[dict[int, float], bool]:
    """Return (pmf, used_real_schedule) for a team."""
    if team and team in team_pmfs:
        pmf = {int(k): float(v) for k, v in team_pmfs[team].items()}
        total = sum(pmf.values()) or 1.0
        return {k: v / total for k, v in pmf.items()}, True
    return fallback_pmf(model_cfg), False


def expected_games_per_week(pmf: Mapping[int, float]) -> float:
    total = sum(pmf.values()) or 1.0
    return sum(int(g) * (float(p) / total) for g, p in pmf.items())


def playoff_week_pmf(
    schedule: pd.DataFrame,
    team: str,
    start_week: int,
    week_start_weekday: int = 1,
    week_one_start=None,
    end_week: int | None = None,
) -> dict[int, float]:
    """Games-per-week PMF restricted to the fantasy playoff weeks.

    A player whose team has a soft, game-dense playoff schedule is worth more
    than the season-long average suggests - the weeks that decide the league are
    not interchangeable with the weeks that do not.
    """
    if schedule.empty:
        return {}
    from src.schedule.calendar import weekly_counts
    if start_week < 1 or (end_week is not None and end_week < start_week):
        raise ValueError('Invalid playoff week interval')
    counts = weekly_counts(schedule, week_start_weekday, start=week_one_start)
    if team not in counts:
        return {}
    selected = counts[team].iloc[start_week - 1:end_week]
    if selected.empty:
        return {}
    return {int(k): v / len(selected) for k, v in Counter(selected).items()}
