"""Ingestion, valuation and end-to-end pipeline tests."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.adp import consensus_adp, join_adp, normalise_name
from src.config import load_config
from src.distributions import build_profiles
from src.ingestion.base import validate_game_logs
from src.ingestion.csv_source import CSVSource, coerce_types, normalise_columns
from src.ingestion.synthetic import generate_game_logs, generate_players
from src.pipeline import run_pipeline
from src.projections import compute_per36, project_games_played, project_players
from src.schedule import expected_games_per_week, games_per_week_by_team
from src.scoring import ScoringEngine
from src.valuation import build_valuations, linear_score, rescale_distribution


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def synthetic():
    players = generate_players(40, seed=99)
    logs = generate_game_logs(players, season="2025-26", seed=99)
    logs = logs.merge(players[["player_id", "position"]], on="player_id", how="left")
    return players, logs


# =========================================================================
# Ingestion / column normalisation
# =========================================================================

def test_nba_api_style_columns_normalise():
    raw = pd.DataFrame([{
        "PLAYER_NAME": "A Player", "GAME_DATE": "2025-10-20", "TEAM_ABBREVIATION": "BOS",
        "MATCHUP": "BOS @ NYK", "MIN": 34.5, "PTS": 30, "REB": 10, "AST": 11,
        "STL": 2, "BLK": 1, "TOV": 3, "PF": 2, "FTM": 6, "FG3M": 4,
    }])
    out = coerce_types(normalise_columns(raw), "2025-26")
    assert out["points"].iloc[0] == 30
    assert out["opponent"].iloc[0] == "NYK"
    assert bool(out["home"].iloc[0]) is False


def test_basketball_reference_style_columns_normalise():
    raw = pd.DataFrame([{
        "Player": "B Player", "Date": "2025-10-20", "Tm": "LAL", "Opp": "GSW",
        "MP": "31:30", "PTS": 20, "TRB": 5, "AST": 4, "STL": 1, "BLK": 0,
        "TOV": 2, "PF": 3, "FT": 5, "3P": 2,
    }])
    out = coerce_types(normalise_columns(raw), "2025-26")
    assert out["minutes"].iloc[0] == pytest.approx(31.5)
    assert out["rebounds"].iloc[0] == 5


def test_mmss_minutes_are_parsed():
    raw = pd.DataFrame([{"Player": "X", "MIN": "34:12", "PTS": 10}])
    out = coerce_types(normalise_columns(raw), "2025-26")
    assert out["minutes"].iloc[0] == pytest.approx(34.2, abs=0.01)


def test_home_flag_from_vs_matchup():
    raw = pd.DataFrame([{"Player": "X", "MATCHUP": "BOS vs. NYK", "PTS": 10}])
    out = coerce_types(normalise_columns(raw), "2025-26")
    assert bool(out["home"].iloc[0]) is True


def test_missing_season_directory_raises_a_helpful_error(tmp_path):
    source = CSVSource(tmp_path)
    with pytest.raises(FileNotFoundError, match="2099-00"):
        source.fetch_game_logs("2099-00")


def test_validate_game_logs_flags_negative_stats():
    frame = pd.DataFrame([{"player_id": "A", "game_id": "1", "points": -5}])
    assert any("negative points" in p for p in validate_game_logs(frame))


def test_validate_game_logs_flags_duplicates():
    frame = pd.DataFrame([
        {"player_id": "A", "game_id": "1", "points": 5},
        {"player_id": "A", "game_id": "1", "points": 5},
    ])
    assert any("duplicate" in p for p in validate_game_logs(frame))


def test_csv_source_round_trip(tmp_path, synthetic):
    _players, logs = synthetic
    directory = tmp_path / "2025-26"
    directory.mkdir(parents=True)
    logs.to_csv(directory / "logs.csv", index=False)
    loaded = CSVSource(tmp_path).fetch_game_logs("2025-26")
    assert len(loaded) == len(logs)


# =========================================================================
# Synthetic data is well-formed and clearly labelled
# =========================================================================

def test_synthetic_data_is_flagged(synthetic):
    _players, logs = synthetic
    assert logs["is_synthetic"].all()


def test_synthetic_stats_are_non_negative(synthetic):
    _players, logs = synthetic
    for column in ("points", "rebounds", "assists", "steals", "blocks", "minutes"):
        assert (logs[column] >= 0).all()


def test_synthetic_data_is_deterministic():
    a = generate_game_logs(generate_players(10, seed=5), seed=5)
    b = generate_game_logs(generate_players(10, seed=5), seed=5)
    pd.testing.assert_frame_equal(a, b)


# =========================================================================
# Projections
# =========================================================================

def test_per36_rates_are_scaled_correctly():
    logs = pd.DataFrame([
        {"player_id": "A", "season": "2025-26", "minutes": 36.0, "points": 20.0,
         "rebounds": 5.0, "assists": 5.0, "steals": 1.0, "blocks": 1.0,
         "turnovers": 2.0, "personal_fouls": 2.0, "free_throws_made": 4.0,
         "three_pointers_made": 2.0},
    ])
    per36 = compute_per36(logs)
    assert per36["points_per36"].iloc[0] == pytest.approx(20.0)


def test_per36_handles_zero_minutes():
    logs = pd.DataFrame([
        {"player_id": "A", "season": "2025-26", "minutes": 0.0, "points": 0.0,
         "rebounds": 0.0, "assists": 0.0, "steals": 0.0, "blocks": 0.0,
         "turnovers": 0.0, "personal_fouls": 0.0, "free_throws_made": 0.0,
         "three_pointers_made": 0.0},
    ])
    assert compute_per36(logs)["points_per36"].iloc[0] == 0.0


def test_projections_respect_minute_bounds(cfg, synthetic):
    players, logs = synthetic
    projections = project_players(compute_per36(logs), players, cfg.model, cfg.assumptions)
    bounds = cfg.model["projection"]["minutes"]
    for projection in projections:
        assert bounds["min_minutes"] <= projection.projected_minutes <= bounds["max_minutes"]


def test_projections_record_their_assumptions(cfg, synthetic):
    players, logs = synthetic
    projections = project_players(compute_per36(logs), players, cfg.model, cfg.assumptions)
    # Rule 7: assumptions must be visible, not buried.
    assert any(p.assumption_notes for p in projections)


def test_role_minutes_assumption_is_applied(cfg, synthetic):
    """An explicit override in assumptions.yaml must move the projection."""
    players, logs = synthetic
    name = players["player_name"].iloc[0]
    per36 = compute_per36(logs)

    base = {p.player_name: p for p in project_players(per36, players, cfg.model, cfg.assumptions)}
    overridden = dict(cfg.assumptions)
    overridden["role_minutes"] = {name: {"minutes": 38.0, "note": "test"}}
    after = {p.player_name: p for p in project_players(per36, players, cfg.model, overridden)}

    assert after[name].projected_minutes != base[name].projected_minutes
    assert "ASSUMPTION" in after[name].assumption_notes


def test_excluded_players_are_dropped(cfg, synthetic):
    players, logs = synthetic
    name = players["player_name"].iloc[0]
    assumptions = dict(cfg.assumptions)
    assumptions["exclude"] = [name]
    projections = project_players(compute_per36(logs), players, cfg.model, assumptions)
    assert name not in {p.player_name for p in projections}


# =========================================================================
# Games played
# =========================================================================

def test_games_played_is_bounded(cfg):
    projection = project_games_played("X", 27, [1.0, 1.0, 1.0], cfg.model)
    assert 0 <= projection.expected_games <= 82


def test_games_played_orders_floor_median_ceiling(cfg):
    projection = project_games_played("X", 27, [0.8], cfg.model)
    assert projection.games_floor <= projection.games_median <= projection.games_ceiling


def test_games_played_regresses_toward_the_baseline(cfg):
    """A single 82-game season must not project 82 games again (handoff sec.7)."""
    projection = project_games_played("X", 28, [1.0], cfg.model)
    assert projection.expected_games < 82


def test_older_players_project_fewer_games(cfg):
    young = project_games_played("Y", 24, [], cfg.model)
    old = project_games_played("O", 36, [], cfg.model)
    assert old.expected_games < young.expected_games


def test_injury_assumption_reduces_games_and_is_recorded(cfg):
    healthy = project_games_played("X", 27, [0.9], cfg.model)
    injured = project_games_played(
        "X", 27, [0.9], cfg.model,
        {"games_missed": 20, "note": "surgery, reported 2026-08"},
    )
    assert injured.expected_games < healthy.expected_games
    assert "ASSUMPTION" in injured.assumption_notes
    assert "surgery" in injured.assumption_notes


# =========================================================================
# Valuation
# =========================================================================

def test_linear_score_matches_engine_without_bonuses(cfg):
    engine = ScoringEngine(cfg.scoring)
    stats = {"points": 20, "rebounds": 5, "assists": 4, "turnovers": 2}
    assert linear_score(stats, engine) == pytest.approx(engine.score_game(stats))


def test_rescale_distribution_hits_the_target_mean():
    sample = np.array([10.0, 20.0, 30.0, 60.0])
    rescaled = rescale_distribution(sample, 50.0)
    assert float(np.mean(rescaled)) == pytest.approx(50.0)


def test_rescale_distribution_preserves_shape():
    """Lock-In value depends on skew, so rescaling must not flatten it."""
    sample = np.array([10.0, 12.0, 15.0, 80.0])
    rescaled = rescale_distribution(sample, 60.0)
    cv_before = sample.std() / sample.mean()
    cv_after = rescaled.std() / rescaled.mean()
    assert cv_after == pytest.approx(cv_before)


def test_rescale_handles_empty_and_zero():
    assert rescale_distribution(np.array([]), 30.0).size == 0
    assert float(np.mean(rescale_distribution(np.zeros(5), 30.0))) == pytest.approx(30.0)


def test_bonus_projection_beats_scoring_the_average_line(cfg, synthetic):
    """The central non-linearity: bonuses must not be priced at zero.

    A player who averages 26 points never crosses 40 on their mean line, but does
    collect 40-point bonuses in real games. Scoring the average line loses that.
    """
    players, logs = synthetic
    engine = ScoringEngine(cfg.scoring)
    scored = engine.score_dataframe(logs)
    profiles = build_profiles(scored, cfg.model)
    projections = project_players(compute_per36(logs), players, cfg.model, cfg.assumptions)
    games = {p.player_id: project_games_played(p.player_id, p.age, [0.85], cfg.model) for p in projections}

    valuations = build_valuations(profiles, projections, games, {}, cfg.league, cfg.model, engine)
    top = max(valuations, key=lambda v: v.projected_fp_per_game)

    naive = engine.score_game(dict(top.__dict__.get("projected_stats", {})))
    assert top.projected_bonus_fp > 0
    assert top.projected_fp_per_game > top.projected_base_fp


def test_valuation_carries_lock_in_metrics(cfg, synthetic):
    players, logs = synthetic
    engine = ScoringEngine(cfg.scoring)
    scored = engine.score_dataframe(logs)
    profiles = build_profiles(scored, cfg.model)
    projections = project_players(compute_per36(logs), players, cfg.model, cfg.assumptions)
    games = {p.player_id: project_games_played(p.player_id, p.age, [0.85], cfg.model) for p in projections}
    valuations = build_valuations(profiles, projections, games, {}, cfg.league, cfg.model, engine)

    for valuation in valuations:
        assert valuation.lockin_perfect_weekly >= valuation.lockin_weekly_value - 1e-6
        assert valuation.lockin_weekly_value >= valuation.lockin_auto_weekly - 1e-6


# =========================================================================
# ADP
# =========================================================================

def test_name_normalisation_matches_accents_and_suffixes():
    assert normalise_name("Nikola Jokić") == normalise_name("Nikola Jokic")
    assert normalise_name("Jaren Jackson Jr.") == normalise_name("Jaren Jackson")


def test_consensus_uses_the_median_across_sources():
    adp = pd.DataFrame([
        {"player_name": "A", "adp": 10, "source": "s1", "name_key": "a"},
        {"player_name": "A", "adp": 12, "source": "s2", "name_key": "a"},
        {"player_name": "A", "adp": 50, "source": "s3", "name_key": "a"},   # outlier
    ])
    consensus = consensus_adp(adp)
    assert consensus["adp"].iloc[0] == 12
    assert consensus["adp_spread"].iloc[0] == 40


def test_join_adp_flags_value_correctly():
    board = pd.DataFrame([
        {"player_name": "Bargain", "model_rank": 25},
        {"player_name": "Reach", "model_rank": 32},
        {"player_name": "Fair", "model_rank": 40},
    ])
    consensus = pd.DataFrame([
        {"name_key": "bargain", "adp": 42.0, "adp_sources": 2, "adp_spread": 3.0},
        {"name_key": "reach", "adp": 24.0, "adp_sources": 2, "adp_spread": 2.0},
        {"name_key": "fair", "adp": 41.0, "adp_sources": 2, "adp_spread": 1.0},
    ])
    joined = join_adp(board, consensus).set_index("player_name")
    # Handoff sec.13: model 25 / ADP 42 is the market inefficiency to flag.
    assert joined.loc["Bargain", "value_flag"] == "undervalued"
    assert joined.loc["Reach", "value_flag"] == "overvalued"
    assert joined.loc["Fair", "value_flag"] == "fairly_valued"


def test_join_adp_handles_no_adp():
    board = pd.DataFrame([{"player_name": "A", "model_rank": 1}])
    joined = join_adp(board, pd.DataFrame())
    assert joined["value_flag"].iloc[0] == "no_adp"


# =========================================================================
# Schedule
# =========================================================================

def test_games_per_week_pmf_sums_to_one(synthetic):
    _players, logs = synthetic
    pmfs = games_per_week_by_team(logs[["game_date", "team"]].drop_duplicates())
    for pmf in pmfs.values():
        assert sum(pmf.values()) == pytest.approx(1.0)


def test_expected_games_per_week_is_plausible(synthetic):
    _players, logs = synthetic
    pmfs = games_per_week_by_team(logs[["game_date", "team"]].drop_duplicates())
    for pmf in pmfs.values():
        assert 1.5 <= expected_games_per_week(pmf) <= 4.5


# =========================================================================
# End-to-end
# =========================================================================

def test_pipeline_runs_end_to_end(cfg, tmp_path, synthetic):
    players, logs = synthetic
    directory = tmp_path / "2025-26"
    directory.mkdir(parents=True)
    logs.to_csv(directory / "logs.csv", index=False)

    result = run_pipeline(cfg, ["2025-26"], raw_root=tmp_path, players=players)

    assert not result.board.empty
    assert result.is_synthetic is True
    assert any("SYNTHETIC" in w for w in result.warnings)
    assert result.board["model_rank"].is_monotonic_increasing


def test_pipeline_board_is_sorted_by_value(cfg, tmp_path, synthetic):
    players, logs = synthetic
    directory = tmp_path / "2025-26"
    directory.mkdir(parents=True)
    logs.to_csv(directory / "logs.csv", index=False)
    result = run_pipeline(cfg, ["2025-26"], raw_root=tmp_path, players=players)
    values = result.board["projected_season_value"].to_numpy()
    assert np.all(np.diff(values) <= 1e-6)


def test_pipeline_warns_when_no_adp_is_loaded(cfg, tmp_path, synthetic):
    players, logs = synthetic
    directory = tmp_path / "2025-26"
    directory.mkdir(parents=True)
    logs.to_csv(directory / "logs.csv", index=False)
    result = run_pipeline(cfg, ["2025-26"], raw_root=tmp_path, players=players)
    assert any("ADP" in w for w in result.warnings)
