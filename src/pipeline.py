"""End-to-end pipeline orchestration.

Engineering Rule 3 (handoff sec.23): *every transformation should be reproducible.
Prefer scripts over manually edited spreadsheets.* This module is the script: one
function turns raw game logs into a draft board, and it is the only path by which
``outputs/`` is ever written.
"""
from __future__ import annotations

from dataclasses import dataclass, replace, field
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
    lockin_distributions: dict = field(default_factory=dict)


def load_seasons(
    cfg: AppConfig,
    seasons: Sequence[str],
    root: Path | None = None,
    allow_synthetic: bool = False,
) -> pd.DataFrame:
    """Load and concatenate raw game logs for the requested seasons.

    ``allow_synthetic`` defaults to False so a stray generated file in data/raw/
    can never reach a real board. Only the demo path opts in - an explicit flag
    rather than a directory comparison, which would silently stop protecting
    anything the moment someone passed a different path.
    """
    root_path = Path(root) if root else cfg.paths["raw"]
    source = CSVSource(root_path, allow_synthetic=allow_synthetic)
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
    # A unique best metadata candidate may disambiguate a retired namesake.
    # Equally plausible candidates must remain unmatched, never first-row wins.
    best = source.groupby('name_key')[['_has_team', '_has_age']].transform('first')
    source = source[(source[['_has_team', '_has_age']] == best).all(axis=1)]
    source = source[~source.name_key.duplicated(keep=False)]

    carry = [c for c in ("team", "position", "positions", "age", "birth_date", "injury_status",
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


def data_health(logs: pd.DataFrame, players: pd.DataFrame) -> dict:
    """How much usable metadata actually reached the model.

    Worth checking explicitly, because the failure is silent: with no ages every
    player falls back to the default 27, the age curve applies one identical
    factor to everyone, and it therefore has *zero* effect on rankings while
    appearing to be switched on. The same goes for positions and shrinkage, which
    pulls toward a positional mean that is meaningless if positions are missing.
    """
    report: dict = {
        "players_in_logs": int(logs["player_id"].nunique()) if "player_id" in logs else 0,
        "players_in_table": len(players),
    }

    if "age" in players.columns:
        ages = pd.to_numeric(players["age"], errors="coerce")
        report["with_age"] = int(ages.notna().sum())
        report["distinct_ages"] = int(ages.dropna().nunique())
        report["age_min"] = float(ages.min()) if ages.notna().any() else None
        report["age_max"] = float(ages.max()) if ages.notna().any() else None
    else:
        report["with_age"] = 0
        report["distinct_ages"] = 0
        report["age_min"] = report["age_max"] = None

    if "position" in players.columns:
        positions = players["position"].replace("", pd.NA)
        report["with_position"] = int(positions.notna().sum())
        report["distinct_positions"] = sorted(str(p) for p in positions.dropna().unique())
    else:
        report["with_position"] = 0
        report["distinct_positions"] = []

    return report


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
    allow_synthetic: bool = False,
    current_logs: pd.DataFrame | None = None,
) -> PipelineResult:
    """Raw game logs -> draft board. The single reproducible path.

    ``current_logs`` are games already played in the projected season. They
    only update projections (src/projections/in_season.py); profiles, the
    availability history and the game-context fit stay on complete seasons.
    """
    warnings: list[str] = []
    engine = ScoringEngine(cfg.scoring)

    logs = load_seasons(cfg, seasons, raw_root, allow_synthetic=allow_synthetic)
    player_table = derive_players(logs, players)

    health = data_health(logs, player_table)
    if health["with_age"] == 0:
        warnings.append(
            "NO AGES reached the model - every player defaults to 27, so the age "
            "curve applies one identical factor and has no effect on rankings. "
            "Run `fetch-players`, or supply a players CSV with an `age` column."
        )
    elif health["distinct_ages"] <= 1:
        warnings.append(
            "All players share the same age, so the age curve cannot discriminate."
        )
    if health["with_position"] == 0:
        warnings.append(
            "NO POSITIONS reached the model - positional scarcity and shrinkage "
            "toward the positional mean are both inoperative."
        )

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

    if current_logs is not None and not current_logs.empty and projections:
        from src.projections.in_season import blend_in_season
        projections = blend_in_season(projections, current_logs, cfg.model)
        warnings.append(
            f"In-season update: projections blended with {len(current_logs)} player-games "
            f"played through {pd.to_datetime(current_logs.game_date).max().date()}")

    # Resolve current team BEFORE schedule valuation, including rookie priors.
    # Attaching a different team only after valuation would mislabel its schedule.
    if players is not None and projections:
        current = reconcile_players(pd.DataFrame([p.to_row() for p in projections]), players)
        projections = [replace(p, team=str(row['team']) if pd.notna(row['team']) else p.team,
                               position=str(row['position']) if pd.notna(row['position']) else p.position)
                       for p, row in zip(projections, current.to_dict('records'))]

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

    # --- schedule: disjoint regular/playoff windows on one shared calendar ---
    playoff_pmfs = {}
    calendar_notes = []
    playoff_weeks = 0
    if schedule is not None and not schedule.empty:
        from src.schedule.calendar import regular_schedule, weekly_counts, counts_to_pmfs
        schedule, schedule_notes = regular_schedule(schedule)
        counts = weekly_counts(schedule, int(cfg.league.calendar.get('week_start_weekday', 1)),
                               start=cfg.league.calendar.get('week_one_start'))
        regular_weeks = int(cfg.league.calendar.get('regular_season_weeks', 22))
        playoff_start = int(cfg.league.raw.get('playoffs', {}).get('start_week', regular_weeks+1))
        playoff_end = cfg.league.raw.get('playoffs', {}).get('end_week')
        if playoff_start <= regular_weeks:
            raise ValueError('Regular and playoff calendar windows overlap')
        team_pmfs = counts_to_pmfs(counts.iloc[:regular_weeks])
        playoff_counts = counts.iloc[playoff_start-1:playoff_end]
        playoff_weeks = len(playoff_counts)
        playoff_pmfs = counts_to_pmfs(playoff_counts)
        calendar_notes = schedule_notes + [
            'ASSUMPTION: week 1 anchored to first regular NBA fixture unless calendar.week_one_start is set',
            'ASSUMPTION: playoffs extend to last loaded week unless playoffs.end_week is set']
        warnings.extend(calendar_notes)
    else:
        # Derive weekly cadence from the historical logs themselves. Better than
        # the flat fallback PMF, but still not the real 2026-27 schedule.
        from src.schedule.calendar import latest_historical_schedule
        historical_schedule = latest_historical_schedule(logs)
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
    if playoff_pmfs and not frame.empty:
        playoff_values = valuations_to_frame(build_valuations(
            profiles, projections, games_projections, playoff_pmfs,
            cfg.league, cfg.model, engine, archetypes))
        fields = ['player_id', 'conditional_lockin_value', 'weekly_availability', 'used_real_schedule']
        frame = frame.merge(playoff_values[fields].rename(columns={
            c: 'playoff_'+c for c in fields if c != 'player_id'}), on='player_id', validate='one_to_one')
    if not frame.empty:
        # Also crosswalk explicit rookie priors, which have no game-log metadata.
        frame = reconcile_players(frame, players) if players is not None else frame
        if 'sleeper_id' not in frame:
            frame['sleeper_id'] = ''
        if 'positions' not in frame:
            frame['positions'] = frame['position']
        if schedule is None or schedule.empty:
            frame['used_real_schedule'] = False
        frame['playoff_weeks'] = playoff_weeks
        frame['calendar_notes'] = '; '.join(calendar_notes)

    # --- ADP ---
    from src.valuation import rescale_distribution
    samples_by_id = {p.player_id: p.fp_sample for p in profiles}
    lockin_distributions = {str(v.player_id): {
        'sample': np.round(rescale_distribution(samples_by_id[v.player_id], v.projected_fp_per_game), 6).tolist(),
        'basis': 'Simulated rookie prior' if str(v.player_id).startswith('ROOKIE_') else 'Historical scores rescaled to projection',
        'historical_games': v.historical_games,
    } for v in valuations}
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
        lockin_distributions=lockin_distributions,
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
    ordered = sorted(set(counts["season"]) | set(seasons), reverse=True)
    history: dict[str, list[float]] = {}
    for player_id, group in counts.groupby("player_id"):
        by_season = dict(zip(group["season"], group["games"]))
        # A whole missed season after the player's debut is 0 games, not a skipped
        # year; otherwise a season lost to injury reads as an old healthy one.
        debut = min(by_season)
        history[str(player_id)] = [
            float(by_season.get(s, 0)) / season_length for s in ordered if s >= debut
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
