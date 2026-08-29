"""End-to-end pipeline orchestration.

Engineering Rule 3 (handoff sec.23): *every transformation should be reproducible.
Prefer scripts over manually edited spreadsheets.* This module is the script: one
function turns raw game logs into a draft board, and it is the only path by which
``outputs/`` is ever written.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.adp import consensus_adp, load_all_adp
from src.config import AppConfig
from src.distributions import assign_archetypes, build_profiles, profiles_to_frame
from src.draft import build_draft_board
from src.ingestion.csv_source import CSVSource
from src.projections import compute_per36, project_games_played, project_players
from src.projections.rookies import build_rookie_projections, rookie_availability
from src.schedule import games_per_week_by_team
from src.scoring import ScoringEngine
from src.valuation import build_valuations, valuations_to_frame


@dataclass
class PipelineResult:
    board: pd.DataFrame
    profiles: pd.DataFrame
    scored_logs: pd.DataFrame
    warnings: list[str]
    is_synthetic: bool


def load_seasons(cfg: AppConfig, seasons: Sequence[str], root: Path | None = None) -> pd.DataFrame:
    """Load and concatenate raw game logs for the requested seasons."""
    source = CSVSource(root or cfg.paths["raw"])
    frames, warnings = [], []
    for season in seasons:
        try:
            frames.append(source.fetch_game_logs(season))
        except FileNotFoundError as exc:
            warnings.append(str(exc))
    if not frames:
        raise FileNotFoundError(
            "No game logs found for any requested season.\n" + "\n".join(warnings)
        )
    return pd.concat(frames, ignore_index=True)


def derive_players(logs: pd.DataFrame, players: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build the player metadata table from the game logs, or reconcile a supplied one.

    Anything downstream keys on the game logs' own ``player_id``, so an external
    metadata file must be reconciled onto that key rather than used as-is.
    """
    derived = _players_from_logs(logs)
    if players is None or players.empty:
        return derived
    return reconcile_players(derived, players)


def _players_from_logs(logs: pd.DataFrame) -> pd.DataFrame:
    latest = logs.sort_values("game_date").groupby("player_id").tail(1)
    derived = latest[["player_id", "player_name"]].copy()
    derived["team"] = latest["team"].to_numpy() if "team" in latest else ""
    derived["position"] = latest["position"].to_numpy() if "position" in latest else ""
    derived["age"] = np.nan
    derived["is_synthetic"] = latest["is_synthetic"].to_numpy() if "is_synthetic" in latest else False
    return derived


def reconcile_players(base: pd.DataFrame, external: pd.DataFrame) -> pd.DataFrame:
    """Attach external metadata (age, position) to the players in the game logs.

    **Joined on normalised NAME, not on any id.** The two sources live in
    different id spaces and there is no shared key: game logs carry
    stats.nba.com player ids, while Sleeper issues its own (exported as
    ``sleeper_id``). Merging on ``player_id`` either raises - because the column
    is simply absent from a Sleeper export - or, worse, succeeds and produces
    entirely NaN metadata, which then silently degrades every projection.

    Name matching is imperfect, so the caller reports the match rate rather than
    assuming it worked.
    """
    from src.adp.loader import normalise_name

    out = base.copy()
    if external is None or external.empty or "player_name" not in external.columns:
        return out

    source = external.copy()
    source["name_key"] = source["player_name"].map(normalise_name)

    # Prefer rows for players currently on a roster, then those with an age, so
    # a retired namesake does not win the join.
    source["_has_team"] = source["team"].notna() if "team" in source.columns else False
    source["_has_age"] = source["age"].notna() if "age" in source.columns else False
    source = source.sort_values(["_has_team", "_has_age"], ascending=False)
    source = source.drop_duplicates(subset=["name_key"], keep="first")

    carry = [c for c in ("position", "positions", "age", "injury_status",
                         "years_experience", "sleeper_id") if c in source.columns]
    out["name_key"] = out["player_name"].map(normalise_name)
    merged = out.merge(
        source[["name_key", *carry]], on="name_key", how="left", suffixes=("", "_ext")
    )

    # External values win only where they actually exist.
    for column in carry:
        external_column = f"{column}_ext" if f"{column}_ext" in merged.columns else column
        if external_column not in merged.columns:
            continue
        if column in out.columns and external_column != column:
            merged[column] = merged[external_column].combine_first(merged[column])
            merged = merged.drop(columns=[external_column])
        elif column in ("position",) and column in merged.columns:
            merged[column] = merged[column].replace("", pd.NA)

    return merged.drop(columns=["name_key"])


