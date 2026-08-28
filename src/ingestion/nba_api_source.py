"""stats.nba.com ingestion via the `nba_api` package.

>>> NETWORK REQUIRED - MUST BE RUN FROM YOUR OWN MACHINE <<<

The environment this repo was built in had ``stats.nba.com`` blocked by an egress
policy (HTTP 403 on CONNECT), so this adapter is written against the documented
endpoint contract and unit-tested against recorded fixtures, but has NOT been
executed against the live API. Treat the first real run as a verification step:

    pip install nba_api
    python -m src.cli ingest --season 2025-26 --source nba_api

and check the row count in the printed manifest against a known season total
(roughly 26,000 player-games for a full NBA season).
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

from src.ingestion.base import DataSource
from src.ingestion.csv_source import coerce_types, normalise_columns


class NBAApiSource(DataSource):
    """Player game logs from the public NBA stats endpoints."""

    name = "nba_api"

    def __init__(self, rate_limit_seconds: float = 0.75, timeout: int = 60):
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout

    def _require_package(self) -> Any:
        try:
            import nba_api  # noqa: F401
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "nba_api is not installed. Run `pip install nba_api`.\n"
                "It is intentionally left out of the default requirements because "
                "the rest of the pipeline runs without network access."
            ) from exc
        return nba_api

    def fetch_game_logs(self, season: str) -> pd.DataFrame:
        """Fetch every player-game for a season.

        ``season`` uses the NBA's own format, e.g. "2025-26".
        """
        self._require_package()
        from nba_api.stats.endpoints import playergamelogs

        response = playergamelogs.PlayerGameLogs(
            season_nullable=season,
            season_type_nullable="Regular Season",
            timeout=self.timeout,
        )
        frame = response.get_data_frames()[0]
        time.sleep(self.rate_limit_seconds)

        renamed = frame.rename(
            columns={
                "PLAYER_ID": "player_id",
                "PLAYER_NAME": "player_name",
                "TEAM_ABBREVIATION": "team",
                "GAME_ID": "game_id",
                "GAME_DATE": "game_date",
                "MATCHUP": "matchup",
                "MIN": "minutes",
                "PTS": "points",
                "REB": "rebounds",
                "AST": "assists",
                "STL": "steals",
                "BLK": "blocks",
                "TOV": "turnovers",
                "PF": "personal_fouls",
                "FTM": "free_throws_made",
                "FG3M": "three_pointers_made",
                "FGA": "field_goals_attempted",
                "FTA": "free_throws_attempted",
                "FG3A": "three_pointers_attempted",
                "OREB": "offensive_rebounds",
                "DREB": "defensive_rebounds",
            }
        )
        out = coerce_types(normalise_columns(renamed), season)
        # PlayerGameLogs carries no starter flag; boxscoretraditionalv2 per game
        # would, at ~1,300 extra requests per season. Not worth it pre-draft:
        # minutes are a better role proxy and are already present.
        if "started" not in out.columns:
            out["started"] = pd.NA
        return out

    def fetch_players(self) -> pd.DataFrame:
        self._require_package()
        from nba_api.stats.endpoints import commonallplayers

        frame = commonallplayers.CommonAllPlayers(
            is_only_current_season=1, timeout=self.timeout
        ).get_data_frames()[0]
        time.sleep(self.rate_limit_seconds)
        return frame.rename(
            columns={
                "PERSON_ID": "player_id",
                "DISPLAY_FIRST_LAST": "player_name",
                "TEAM_ABBREVIATION": "team",
            }
        )


class NBAScheduleSource(DataSource):
    """Season schedule, needed for real games-per-fantasy-week figures.

    Lock-In value is highly sensitive to how many games a team plays each week,
    so replacing the fallback PMF with this is one of the highest-leverage
    upgrades available to the model.
    """

    name = "nba_api_schedule"

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def fetch_game_logs(self, season: str) -> pd.DataFrame:  # pragma: no cover
        raise NotImplementedError("NBAScheduleSource provides schedules, not game logs")

    def fetch_schedule(self, season: str) -> pd.DataFrame:
        try:
            from nba_api.stats.endpoints import scheduleleaguev2
        except ImportError as exc:  # pragma: no cover
            raise ImportError("nba_api is not installed. Run `pip install nba_api`.") from exc

        frame = scheduleleaguev2.ScheduleLeagueV2(season=season, timeout=self.timeout).get_data_frames()[0]
        renamed = frame.rename(
            columns={
                "gameId": "game_id",
                "gameDateEst": "game_date",
                "homeTeam_teamTricode": "home_team",
                "awayTeam_teamTricode": "away_team",
            }
        )
        keep = [c for c in ("game_id", "game_date", "home_team", "away_team") if c in renamed.columns]
        out = renamed[keep].copy()
        out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce")
        out["season"] = season
        return out
