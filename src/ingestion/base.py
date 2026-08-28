"""Ingestion contract.

Engineering Rule 2 (handoff sec.23): *keep raw data immutable.* Adapters write to
``data/raw/`` once and nothing in the pipeline ever edits those files in place -
all cleaning happens on the way into ``data/processed/``.

Engineering Rule 6: *track timestamps.* Every write is accompanied by a sidecar
manifest recording the source, the retrieval time and the row count, so a stale
download can never be mistaken for a fresh one three days before a draft.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.schemas import GAME_LOG_SCHEMA, check_columns


@dataclass
class IngestionManifest:
    """Provenance record written alongside every raw file."""

    source: str
    dataset: str
    season: str | None
    retrieved_at: str
    row_count: int
    columns: list[str]
    notes: str = ""

    @staticmethod
    def now(source: str, dataset: str, df: pd.DataFrame, season: str | None = None, notes: str = "") -> "IngestionManifest":
        return IngestionManifest(
            source=source,
            dataset=dataset,
            season=season,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            row_count=len(df),
            columns=list(df.columns),
            notes=notes,
        )


class DataSource(ABC):
    """Base class for every ingestion adapter."""

    name: str = "abstract"

    @abstractmethod
    def fetch_game_logs(self, season: str) -> pd.DataFrame:
        """Return a dataframe conforming to schemas.GAME_LOG_SCHEMA."""

    def fetch_players(self) -> pd.DataFrame:  # pragma: no cover - optional
        raise NotImplementedError(f"{self.name} does not provide player metadata")

    def fetch_schedule(self, season: str) -> pd.DataFrame:  # pragma: no cover - optional
        raise NotImplementedError(f"{self.name} does not provide a schedule")


def write_raw(df: pd.DataFrame, path: Path, source: str, dataset: str, season: str | None = None, notes: str = "") -> Path:
    """Write a raw dataset plus its provenance manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False) if path.suffix == ".parquet" else df.to_csv(path, index=False)
    manifest = IngestionManifest.now(source, dataset, df, season, notes)
    path.with_suffix(path.suffix + ".manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2), encoding="utf-8"
    )
    return path


def validate_game_logs(df: pd.DataFrame) -> list[str]:
    """Check a game log frame against the schema and basic sanity rules."""
    problems = [str(v) for v in check_columns(df, GAME_LOG_SCHEMA, "game_log")]
    if df.empty:
        problems.append("[game_log] frame is empty")
        return problems

    if "minutes" in df.columns and (df["minutes"] < 0).any():
        problems.append("[game_log] negative minutes present")

    for column in ("points", "rebounds", "assists", "steals", "blocks"):
        if column in df.columns and (df[column] < 0).any():
            problems.append(f"[game_log] negative {column} present")

    if {"player_id", "game_id"}.issubset(df.columns):
        duplicates = df.duplicated(subset=["player_id", "game_id"]).sum()
        if duplicates:
            problems.append(f"[game_log] {duplicates} duplicate player/game rows")

    return problems
