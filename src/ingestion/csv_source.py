"""CSV ingestion adapter.

The escape hatch that makes the pipeline usable regardless of API access: drop
any CSV matching ``docs/schemas.md`` into ``data/raw/<season>/`` and the whole
model runs. Column names are mapped case-insensitively through a synonym table,
so exports from Basketball Reference, Kaggle dumps and nba_api all load without
editing the file (which Rule 2 forbids anyway).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.config import RAW_DIR
from src.ingestion.base import DataSource

# Maps many real-world column spellings onto the canonical schema names.
COLUMN_SYNONYMS: dict[str, str] = {
    # identity
    "player": "player_name", "player_name": "player_name", "name": "player_name",
    "playername": "player_name", "player_id": "player_id", "playerid": "player_id",
    "game_id": "game_id", "gameid": "game_id",
    "game_date": "game_date", "date": "game_date", "gamedate": "game_date",
    "team": "team", "team_abbreviation": "team", "tm": "team",
    "opponent": "opponent", "opp": "opponent", "matchup": "matchup",
    "season": "season", "season_id": "season",
    "started": "started", "gs": "started", "is_starter": "started",
    "home": "home",
    # box score
    "min": "minutes", "minutes": "minutes", "mp": "minutes",
    "pts": "points", "points": "points",
    "reb": "rebounds", "trb": "rebounds", "rebounds": "rebounds",
    "ast": "assists", "assists": "assists",
    "stl": "steals", "steals": "steals",
    "blk": "blocks", "blocks": "blocks",
    "tov": "turnovers", "to": "turnovers", "turnovers": "turnovers",
    "pf": "personal_fouls", "personal_fouls": "personal_fouls", "fouls": "personal_fouls",
    "ftm": "free_throws_made", "ft": "free_throws_made", "free_throws_made": "free_throws_made",
    "fg3m": "three_pointers_made", "3p": "three_pointers_made", "fg3": "three_pointers_made",
    "three_pointers_made": "three_pointers_made", "tpm": "three_pointers_made",
    # optional
    "fga": "field_goals_attempted", "fta": "free_throws_attempted",
    "fg3a": "three_pointers_attempted", "3pa": "three_pointers_attempted",
    "oreb": "offensive_rebounds", "dreb": "defensive_rebounds",
    "usg_pct": "usage_rate", "usg": "usage_rate",
}

# The minimum a file must have to be treated as a game log at all.
REQUIRED_GAME_LOG_COLUMNS = {"player_name", "points", "rebounds", "assists"}

NUMERIC_COLUMNS = (
    "minutes", "points", "rebounds", "assists", "steals", "blocks", "turnovers",
    "personal_fouls", "free_throws_made", "three_pointers_made",
    "field_goals_attempted", "free_throws_attempted", "three_pointers_attempted",
    "offensive_rebounds", "defensive_rebounds", "usage_rate",
)


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename source columns onto the canonical schema, case-insensitively."""
    renames = {}
    for column in df.columns:
        key = str(column).strip().lower().replace(" ", "_")
        if key in COLUMN_SYNONYMS:
            renames[column] = COLUMN_SYNONYMS[key]
    out = df.rename(columns=renames)
    # Deduplicate any collision produced by two synonyms mapping to one name.
    return out.loc[:, ~out.columns.duplicated()]


def coerce_types(df: pd.DataFrame, season: str | None = None) -> pd.DataFrame:
    """Apply schema dtypes and derive anything trivially derivable."""
    out = df.copy()

    if "game_date" in out.columns:
        out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce")

    # "MIN" often arrives as "34:12" rather than a number. Parse it BEFORE the
    # numeric coercion below, which would otherwise turn it into NaN -> 0.
    # (Checked by value, not dtype: pandas >=3 infers `str`, not `object`.)
    if "minutes" in out.columns and not pd.api.types.is_numeric_dtype(out["minutes"]):
        out["minutes"] = out["minutes"].map(_parse_minutes)

    for column in NUMERIC_COLUMNS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0)

    # nba_api ships MATCHUP ("BOS vs. NYK" / "BOS @ NYK") rather than opponent/home.
    if "matchup" in out.columns:
        if "opponent" not in out.columns:
            out["opponent"] = out["matchup"].astype(str).str.split(r"\s+(?:vs\.|@)\s+", regex=True).str[-1]
        if "home" not in out.columns:
            out["home"] = ~out["matchup"].astype(str).str.contains("@")
        out = out.drop(columns=["matchup"])

    if "started" in out.columns:
        out["started"] = out["started"].map(_parse_bool)
    if "home" in out.columns:
        out["home"] = out["home"].map(_parse_bool)

    if season and "season" not in out.columns:
        out["season"] = season

    if "player_id" not in out.columns and "player_name" in out.columns:
        # Deterministic surrogate key so a name-only source still joins.
        out["player_id"] = out["player_name"].astype(str).str.strip().str.lower().str.replace(r"\s+", "_", regex=True)

    if "game_id" not in out.columns and {"game_date", "team"}.issubset(out.columns):
        out["game_id"] = (
            out["game_date"].dt.strftime("%Y%m%d").fillna("unknown") + "_" + out["team"].astype(str)
        )

    return out


