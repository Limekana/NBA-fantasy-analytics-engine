"""ADP ingestion and consensus.

Handoff sec.14: *"Do not use one ranking source as truth. Store source,
timestamp, player, ADP, league format, scoring format. Keep ADP separate from
expert rankings. They are different pieces of information."*

That separation is the whole point of this module. ADP is a *market price* - a
measurement of what other drafters will actually do. An expert ranking is a
competing *opinion* about value. Our model produces the opinion; ADP tells us
what it will cost. Blending them destroys the only signal that lets you find a
market inefficiency.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.schemas import ADP_SCHEMA

ADP_COLUMN_SYNONYMS = {
    "player": "player_name", "name": "player_name", "player_name": "player_name",
    "adp": "adp", "avg_pick": "adp", "average_draft_position": "adp", "rank": "adp",
    "overall": "adp", "avg": "adp",
}


def normalise_name(name: str) -> str:
    """Canonical player-name key for joining across sources.

    Name matching is the most common silent failure in fantasy tooling: one
    source writes "Nikola Jokic", another "Nikola Jokić", a third "N. Jokic".
    Stripping accents, punctuation and suffixes catches most of it; the CLI
    reports anything left unmatched rather than dropping it quietly.
    """
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def load_adp_file(path: Path | str, source: str, retrieved_at: str,
                  league_format: str = "", scoring_format: str = "") -> pd.DataFrame:
    """Load one ADP CSV, tagging it with its full provenance."""
    path = Path(path)
    raw = pd.read_csv(path)
    renames = {}
    for column in raw.columns:
        key = str(column).strip().lower().replace(" ", "_")
        if key in ADP_COLUMN_SYNONYMS:
            renames[column] = ADP_COLUMN_SYNONYMS[key]
    frame = raw.rename(columns=renames)

    missing = {"player_name", "adp"} - set(frame.columns)
    if missing:
        raise ValueError(
            f"{path} is missing required column(s) {sorted(missing)}. "
            f"Found: {list(raw.columns)}. See docs/schemas.md."
        )

    out = frame[["player_name", "adp"]].copy()
    out["adp"] = pd.to_numeric(out["adp"], errors="coerce")
    out = out.dropna(subset=["adp"])
    out["source"] = source
    out["retrieved_at"] = retrieved_at
    out["league_format"] = league_format
    out["scoring_format"] = scoring_format
    out["name_key"] = out["player_name"].map(normalise_name)
    return out


def load_all_adp(sources_cfg: Mapping, repo_root: Path) -> pd.DataFrame:
    """Load every ADP source listed in config/sources.yaml."""
    entries = (sources_cfg.get("adp") or {}).get("sources") or []
    frames = []
    for entry in entries:
        path = repo_root / entry["file"]
        if not path.exists():
            continue
        frames.append(
            load_adp_file(
                path,
                source=entry.get("name", path.stem),
                retrieved_at=entry.get("retrieved_at", ""),
                league_format=entry.get("league_format", ""),
                scoring_format=entry.get("scoring_format", ""),
            )
        )
    if not frames:
        return pd.DataFrame(columns=list(ADP_SCHEMA) + ["name_key"])
    return pd.concat(frames, ignore_index=True)


def consensus_adp(adp: pd.DataFrame, min_sources: int = 1) -> pd.DataFrame:
    """Combine multiple ADP sources into a consensus with a disagreement measure.

    The spread between sources is reported, not smoothed away: a player the
    sources disagree about is a player whose draft-day cost is genuinely
    uncertain, which is useful information when deciding whether to reach.
    """
    if adp.empty:
        return pd.DataFrame(columns=["name_key", "player_name", "adp", "adp_sources", "adp_spread"])

    grouped = adp.groupby("name_key")
    out = grouped.agg(
        player_name=("player_name", "first"),
        adp=("adp", "median"),          # median resists one outlier source
        adp_min=("adp", "min"),
        adp_max=("adp", "max"),
        adp_sources=("source", "nunique"),
    ).reset_index()
    out["adp_spread"] = out["adp_max"] - out["adp_min"]
    return out[out["adp_sources"] >= min_sources].sort_values("adp").reset_index(drop=True)


def join_adp(board: pd.DataFrame, consensus: pd.DataFrame) -> pd.DataFrame:
    """Attach ADP to the draft board and compute the value gap.

    ``adp_vs_model`` is positive when the market drafts a player LATER than our
    model ranks them - i.e. a bargain we can wait on. Negative means the market
    pays more than we think they are worth.
    """
    out = board.copy()
    out["name_key"] = out["player_name"].map(normalise_name)

    if consensus.empty:
        out["adp"] = np.nan
        out["adp_sources"] = 0
        out["adp_spread"] = np.nan
        out["adp_vs_model"] = np.nan
        out["value_flag"] = "no_adp"
        return out.drop(columns=["name_key"])

    merged = out.merge(
        consensus[["name_key", "adp", "adp_sources", "adp_spread"]], on="name_key", how="left"
    )
    merged["adp_vs_model"] = merged["adp"] - merged["model_rank"]

    # Flag thresholds scale with draft size: a 10-pick gap means much more in
    # round 2 than in round 12.
    def classify(row) -> str:
        if pd.isna(row["adp"]):
            return "no_adp"
        gap = row["adp_vs_model"]
        tolerance = max(6.0, 0.25 * float(row["model_rank"]))
        # Inclusive at the boundary: a gap that exactly meets the tolerance is
        # still a disagreement with the market, and worth surfacing.
        if gap >= tolerance:
            return "undervalued"
        if gap <= -tolerance:
            return "overvalued"
        return "fairly_valued"

    merged["value_flag"] = merged.apply(classify, axis=1)
    return merged.drop(columns=["name_key"])
