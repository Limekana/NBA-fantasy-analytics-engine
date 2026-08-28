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


def cmd_demo(args) -> int:
    """Generate synthetic data and run the whole pipeline on it.

    Proves the pipeline works end to end without any network access. The output
    is fictional and is labelled as such everywhere it appears.
    """
    from src.ingestion.synthetic import write_synthetic_season

    cfg = load_config()
    _print_header("SYNTHETIC END-TO-END DEMO")
    print("\n*** SYNTHETIC DATA - NOT REAL PLAYERS, NOT A REAL PROJECTION ***\n")

    season = args.season or "2025-26"
    path, players, logs = write_synthetic_season(RAW_DIR, season, n_players=args.players)
    print(f"Generated {len(players)} players / {len(logs):,} game rows -> {path}")

    return _run_and_report(cfg, [season], players=players, verbose=args.verbose)


def cmd_build_board(args) -> int:
    """Run the pipeline on whatever real data is in data/raw/."""
    cfg = load_config()
    _print_header("BUILD DRAFT BOARD")
    seasons = args.seasons or ["2025-26", "2024-25", "2023-24"]
    players = pd.read_csv(args.players) if args.players else None
    schedule = pd.read_csv(args.schedule) if args.schedule else None
    if schedule is not None:
        schedule["game_date"] = pd.to_datetime(schedule["game_date"])
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

    demo = sub.add_parser("demo", help="run the full pipeline on synthetic data (no network)")
    demo.add_argument("--season", default="2025-26")
    demo.add_argument("--players", type=int, default=180)
    demo.add_argument("--verbose", action="store_true")
    demo.set_defaults(func=cmd_demo)

    board = sub.add_parser("build-board", help="build the draft board from data/raw/")
    board.add_argument("--seasons", nargs="*", help="defaults to the last three seasons")
    board.add_argument("--players", help="optional player metadata CSV (age, position)")
    board.add_argument("--schedule", help="optional 2026-27 schedule CSV")
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
