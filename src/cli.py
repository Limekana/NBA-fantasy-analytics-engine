"""Command-line interface.

    python -m src.cli --help

Designed so the whole system is usable from a phone over SSH or a web terminal:
every command prints a self-contained, readable report rather than expecting a
notebook or a GUI.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from src.config import RAW_DIR, load_config, validate
from src.scoring import ScoringEngine


DATA_HELP = """
The pipeline needs THREE things. Only the first is required.

===============================================================================
1. GAME LOGS  (required)  ->  data/raw/<season>/anything.csv
===============================================================================

EASIEST - let the tool fetch it (needs internet, ~2 min/season):

    pip install nba_api
    python -m src.cli ingest --season 2025-26 --source nba_api
    python -m src.cli ingest --season 2024-25 --source nba_api
    python -m src.cli ingest --season 2023-24 --source nba_api

  Sanity check: a full season is ~26,000 rows. The command prints the count.
  If you get a few hundred, the endpoint truncated - do not build a board on it.
  You need >= 2 seasons for `backtest` to run at all.

MANUAL - any CSV works. Sources, best first:

  * Basketball Reference -> any player game-log page -> "Share & Export" ->
    "Get table as CSV". Free. Tedious for a whole league, fine for spot checks.
  * Kaggle - search "NBA player game logs"; several maintained dumps exist.
    Download, unzip, drop the CSV in. Check it covers the seasons you want.
  * nbastuffer / hoopR (R package) - both export game-level CSV.

  Put the file anywhere under data/raw/<season>/ and the pipeline finds it.

  COLUMN NAMES: you do NOT need to rename anything. These are all recognised
  automatically, case-insensitively:

    what it is        canonical name          also accepted
    ----------------  ----------------------  ---------------------------
    player            player_name             Player, PLAYER_NAME, Name
    date              game_date               Date, GAME_DATE
    team              team                    Tm, TEAM_ABBREVIATION
    opponent          opponent                Opp, or parsed from MATCHUP
    minutes           minutes                 MIN, MP   ("34:12" or 34.2)
    points            points                  PTS
    rebounds          rebounds                REB, TRB
    assists           assists                 AST
    steals            steals                  STL
    blocks            blocks                  BLK
    turnovers         turnovers               TOV, TO
    fouls             personal_fouls          PF, Fouls
    free throws made  free_throws_made        FTM, FT
    threes made       three_pointers_made     FG3M, 3P, TPM

  REQUIRED: player, date, and the nine box-score stats. Everything else is
  optional - player_id and game_id are generated if absent, and opponent/home
  are parsed out of a MATCHUP column when one exists.

  A minimal valid CSV looks exactly like this:

    Player,Date,Tm,MIN,PTS,TRB,AST,STL,BLK,TOV,PF,FT,3P
    Nikola Jokic,2025-10-21,DEN,35.2,28,12,11,1,1,3,2,5,2

  Then:  python -m src.cli build-board --seasons 2025-26 2024-25 2023-24

===============================================================================
2. PLAYER METADATA  (strongly recommended)  ->  pass with --players
===============================================================================

Game logs carry no age and no position. Without them the model defaults every
player to age 27 (and says so in assumption_notes) and cannot compute positional
scarcity.

    python -c "from src.ingestion.sleeper_source import SleeperSource; \
    SleeperSource().fetch_players().to_csv('data/external/players.csv', index=False)"

  Free, public, no API key. Also gives current injury_status.
  Or write your own CSV:  player_name,team,position,age

===============================================================================
3. SCHEDULE  (highest-leverage optional input)  ->  pass with --schedule
===============================================================================

Lock-In value depends directly on games per fantasy week. Without the real
2026-27 schedule the model falls back to historical cadence, which cannot tell a
player on a 4-game-week-heavy schedule from one who is not.

    python -c "from src.ingestion.nba_api_source import NBAScheduleSource; \
    NBAScheduleSource().fetch_schedule('2026-27').to_csv( \
    'data/external/schedule_2026-27.csv', index=False)"

  Format:  game_date,home_team,away_team    (game_id and season optional)

===============================================================================
4. ADP  (optional; enables market-value analysis)  ->  data/external/adp/
===============================================================================

Without it the board ranks on model value only and cannot flag bargains.
Any CSV with two columns works:

    player_name,adp
    Nikola Jokic,1.4

