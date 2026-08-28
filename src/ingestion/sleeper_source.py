"""Sleeper player metadata and league settings verification.

>>> NETWORK REQUIRED <<< ``api.sleeper.app`` was egress-blocked in the build
environment. The endpoints used here are public and unauthenticated.

Two jobs:

1.  Canonical player identity (Sleeper's own ``player_id``, positions, age,
    injury status). Sleeper's ids are the join key for anything league-specific.
2.  **Verifying that config/league.yaml actually matches the league.** The
    handoff warns that the provisional settings may not be final; once you set
    ``meta.sleeper_league_id`` this can diff the real settings against the YAML,
    which is far safer than trusting a transcription.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

import pandas as pd

from src.ingestion.base import DataSource

PLAYERS_URL = "https://api.sleeper.app/v1/players/nba"
LEAGUE_URL = "https://api.sleeper.app/v1/league/{league_id}"

# Sleeper's NBA scoring keys -> our config/league.yaml names.
SLEEPER_SCORING_KEYS: dict[str, str] = {
    "pts": "points",
    "reb": "rebounds",
    "ast": "assists",
    "stl": "steals",
    "blk": "blocks",
    "tov": "turnovers",
    "pf": "personal_fouls",
    "ftm": "free_throws_made",
    "fgm3": "three_pointers_made",
    "dd": "double_double",
    "td": "triple_double",
}


def _get_json(url: str, timeout: int = 60) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


class SleeperSource(DataSource):
    name = "sleeper"

    def fetch_game_logs(self, season: str) -> pd.DataFrame:  # pragma: no cover
        raise NotImplementedError("Sleeper provides player metadata, not game logs")

    def fetch_players(self) -> pd.DataFrame:
        """All NBA players known to Sleeper (a large payload, ~5MB)."""
        payload = _get_json(PLAYERS_URL)
        rows = []
        for player_id, record in payload.items():
            positions = record.get("fantasy_positions") or []
            rows.append(
                {
                    "sleeper_id": str(player_id),
                    "player_name": record.get("full_name")
                    or f"{record.get('first_name', '')} {record.get('last_name', '')}".strip(),
                    "team": record.get("team"),
                    "position": (positions[0] if positions else record.get("position")),
                    "positions": "|".join(positions),
                    "age": record.get("age"),
                    "injury_status": record.get("injury_status"),
                    "years_experience": record.get("years_exp"),
                    "status": record.get("status"),
                }
            )
        frame = pd.DataFrame(rows)
        return frame[frame["player_name"].notna() & (frame["player_name"] != "")]

    def fetch_league_settings(self, league_id: str) -> dict:
        """Raw league settings, for verifying config/league.yaml."""
        return _get_json(LEAGUE_URL.format(league_id=league_id))


def diff_scoring(league_payload: dict, configured: dict) -> list[str]:
    """Compare Sleeper's live scoring settings against config/league.yaml.

    Returns human-readable differences. An empty list means the YAML is faithful.
    This is the single highest-value check to run before the draft: every
    valuation in the system is downstream of these numbers.
    """
    live = (league_payload or {}).get("scoring_settings") or {}
    differences: list[str] = []

    for sleeper_key, our_key in SLEEPER_SCORING_KEYS.items():
        if sleeper_key not in live:
            continue
        live_value = float(live[sleeper_key])
        our_value = configured.get(our_key)
        if our_value is None:
            differences.append(
                f"Sleeper has {sleeper_key}={live_value} but config/league.yaml has no {our_key}"
            )
        elif abs(float(our_value) - live_value) > 1e-9:
            differences.append(
                f"{our_key}: config says {our_value}, Sleeper says {live_value}"
            )

    for our_key, our_value in configured.items():
        reverse = {v: k for k, v in SLEEPER_SCORING_KEYS.items()}
        sleeper_key = reverse.get(our_key)
        if sleeper_key and sleeper_key not in live and float(our_value) != 0.0:
            differences.append(
                f"config/league.yaml has {our_key}={our_value} but Sleeper does not score it"
            )

    return differences