def _is_synthetic(path: Path, frame: pd.DataFrame) -> bool:
    """Two independent checks, because either alone can be defeated.

    The filename catches a renamed-but-untouched export; the column catches a
    file that was renamed to look real.
    """
    if path.name.upper().startswith("SYNTHETIC"):
        return True
    if "is_synthetic" in frame.columns:
        flags = frame["is_synthetic"]
        try:
            return bool(flags.astype(str).str.lower().isin(("true", "1")).any())
        except Exception:  # noqa: BLE001
            return bool(flags.any())
    return False


def _parse_minutes(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if ":" in text:
        parts = text.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    return str(value).strip().lower() in ("1", "true", "t", "yes", "y", "started")


class CSVSource(DataSource):
    """Loads game logs from any CSV/parquet already on disk.

    Refuses synthetic data by default. A `demo` run used to write its generated
    seasons into data/raw/ alongside real game logs, and because every CSV in a
    season directory is loaded, fake players ended up competing for slots on a
    real draft board. Two independent checks now prevent that: the filename
    prefix and an `is_synthetic` column in the data itself.
    """

    name = "csv"

    def __init__(self, root: Path | str | None = None, allow_synthetic: bool = False):
        self.root = Path(root) if root else RAW_DIR
        self.allow_synthetic = allow_synthetic

    def fetch_game_logs(self, season: str) -> pd.DataFrame:
        directory = self.root / season
        if not directory.exists():
            raise FileNotFoundError(
                f"No raw data directory for season {season}: {directory}\n"
                f"Place a CSV matching docs/schemas.md there, or run "
                f"`python -m src.cli ingest --season {season} --source nba_api` "
                f"from a machine with network access."
            )
        files = sorted(
            [p for p in directory.iterdir() if p.suffix.lower() in (".csv", ".parquet")]
        )
        if not files:
            raise FileNotFoundError(f"No .csv/.parquet files found in {directory}")

        frames, skipped, synthetic_skipped = [], [], []
        for path in files:
            raw = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
            normalised = normalise_columns(raw)
            # A season directory often ends up holding other CSVs too - player
            # metadata, a schedule, an ADP export. Loading one of those as a game
            # log produces statless rows that poison every downstream aggregate
            # (and, because they become NaN, can silently break tiering rather
            # than failing loudly). So require real game-log columns.
            missing = REQUIRED_GAME_LOG_COLUMNS - set(normalised.columns)
            if missing:
                skipped.append((path.name, sorted(missing)))
                continue

            if not self.allow_synthetic and _is_synthetic(path, normalised):
                synthetic_skipped.append(path.name)
                continue

            frames.append(coerce_types(normalised, season))

        if not frames:
            detail = "; ".join(f"{name} (missing {cols})" for name, cols in skipped)
            raise FileNotFoundError(
                f"No usable game-log files in {directory}. Skipped: {detail or 'none'}.\n"
                "A game log needs at least a player column, a date column and the "
                "box-score stats. See `python -m src.cli data-help`."
            )
        if synthetic_skipped:
            print(f"  !! IGNORED {len(synthetic_skipped)} SYNTHETIC file(s) in "
                  f"{directory.name}: {', '.join(synthetic_skipped)}")
            print("     These are generated demo data, not real players. Delete them:")
            print(f"       Remove-Item '{directory}\\SYNTHETIC_*'")
        if skipped:
            names = ", ".join(name for name, _ in skipped)
            print(f"  note: ignored {len(skipped)} non-game-log file(s) in {directory.name}: {names}")

        combined = pd.concat(frames, ignore_index=True)
        if {"player_id", "game_id"}.issubset(combined.columns):
            combined = combined.drop_duplicates(subset=["player_id", "game_id"], keep="first")
        return combined