Register each file in config/sources.yaml under adp.sources with its name,
retrieved_at, league_format and scoring_format. Multiple sources are combined by
median and their disagreement is reported - never trust a single source.

NOTE: published NBA ADP is almost always 9-cat or standard points, NOT Sleeper
Lock-In with these bonuses. That mismatch IS the edge, but record the real
scoring_format so you remember the board is comparing against a different game.

===============================================================================
Then, in order:
    python -m src.cli check-config     # confirm scoring is right
    python -m src.cli backtest         # NEVER SKIP - validates the model
    python -m src.cli build-board      # produce the board
    python -m src.cli draft --pick N   # on draft day
===============================================================================
"""


def _print_header(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def _warn(warnings) -> None:
    if warnings:
        print("\nWARNINGS")
        for warning in warnings:
            print(f"  ! {warning}")


# --------------------------------------------------------------------------
def cmd_check_config(args) -> int:
    """Validate every configuration file and show the resolved scoring rules."""
    cfg = load_config()
    _print_header("CONFIGURATION CHECK")

    problems = validate(cfg)
    if problems:
        print("\nPROBLEMS FOUND:")
        for problem in problems:
            print(f"  x {problem}")
    else:
        print("\nAll configuration files are valid.")

    print(f"\nLeague: {cfg.league.teams} teams, mode={cfg.league.game_mode}, "
          f"roster size {cfg.league.roster_size}")
    print(f"Lock-In enabled: {cfg.league.is_lock_in}")
    print(f"Auto-lock fallback: {cfg.league.lock_in.get('auto_lock_fallback')}")

    print("\nSCORING (from config/league.yaml)")
    for stat, weight in cfg.scoring.stat_weights.items():
        print(f"  {stat:<24} {weight:+g}")
    print("\nBONUSES")
    for bonus, value in cfg.scoring.bonuses.items():
        print(f"  {bonus:<24} {value:+g}")

    print("\nBONUS INTERACTION RULES")
    print(f"  double-double categories : {', '.join(cfg.scoring.dd_categories)} (>= {cfg.scoring.dd_threshold})")
    print(f"  TD also pays DD          : {cfg.scoring.td_stacks}   [VERIFIED against Sleeper docs]")
    print(f"  50+ also pays 40+        : {cfg.scoring.point_thresholds_stack}   "
          f"[** UNVERIFIED ASSUMPTION - confirm in your league's Sleeper UI **]")

    slot = cfg.league.draft.get("my_draft_slot")
    print(f"\nDraft slot: {slot if slot else 'NOT SET (set draft.my_draft_slot after the lottery)'}")
    return 1 if problems else 0


def cmd_scoring_check(args) -> int:
    """Score reference stat lines so they can be compared against Sleeper by hand.

    This is the verification step for the one assumption the build could not
    confirm: take a real 50-point game from your league, compare Sleeper's number
    with this one, and flip bonus_rules.points_thresholds_stack if they disagree.
    """
    cfg = load_config()
    engine = ScoringEngine(cfg.scoring)
    _print_header("SCORING ENGINE REFERENCE LINES")
    print("\nCompare these against Sleeper's own scoring for the same box score.\n")

    cases = [
        ("39 pts (no point bonus)", dict(points=39)),
        ("40 pts exactly", dict(points=40)),
        ("41 pts", dict(points=41)),
        ("49 pts", dict(points=49)),
        ("50 pts exactly  <-- KEY TEST", dict(points=50)),
        ("51 pts", dict(points=51)),
        ("10/10 reb+ast (double-double)", dict(points=5, rebounds=10, assists=10)),
        ("10/10/10 (triple-double)  <-- KEY TEST", dict(points=10, rebounds=10, assists=10)),
        ("14 assists", dict(points=5, assists=14)),
        ("15 assists exactly", dict(points=5, assists=15)),
        ("16 assists", dict(points=5, assists=16)),
        ("19 rebounds", dict(points=5, rebounds=19)),
        ("20 rebounds exactly", dict(points=5, rebounds=20)),
        ("50/10/15 (everything at once)", dict(points=50, rebounds=10, assists=15)),
        ("Typical star line", dict(points=28, rebounds=8, assists=6, steals=1, blocks=1,
                                   turnovers=3, personal_fouls=2, free_throws_made=6,
                                   three_pointers_made=3)),
    ]
    for label, stats in cases:
        breakdown = engine.score_game_detailed(stats)
        bonuses = ", ".join(f"{k}+{v:g}" for k, v in breakdown.bonuses_awarded.items()) or "none"
        print(f"  {label:<38} {breakdown.total:8.2f} FP   (base {breakdown.base:6.2f}, bonus: {bonuses})")

    print(f"\nCurrent assumption: 50+ stacks with 40+ = {cfg.scoring.point_thresholds_stack}")
    print("If Sleeper reports a different number for the 50-point line, edit")
    print("config/league.yaml -> bonus_rules.points_thresholds_stack and rerun.")
    return 0


def cmd_ingest(args) -> int:
    """Download raw game logs. Requires network access to stats.nba.com."""
    from src.ingestion.base import validate_game_logs, write_raw

    cfg = load_config()
    _print_header(f"INGEST {args.season} FROM {args.source}")

    if args.source == "nba_api":
        from src.ingestion.nba_api_source import NBAApiSource
        source = NBAApiSource()
    elif args.source == "csv":
        from src.ingestion.csv_source import CSVSource
        source = CSVSource()
    else:
        print(f"Unknown source: {args.source}")
        return 1

    try:
        logs = source.fetch_game_logs(args.season)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print("\nIf this is a network error, this command must be run from a machine")
        print("with access to stats.nba.com. See docs/data_sources.md for the")
        print("manual CSV route, which needs no API access.")
        return 1

    problems = validate_game_logs(logs)
    if problems:
        print("\nSCHEMA WARNINGS:")
        for problem in problems:
            print(f"  ! {problem}")

    destination = RAW_DIR / args.season / f"game_logs_{args.season}.csv"
    write_raw(logs, destination, source.name, "game_log", args.season)
    print(f"\nWrote {len(logs):,} rows to {destination}")
    print(f"Manifest: {destination}.manifest.json")
    return 0


def cmd_fetch_players(args) -> int:
    """Download player metadata (age, position, injury status) from Sleeper.

    Ages drive the age curve and the availability baseline; positions drive
    positional scarcity. Game logs carry neither, so without this the model
    defaults everyone to age 27 and says so in assumption_notes.
    """
    from src.config import EXTERNAL_DIR
    from src.ingestion.sleeper_source import SleeperSource

    _print_header("FETCH PLAYER METADATA FROM SLEEPER")
    print("\nPublic endpoint, no API key needed. Payload is ~5MB, give it a moment.\n")
    try:
        players = SleeperSource().fetch_players()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}")
        print("\nThis needs internet access to api.sleeper.app.")
        print("You can skip it - the model still runs, but without ages or positions.")
        return 1

    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    destination = EXTERNAL_DIR / "players.csv"
    players.to_csv(destination, index=False)
    on_team = int(players["team"].notna().sum()) if "team" in players.columns else 0
    print(f"Wrote {len(players):,} players ({on_team:,} currently on an NBA roster)")
    print(f"  -> {destination}")
    print("\n`build-board` and `backtest` will pick this up automatically.")
    return 0


def cmd_fetch_schedule(args) -> int:
    """Download the NBA schedule, which drives games-per-fantasy-week.

    This is the highest-leverage optional input: in Lock-In, a player on a
    4-game-week-heavy schedule is worth strictly more than an identical player
    who is not, and without this the model falls back to historical cadence.
    """
    from src.config import EXTERNAL_DIR
    from src.ingestion.nba_api_source import NBAScheduleSource

    season = args.season
    _print_header(f"FETCH {season} SCHEDULE")
    try:
        schedule = NBAScheduleSource().fetch_schedule(season)
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print("\nNeeds `pip install nba_api` and internet access to stats.nba.com.")
        print("If the schedule is not published yet, skip it - the model falls back")
        print("to historical weekly cadence and flags that as an assumption.")
        return 1

    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    destination = EXTERNAL_DIR / f"schedule_{season}.csv"
    schedule.to_csv(destination, index=False)
    print(f"\nWrote {len(schedule):,} games -> {destination}")
    print("\n`build-board` will pick this up automatically.")
    return 0


def _auto_discover(cfg, explicit_players, explicit_schedule):
    """Find players.csv / schedule_*.csv in data/external/ unless overridden.

    Beginners should not have to remember two --flags on every run, and the
    files always land in the same place because fetch-players and fetch-schedule
    put them there.
    """
    external = cfg.paths["external"]
    players_path = Path(explicit_players) if explicit_players else external / "players.csv"
    players = None
    if players_path.exists():
        players = pd.read_csv(players_path)
        print(f"  using player metadata: {players_path}")

    schedule = None
    if explicit_schedule:
        schedule_path = Path(explicit_schedule)
    else:
        season = cfg.league.raw.get("meta", {}).get("season", "2026-27")
        candidates = sorted(external.glob(f"schedule_{season}*.csv")) or sorted(external.glob("schedule_*.csv"))
        schedule_path = candidates[-1] if candidates else None
    if schedule_path and Path(schedule_path).exists():
        schedule = pd.read_csv(schedule_path)
        schedule["game_date"] = pd.to_datetime(schedule["game_date"])
        print(f"  using schedule: {schedule_path}")

    return players, schedule


def cmd_demo(args) -> int:
    """Generate synthetic data and run the whole pipeline on it.

    Proves the pipeline works end to end without any network access. The output
    is fictional and is labelled as such everywhere it appears.
    """
    from src.ingestion.synthetic import write_synthetic_season

    cfg = load_config()
    _print_header("SYNTHETIC END-TO-END DEMO")
    print("\n*** SYNTHETIC DATA - NOT REAL PLAYERS, NOT A REAL PROJECTION ***\n")

    seasons = args.seasons or ["2023-24", "2024-25", "2025-26"]
    _path, players, logs = write_synthetic_season(
        RAW_DIR, n_players=args.players, seasons=seasons
    )
    print(f"Generated {len(players)} players / {len(logs):,} game rows "
          f"across {len(seasons)} seasons")
    print("Run `python -m src.cli backtest` to exercise the backtest machinery.\n")

    return _run_and_report(cfg, seasons, players=players, verbose=args.verbose)


def cmd_build_board(args) -> int:
    """Run the pipeline on whatever real data is in data/raw/."""
    cfg = load_config()
    _print_header("BUILD DRAFT BOARD")
    seasons = args.seasons or ["2025-26", "2024-25", "2023-24"]
    players, schedule = _auto_discover(cfg, args.players, args.schedule)
    return _run_and_report(cfg, seasons, players=players, schedule=schedule, verbose=args.verbose)


def _run_and_report(cfg, seasons, players=None, schedule=None, verbose=False) -> int:
    from src.pipeline import run_pipeline, write_outputs

    try:
        result = run_pipeline(cfg, seasons, players=players, schedule=schedule)
    except FileNotFoundError as exc:
        print(f"\n{exc}")
        return 1

    if result.board.empty:
        print("\nNo players in the resulting board.")
        _warn(result.warnings)
        return 1

    paths = write_outputs(result, cfg)
    columns = [
        c for c in ("model_rank", "player_name", "team", "position", "tier",
                    "projected_fp_game", "projected_games", "lockin_value",
                    "lock_in_advantage", "projected_season_value", "adp",
                    "adp_vs_model", "risk")
        if c in result.board.columns
    ]
    print(f"\nTOP {min(25, len(result.board))} BY MODEL VALUE\n")
    print(result.board[columns].head(25).to_string(index=False))

    if verbose and "archetype" in result.board.columns:
        print("\nARCHETYPE DISTRIBUTION")
        print(result.board["archetype"].value_counts().to_string())

    print("\nOUTPUTS")
    for name, path in paths.items():
        print(f"  {name:<12} {path}")
    _warn(result.warnings)
    return 0


def cmd_draft(args) -> int:
    """Live draft assistant."""
    from src.draft import DraftAssistant, format_recommendation

    cfg = load_config()
    board_path = Path(args.board) if args.board else _find_board(cfg)
    if board_path is None or not board_path.exists():
        print("No draft board found. Run `python -m src.cli build-board` first "
              "(or `demo` to try it on synthetic data).")
        return 1

    board = pd.read_csv(board_path)
    if "is_synthetic" in board.columns and board["is_synthetic"].any():
        print("\n*** WARNING: this board is built on SYNTHETIC data. "
              "Do not draft from it. ***\n")

    slot = args.slot or cfg.league.draft.get("my_draft_slot")
    if not slot:
        print("Draft slot unknown. Pass --slot N, or set draft.my_draft_slot in "
              "config/league.yaml once the lottery has happened.")
        return 1

    drafted = _read_names(args.drafted)
    my_roster = _read_names(args.roster)

    assistant = DraftAssistant(board, cfg.league, cfg.model)
    package = assistant.recommend(
        my_slot=int(slot),
        current_pick=args.pick,
        drafted_names=drafted,
        my_roster=my_roster,
        top_n=args.top,
        n_simulations=args.simulations,
    )
    print(format_recommendation(package))
    return 0


def cmd_availability(args) -> int:
    """What is likely to reach a given pick? (Monte Carlo)"""
    from src.draft import DraftSimulator, board_to_players, picks_for_slot

    cfg = load_config()
    board_path = Path(args.board) if args.board else _find_board(cfg)
    if board_path is None or not board_path.exists():
        print("No draft board found. Run `python -m src.cli build-board` first.")
        return 1

    board = pd.read_csv(board_path)
    slot = args.slot or cfg.league.draft.get("my_draft_slot")
    if not slot:
        print("Pass --slot N or set draft.my_draft_slot in config/league.yaml.")
        return 1

    rounds = int(cfg.league.draft.get("rounds", cfg.league.roster_size))
    my_picks = picks_for_slot(int(slot), cfg.league.teams, rounds)
    _print_header(f"AVAILABILITY FOR DRAFT SLOT {slot}")
    print(f"\nYour picks: {', '.join(str(p) for p in my_picks)}\n")

    simulator = DraftSimulator(cfg.model, cfg.league)
    players = board_to_players(board)
    targets = [p for p in my_picks if p <= args.through]
    result = simulator.simulate(players, n_simulations=args.simulations, target_picks=targets)

    lookup = {p.player_id: p for p in players}
    rows = [
        {
            "player": lookup[pid].name,
            "pos": lookup[pid].position,
            "model_rank": lookup[pid].model_rank,
            "adp": lookup[pid].adp,
            **{f"avail@{pick}": probs[i] for i, pick in enumerate(sorted(targets))},
        }
        for pid, probs in result.picked_by_round.items()
        if pid in lookup
    ]
    frame = pd.DataFrame(rows).sort_values("model_rank").head(args.top)
    percent_columns = [c for c in frame.columns if c.startswith("avail@")]
    for column in percent_columns:
        frame[column] = (frame[column] * 100).round(0).astype(int).astype(str) + "%"
    print(frame.to_string(index=False))
    print(f"\n({result.n_simulations:,} simulated drafts)")
    return 0


def cmd_backtest(args) -> int:
    """Backtest the projection model and the Lock-In strategies.

    Handoff sec.20 calls this mandatory before trusting the model.
    """
    from src.backtest import backtest_lockin_strategies, backtest_projections
    from src.pipeline import derive_players, load_seasons

    cfg = load_config()
    _print_header("BACKTEST")

    seasons = args.seasons or ["2025-26", "2024-25", "2023-24"]
    try:
        logs = load_seasons(cfg, seasons)
    except FileNotFoundError as exc:
        print(f"\n{exc}")
        return 1

    discovered, _schedule = _auto_discover(cfg, args.players, None)
    players = discovered if discovered is not None else derive_players(logs)
    if "position" not in logs.columns and "position" in players.columns:
        logs = logs.merge(players[["player_id", "position"]], on="player_id", how="left")
    scored = ScoringEngine(cfg.scoring).score_dataframe(logs)

    synthetic = bool(scored["is_synthetic"].any()) if "is_synthetic" in scored.columns else False
    if synthetic:
        print("\n*** SYNTHETIC DATA - these results validate the BACKTEST MACHINERY,")
        print("    not the model. Real conclusions need real game logs. ***")

    # --- 1. Lock-In strategies on real weekly sequences ---
    print("\n" + "-" * 72)
    print("LOCK-IN STRATEGY BACKTEST")
    print("-" * 72)
    print("Replays each locking policy over actual chronological fantasy weeks.")
    print("No projection involved - this is what each rule would have banked.\n")
    try:
        lockin = backtest_lockin_strategies(scored, cfg, season=args.lockin_season)
        frame = lockin.to_frame()
        print(frame.to_string(index=False))
        print(f"\n{lockin.n_weeks:,} player-weeks across {lockin.n_players} players")

        per_week = lockin.strategy_per_week
        floor_value = per_week.get("last_game", 0.0)
        best = max((v for k, v in per_week.items() if k != "perfect"), default=0.0)
        available = best - floor_value
        if available > 0:
            print("\nShare of the available edge captured by each policy")
            print("(0% = never locking, 100% = the best realistic policy):")
            for name, value in sorted(per_week.items(), key=lambda kv: -kv[1]):
                if name == "perfect":
                    continue
                print(f"  {name:<14} {(value - floor_value) / available * 100:6.1f}%")
    except ValueError as exc:
        print(f"  skipped: {exc}")

    # --- 2. Projection model vs naive baselines ---
    print("\n" + "-" * 72)
    print("PROJECTION BACKTEST")
    print("-" * 72)
    available_seasons = sorted(scored["season"].dropna().unique())
    target = args.target_season or (available_seasons[-1] if available_seasons else None)
    if target is None or len(available_seasons) < 2:
        print("\n  SKIPPED: needs at least two seasons of data.")
        print(f"  Seasons present: {available_seasons or 'none'}")
        print("  Ingest an earlier season and re-run - the projection model is")
        print("  UNVALIDATED until this passes.")
        return 0

    print(f"\nTraining on seasons before {target}; predicting {target}.")
    print("Scored on rank correlation, because drafting is a ranking problem.\n")
    try:
        metrics, comparison = backtest_projections(scored, players, cfg, target)
    except ValueError as exc:
        print(f"  SKIPPED: {exc}")
        return 0

    table = pd.DataFrame([m.to_row() for m in metrics])
    print(table.to_string(index=False))

    model = next((m for m in metrics if m.name == "model"), None)
    baselines = [m for m in metrics if m.name != "model"]
    if model and baselines:
        best_baseline = max(baselines, key=lambda m: m.spearman)
        delta = model.spearman - best_baseline.spearman
        print("\nVERDICT")
        if delta > 0.01:
            print(f"  Model beats the best baseline ({best_baseline.name}) by "
                  f"{delta:+.4f} Spearman.")
        elif delta > -0.01:
            print(f"  Model is level with {best_baseline.name} ({delta:+.4f} Spearman).")
            print("  It adds no ranking value yet. Handoff Rule 5: do not keep")
            print("  complexity that does not earn its place.")
        else:
            print(f"  *** Model LOSES to {best_baseline.name} by {abs(delta):.4f} "
                  f"Spearman. ***")
            print("  Investigate before trusting the board. Handoff sec.20.")

    # --- 3. optional weight tuning ---
    if args.tune:
        from src.backtest import tune_projection_weights

        print("\n" + "-" * 72)
        print("PROJECTION WEIGHT TUNING  (handoff sec.6)")
        print("-" * 72)
        print("Grid search over season-blend weights and shrinkage.\n")
        grid = tune_projection_weights(scored, players, cfg, target)
        if grid.empty:
            print("  No configurations could be evaluated.")
        else:
            print(grid.head(15).to_string(index=False))
            best = grid.iloc[0]
            print(f"\n  Best: {best['profile']} (weights {best['weights']}, "
                  f"k={best['shrinkage_k']}) at {best['spearman']:.4f} Spearman, "
                  f"{best['vs_baseline']:+.4f} vs baseline.")
            print("\n  CAUTION: this grid was searched on the same held-out season it")
            print("  is scored on, so the top row is optimistic. Prefer a profile that")
            print("  is strong across several shrinkage values, and re-test on a")
            print("  different target season before adopting it in config/model.yaml.")

    if args.output:
        table.to_csv(args.output, index=False)
        comparison.to_csv(str(args.output).replace(".csv", "_players.csv"))
        print(f"\nWrote {args.output}")
    return 0


def cmd_data_help(args) -> int:
    """Print exactly where to get data and what format it needs."""
    _print_header("HOW TO GET DATA INTO THIS PIPELINE")
    print(DATA_HELP)
    return 0


def cmd_verify_league(args) -> int:
    """Diff config/league.yaml against the real Sleeper league settings."""
    from src.ingestion.sleeper_source import SleeperSource, diff_scoring

    cfg = load_config()
    league_id = args.league_id or cfg.league.raw.get("meta", {}).get("sleeper_league_id")
    if not league_id:
        print("No league id. Pass --league-id, or set meta.sleeper_league_id in "
              "config/league.yaml.")
        return 1

    _print_header(f"VERIFY LEAGUE {league_id} AGAINST config/league.yaml")
    try:
        payload = SleeperSource().fetch_league_settings(str(league_id))
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print("\nThis command needs network access to api.sleeper.app.")
        return 1

    configured = {**cfg.scoring.stat_weights, **cfg.scoring.bonuses}
    differences = diff_scoring(payload, configured)
    if differences:
        print("\nDIFFERENCES FOUND - config/league.yaml does NOT match Sleeper:")
        for difference in differences:
            print(f"  x {difference}")
        print("\nEvery valuation is downstream of these numbers. Fix the YAML and rerun.")
        return 1

    print("\nScoring settings match. ")
    settings = payload.get("settings", {})
    print(f"Sleeper reports {payload.get('total_rosters')} teams "
          f"(config says {cfg.league.teams}).")
    return 0


def _find_board(cfg) -> Path | None:
    for name in ("draft_board.csv", "SYNTHETIC_draft_board.csv"):
        path = cfg.paths["outputs"] / name
        if path.exists():
            return path
    return None


def _read_names(value: str | None) -> list[str]:
    """Names from a comma-separated string or a newline-delimited file."""
    if not value:
        return []
    path = Path(value)
    if path.exists():
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [part.strip() for part in value.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="NBA fantasy analytics engine for Sleeper Lock-In leagues.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-config", help="validate config and show resolved scoring rules").set_defaults(func=cmd_check_config)
    sub.add_parser("scoring-check", help="print reference stat lines to verify against Sleeper").set_defaults(func=cmd_scoring_check)

    ingest = sub.add_parser("ingest", help="download raw game logs (needs network)")
    ingest.add_argument("--season", required=True, help='e.g. "2025-26"')
    ingest.add_argument("--source", default="nba_api", choices=["nba_api", "csv"])
    ingest.set_defaults(func=cmd_ingest)

    sub.add_parser(
        "fetch-players", help="download player ages/positions from Sleeper (needs internet)"
    ).set_defaults(func=cmd_fetch_players)

    fetch_sched = sub.add_parser(
        "fetch-schedule", help="download the NBA schedule (needs internet)"
    )
    fetch_sched.add_argument("--season", default="2026-27")
    fetch_sched.set_defaults(func=cmd_fetch_schedule)

    demo = sub.add_parser("demo", help="run the full pipeline on synthetic data (no network)")
    demo.add_argument("--seasons", nargs="*", help="default: three seasons, so backtest runs")
    demo.add_argument("--players", type=int, default=180)
    demo.add_argument("--verbose", action="store_true")
    demo.set_defaults(func=cmd_demo)

    board = sub.add_parser("build-board", help="build the draft board from data/raw/")
    board.add_argument("--seasons", nargs="*", help="defaults to the last three seasons")
    board.add_argument("--players", help="player metadata CSV (default: data/external/players.csv)")
    board.add_argument("--schedule", help="schedule CSV (default: data/external/schedule_*.csv)")
    board.add_argument("--verbose", action="store_true")
    board.set_defaults(func=cmd_build_board)

    draft = sub.add_parser("draft", help="live draft assistant")
    draft.add_argument("--pick", type=int, required=True, help="current overall pick number")
    draft.add_argument("--slot", type=int, help="your draft slot (1..teams)")
    draft.add_argument("--drafted", help="comma-separated names, or a file of names")
    draft.add_argument("--roster", help="your roster so far: comma-separated positions or a file")
    draft.add_argument("--board", help="path to draft_board.csv")
    draft.add_argument("--top", type=int, default=5)
    draft.add_argument("--simulations", type=int, default=500)
    draft.set_defaults(func=cmd_draft)

    avail = sub.add_parser("availability", help="who is likely to reach each of your picks")
    avail.add_argument("--slot", type=int)
    avail.add_argument("--through", type=int, default=60, help="only picks up to this number")
    avail.add_argument("--board")
    avail.add_argument("--top", type=int, default=40)
    avail.add_argument("--simulations", type=int, default=1000)
    avail.set_defaults(func=cmd_availability)

    back = sub.add_parser("backtest", help="validate the model against naive baselines")
    back.add_argument("--seasons", nargs="*")
    back.add_argument("--players", help="player metadata CSV")
    back.add_argument("--target-season", help="season to predict (default: latest)")
    back.add_argument("--lockin-season", help="restrict the Lock-In backtest to one season")
    back.add_argument("--output", help="write metrics to this CSV")
    back.add_argument("--tune", action="store_true",
                      help="grid-search projection weights (handoff sec.6)")
    back.set_defaults(func=cmd_backtest)

    sub.add_parser("data-help", help="where to get data and what format it needs").set_defaults(func=cmd_data_help)

    verify = sub.add_parser("verify-league", help="diff config/league.yaml against Sleeper (needs network)")
    verify.add_argument("--league-id")
    verify.set_defaults(func=cmd_verify_league)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