def player_match_rate(players: pd.DataFrame) -> tuple[int, int]:
    """(matched, total) players that picked up an age from external metadata."""
    if players.empty or "age" not in players.columns:
        return 0, len(players)
    return int(players["age"].notna().sum()), len(players)


def run_pipeline(
    cfg: AppConfig,
    seasons: Sequence[str],
    raw_root: Path | None = None,
    players: pd.DataFrame | None = None,
    schedule: pd.DataFrame | None = None,
) -> PipelineResult:
    """Raw game logs -> draft board. The single reproducible path."""
    warnings: list[str] = []
    engine = ScoringEngine(cfg.scoring)

    logs = load_seasons(cfg, seasons, raw_root)
    player_table = derive_players(logs, players)

    if players is not None and not players.empty:
        matched, total = player_match_rate(player_table)
        if total and matched / total < 0.5:
            warnings.append(
                f"Only {matched}/{total} players matched the supplied metadata by name. "
                "Ages and positions are missing for the rest, so the age curve and "
                "positional scarcity are degraded. Check that the metadata file covers "
                "the same seasons as your game logs."
            )

    # Attach position so distributions and scarcity can use it.
    if "position" not in logs.columns and "position" in player_table.columns:
        logs = logs.merge(
            player_table[["player_id", "position"]].drop_duplicates("player_id"),
            on="player_id", how="left",
        )

    scored = engine.score_dataframe(logs)

    # --- facts ---
    profiles = build_profiles(scored, cfg.model)
    profile_frame = profiles_to_frame(profiles)
    if not profile_frame.empty:
        profile_frame = assign_archetypes(profile_frame)
    archetypes = (
        dict(zip(profile_frame["player_id"], profile_frame["archetype"]))
        if "archetype" in profile_frame.columns else {}
    )

    # --- projections (assumptions) ---
    per36 = compute_per36(logs)
    projections = project_players(per36, player_table, cfg.model, cfg.assumptions)
    if not projections:
        warnings.append("No projections produced - check that player metadata joins to the logs.")

    # --- rookies: no game logs exist, so they enter as explicit priors ---
    rookie_projections, rookie_profiles = build_rookie_projections(
        cfg.assumptions, cfg.model, engine
    )
    if rookie_projections:
        projections = list(projections) + rookie_projections
        profiles = list(profiles) + rookie_profiles
        warnings.append(
            f"ASSUMPTION: {len(rookie_projections)} rookies projected from priors in "
            "config/assumptions.yaml, not from NBA data. Verify their roles and "
            "landing spots before drafting them."
        )
        low_confidence = [
            p.player_name for p in rookie_projections if "VERIFY" in p.assumption_notes
        ]
        if low_confidence:
            warnings.append(
                "VERIFY LANDING SPOTS - these rookies came from single or unconfirmed "
                f"sources and their team may be wrong: {', '.join(low_confidence)}"
            )

    season_length = int(cfg.model.get("games_played", {}).get("season_length", 82))
    availability_history = _availability_history(logs, seasons, season_length)
    injuries = cfg.assumptions.get("injuries") or {}
    rookie_entries = cfg.assumptions.get("rookies") or {}
    games_projections = {}
    for projection in projections:
        history = availability_history.get(projection.player_id, [])
        if not history and projection.player_name in rookie_entries:
            # A rookie has no history at all; use the explicit rookie prior rather
            # than letting the age baseline invent one.
            history = [rookie_availability(rookie_entries[projection.player_name], season_length)]
        games_projections[projection.player_id] = project_games_played(
            projection.player_id,
            projection.age,
            history,
            cfg.model,
            injuries.get(projection.player_name),
        )

    # --- schedule ---
    if schedule is not None and not schedule.empty:
        team_pmfs = games_per_week_by_team(
            schedule, int(cfg.league.calendar.get("week_start_weekday", 1))
        )
    else:
        # Derive weekly cadence from the historical logs themselves. Better than
        # the flat fallback PMF, but still not the real 2026-27 schedule.
        historical_schedule = logs[["game_date", "team"]].drop_duplicates()
        team_pmfs = games_per_week_by_team(
            historical_schedule, int(cfg.league.calendar.get("week_start_weekday", 1))
        )
        warnings.append(
            "ASSUMPTION: games-per-week derived from HISTORICAL schedules, not the "
            "real 2026-27 schedule. Lock-In value is schedule-sensitive; ingest the "
            "real schedule when it is published."
        )

    # --- valuation ---
    valuations = build_valuations(
        profiles, projections, games_projections, team_pmfs,
        cfg.league, cfg.model, engine, archetypes,
    )
    frame = valuations_to_frame(valuations)

    # --- ADP ---
    adp = load_all_adp(cfg.sources, cfg.paths["repo"])
    consensus = consensus_adp(adp) if not adp.empty else pd.DataFrame()
    if consensus.empty:
        warnings.append(
            "No ADP sources loaded (config/sources.yaml adp.sources is empty). "
            "Market-value analysis is unavailable - the board ranks on model value only."
        )

    board = build_draft_board(frame, cfg.league, cfg.model, consensus if not consensus.empty else None)

    is_synthetic = bool(board["is_synthetic"].any()) if "is_synthetic" in board.columns else False
    if is_synthetic:
        warnings.append(
            "SYNTHETIC DATA IN OUTPUT - this board contains generated players and "
            "must not be used to make real draft decisions."
        )

    return PipelineResult(
        board=board,
        profiles=profile_frame,
        scored_logs=scored,
        warnings=warnings,
        is_synthetic=is_synthetic,
    )


