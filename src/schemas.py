"""Canonical data schemas.

Every dataframe crossing a module boundary conforms to one of these. Keeping the
schema in one place is what lets ingestion adapters be swapped without touching
the scoring, projection or draft code.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Box score stat fields -------------------------------------------------
# The scoring engine only ever reads these names. An ingestion adapter's single
# job is to produce them.
BOX_SCORE_FIELDS: tuple[str, ...] = (
    "points",
    "rebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "personal_fouls",
    "free_throws_made",
    "three_pointers_made",
)

# Optional, used for role/projection work when the source provides them.
OPTIONAL_BOX_SCORE_FIELDS: tuple[str, ...] = (
    "field_goals_attempted",
    "free_throws_attempted",
    "three_pointers_attempted",
    "offensive_rebounds",
    "defensive_rebounds",
    "usage_rate",
)

# --- game_log ---------------------------------------------------------------
# One row per player per NBA game. This is RAW FACT and must never be edited in
# place (Engineering Rule 2).
GAME_LOG_SCHEMA: dict[str, str] = {
    "player_id": "string",      # source-native id
    "player_name": "string",
    "season": "string",         # "2025-26"
    "game_id": "string",
    "game_date": "datetime64[ns]",
    "team": "string",           # tricode, e.g. "BOS"
    "opponent": "string",
    "home": "boolean",
    "started": "boolean",
    "minutes": "float64",
    **{f: "float64" for f in BOX_SCORE_FIELDS},
}

# --- players ----------------------------------------------------------------
PLAYER_SCHEMA: dict[str, str] = {
    "player_id": "string",
    "player_name": "string",
    "sleeper_id": "string",
    "team": "string",
    "position": "string",        # primary position: PG/SG/SF/PF/C
    "positions": "string",       # pipe-delimited eligibility, e.g. "PG|SG"
    "age": "float64",
    "injury_status": "string",
    "years_experience": "float64",
}

# --- schedule ---------------------------------------------------------------
SCHEDULE_SCHEMA: dict[str, str] = {
    "game_id": "string",
    "game_date": "datetime64[ns]",
    "season": "string",
    "home_team": "string",
    "away_team": "string",
}

# --- adp --------------------------------------------------------------------
# Handoff sec.14: source and timestamp are part of the record, not metadata.
ADP_SCHEMA: dict[str, str] = {
    "player_name": "string",
    "adp": "float64",
    "source": "string",
    "retrieved_at": "string",
    "league_format": "string",
    "scoring_format": "string",
}


@dataclass(frozen=True)
class SchemaViolation:
    dataset: str
    column: str
    problem: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.dataset}] {self.column}: {self.problem}"


def check_columns(df, schema: dict[str, str], dataset: str, required_only: bool = True):
    """Return a list of SchemaViolation for missing columns.

    Deliberately permissive about EXTRA columns - sources often carry useful
    extras (FGA, usage) and dropping them would lose information.
    """
    violations = []
    for column in schema:
        if column not in df.columns:
            violations.append(SchemaViolation(dataset, column, "missing"))
    if not required_only:
        for column in df.columns:
            if column not in schema:
                violations.append(SchemaViolation(dataset, column, "unexpected"))
    return violations
