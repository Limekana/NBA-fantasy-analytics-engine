"""Contract tests against the real nba_api package schema.

The ingestion adapters were written against documented endpoint contracts but,
in the build environment, could never be executed - stats.nba.com was blocked.
These tests close most of that gap without any network call: `nba_api` declares
the columns each endpoint returns, so we can verify our renames refer to columns
that actually exist, and catch upstream API drift the moment it lands.

Skipped when nba_api is not installed, since it is deliberately optional.
"""
from __future__ import annotations

import inspect

import pytest

nba_api = pytest.importorskip("nba_api", reason="nba_api is an optional dependency")

from src.ingestion.nba_api_source import NBAApiSource, NBAScheduleSource  # noqa: E402

# Columns NBAApiSource.fetch_game_logs renames. If upstream drops or renames one,
# the adapter silently produces a frame missing that stat - so assert loudly.
GAME_LOG_SOURCE_COLUMNS = [
    "PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "GAME_ID", "GAME_DATE",
    "MATCHUP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "TOV", "PF", "FTM",
    "FG3M", "FGA", "FTA", "FG3A", "OREB", "DREB",
]

SCHEDULE_SOURCE_COLUMNS = [
    "gameId", "gameDateEst", "homeTeam_teamTricode", "awayTeam_teamTricode",
]


def test_player_game_logs_provides_every_column_we_rename():
    from nba_api.stats.endpoints import playergamelogs

    available = set(next(iter(playergamelogs.PlayerGameLogs.expected_data.values())))
    missing = [c for c in GAME_LOG_SOURCE_COLUMNS if c not in available]
    assert not missing, f"nba_api no longer returns: {missing}"


def test_player_game_logs_accepts_the_arguments_we_pass():
    from nba_api.stats.endpoints import playergamelogs

    params = set(inspect.signature(playergamelogs.PlayerGameLogs.__init__).parameters)
    for argument in ("season_nullable", "season_type_nullable", "timeout"):
        assert argument in params, f"PlayerGameLogs no longer accepts {argument}"


def test_schedule_endpoint_provides_every_column_we_rename():
    from nba_api.stats.endpoints import scheduleleaguev2

    datasets = scheduleleaguev2.ScheduleLeagueV2.expected_data
    # The adapter takes get_data_frames()[0], which is the games dataset.
    first = next(iter(datasets.values()))
    missing = [c for c in SCHEDULE_SOURCE_COLUMNS if c not in set(first)]
    assert not missing, f"ScheduleLeagueV2 no longer returns: {missing}"


def test_schedule_endpoint_accepts_the_arguments_we_pass():
    from nba_api.stats.endpoints import scheduleleaguev2

    params = set(inspect.signature(scheduleleaguev2.ScheduleLeagueV2.__init__).parameters)
    for argument in ("season", "timeout"):
        assert argument in params


def test_common_all_players_accepts_the_arguments_we_pass():
    from nba_api.stats.endpoints import commonallplayers

    params = set(inspect.signature(commonallplayers.CommonAllPlayers.__init__).parameters)
    for argument in ("is_only_current_season", "timeout"):
        assert argument in params


def test_adapter_package_check_passes_when_installed():
    assert NBAApiSource()._require_package() is not None


def test_schedule_source_rejects_game_log_requests():
    """It provides schedules; asking it for game logs must fail loudly, not
    return something plausible-looking and wrong."""
    with pytest.raises(NotImplementedError):
        NBAScheduleSource().fetch_game_logs("2025-26")