def _availability_history(
    logs: pd.DataFrame, seasons: Sequence[str], season_length: int
) -> dict[str, list[float]]:
    """Games-played fraction per player per season, most recent season first."""
    if "season" not in logs.columns:
        return {}
    counts = logs.groupby(["player_id", "season"]).size().reset_index(name="games")
    ordered = sorted(set(counts["season"]), reverse=True)
    history: dict[str, list[float]] = {}
    for player_id, group in counts.groupby("player_id"):
        by_season = dict(zip(group["season"], group["games"]))
        history[str(player_id)] = [
            float(by_season[s]) / season_length for s in ordered if s in by_season
        ]
    return history


def write_outputs(result: PipelineResult, cfg: AppConfig) -> dict[str, Path]:
    """Write draft_board.csv, player_values.csv and draft_board.html."""
    from src.reporting import render_draft_board

    output_dir = cfg.paths["outputs"]
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix = "SYNTHETIC_" if result.is_synthetic else ""
    board_csv = output_dir / f"{prefix}draft_board.csv"
    values_csv = output_dir / f"{prefix}player_values.csv"
    board_html = output_dir / f"{prefix}draft_board.html"

    result.board.to_csv(board_csv, index=False)
    result.profiles.to_csv(values_csv, index=False)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subtitle = (
        f"Generated {generated} | {len(result.board)} players | "
        f"scoring from config/league.yaml"
    )
    render_draft_board(result.board, board_html, subtitle)

    return {"board_csv": board_csv, "values_csv": values_csv, "board_html": board_html}
