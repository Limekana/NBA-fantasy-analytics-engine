"""Backtesting tests.

Handoff §20 makes backtesting mandatory. These tests check the machinery is
trustworthy - above all that it cannot see the future, since a leaky backtest is
worse than no backtest: it manufactures confidence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest import (
    WEIGHT_PROFILES,
    backtest_lockin_strategies,
    backtest_projections,
    score_predictions,
    tune_projection_weights,
)
from src.config import load_config
from src.ingestion.synthetic import generate_multi_season, generate_players
from src.scoring import ScoringEngine

SEASONS = ("2023-24", "2024-25", "2025-26")


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def multi_season():
    players = generate_players(60, seed=555)
    logs = generate_multi_season(players, SEASONS, seed=555)
    logs = logs.merge(players[["player_id", "position"]], on="player_id", how="left")
    scored = ScoringEngine(load_config().scoring).score_dataframe(logs)
    return players, scored


# =========================================================================
# Metrics
# =========================================================================

def test_perfect_prediction_scores_perfectly():
    values = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    metrics = score_predictions("perfect", values, values)
    assert metrics.spearman == pytest.approx(1.0)
    assert metrics.mae == pytest.approx(0.0)


def test_inverted_prediction_scores_minus_one():
    predicted = pd.Series([50.0, 40.0, 30.0, 20.0, 10.0])
    actual = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    assert score_predictions("inverted", predicted, actual).spearman == pytest.approx(-1.0)


def test_metrics_ignore_a_constant_offset_in_rank_terms():
    """Drafting is a ranking problem: a uniformly low projection still ranks well."""
    actual = pd.Series([10.0, 20.0, 30.0, 40.0])
    predicted = actual * 0.5
    metrics = score_predictions("scaled", predicted, actual)
    assert metrics.spearman == pytest.approx(1.0)
    assert metrics.mae > 0


def test_top_n_hit_rate_is_computed():
    predicted = pd.Series([100.0, 90.0, 80.0, 10.0, 5.0])
    actual = pd.Series([100.0, 90.0, 5.0, 10.0, 80.0])
    metrics = score_predictions("partial", predicted, actual, top_ns=(2,))
    assert metrics.top_n_hit_rate[2] == pytest.approx(1.0)


def test_too_few_players_returns_nan_rather_than_crashing():
    metrics = score_predictions("tiny", pd.Series([1.0]), pd.Series([1.0]))
    assert np.isnan(metrics.spearman)


# =========================================================================
# No lookahead - the property that makes a backtest worth anything
# =========================================================================

def test_projection_backtest_requires_a_prior_season(cfg, multi_season):
    players, scored = multi_season
    earliest = min(scored["season"])
    with pytest.raises(ValueError, match="no seasons before"):
        backtest_projections(scored, players, cfg, earliest)


def test_projection_backtest_rejects_an_absent_season(cfg, multi_season):
    players, scored = multi_season
    with pytest.raises(ValueError, match="not present"):
        backtest_projections(scored, players, cfg, "2099-00")


def test_projection_backtest_ignores_target_season_data(cfg, multi_season):
    """The decisive test: corrupting the target season must not move the
    predictions at all. If it does, the backtest is leaking."""
    players, scored = multi_season
    target = "2025-26"

    _metrics_a, comparison_a = backtest_projections(scored, players, cfg, target)

    corrupted = scored.copy()
    mask = corrupted["season"] == target
    for column in ("points", "rebounds", "assists", "fantasy_points"):
        corrupted.loc[mask, column] = corrupted.loc[mask, column] * 10.0
    _metrics_b, comparison_b = backtest_projections(corrupted, players, cfg, target)

    joined = comparison_a[["model_fp_game"]].join(
        comparison_b[["model_fp_game"]], rsuffix="_corrupt", how="inner"
    )
    assert len(joined) > 0
    # Predictions identical; only the actuals changed.
    assert np.allclose(joined["model_fp_game"], joined["model_fp_game_corrupt"])


def test_projection_backtest_returns_model_and_baselines(cfg, multi_season):
    players, scored = multi_season
    metrics, comparison = backtest_projections(scored, players, cfg, "2025-26")
    names = {m.name for m in metrics}
    assert "model" in names
    assert "baseline_prior_fp_game" in names
    assert not comparison.empty


def test_baselines_are_genuinely_predictive(cfg, multi_season):
    """Sanity: last season's FP/game must correlate strongly with this season's.
    If this fails, the synthetic generator is broken, not the model."""
    players, scored = multi_season
    metrics, _ = backtest_projections(scored, players, cfg, "2025-26")
    baseline = next(m for m in metrics if m.name == "baseline_prior_fp_game")
    assert baseline.spearman > 0.5


def test_model_is_competitive_with_the_naive_baseline(cfg, multi_season):
    """A guard against regressions that quietly break the projection.

    Deliberately NOT asserting the model wins - on synthetic data whose
    year-over-year drift is largely noise, matching the baseline is the honest
    expected result. This asserts only that the model has not become badly worse,
    which would indicate a bug.
    """
    players, scored = multi_season
    metrics, _ = backtest_projections(scored, players, cfg, "2025-26")
    model = next(m for m in metrics if m.name == "model")
    baseline = next(m for m in metrics if m.name == "baseline_prior_fp_game")
    assert model.spearman > baseline.spearman - 0.05


def test_model_beats_the_minutes_only_baseline(cfg, multi_season):
    """Minutes alone is a pure opportunity proxy. Losing to it would mean the
    production model contributes nothing."""
    players, scored = multi_season
    metrics, _ = backtest_projections(scored, players, cfg, "2025-26")
    model = next(m for m in metrics if m.name == "model")
    minutes = next(m for m in metrics if m.name == "baseline_prior_minutes")
    assert model.spearman > minutes.spearman


# =========================================================================
# Lock-In strategy backtest on real weekly sequences
# =========================================================================

def test_lockin_backtest_runs_and_orders_strategies(cfg, multi_season):
    _players, scored = multi_season
    result = backtest_lockin_strategies(scored, cfg, season="2025-26")
    per_week = result.strategy_per_week

    # Perfect foresight bounds everything; doing nothing floors everything.
    assert per_week["perfect"] >= max(v for k, v in per_week.items() if k != "perfect")
    assert per_week["last_game"] <= min(v for k, v in per_week.items() if k != "last_game")


def test_optimal_stopping_wins_the_lockin_backtest(cfg, multi_season):
    """On real chronological weeks - no resampling, no distribution assumed -
    optimal stopping should beat every other realistic policy."""
    _players, scored = multi_season
    result = backtest_lockin_strategies(scored, cfg, season="2025-26")
    per_week = result.strategy_per_week
    realistic = {k: v for k, v in per_week.items() if k not in ("perfect",)}
    assert max(realistic, key=realistic.get) == "optimal_iid"


def test_secretary_rule_underperforms_on_real_weeks(cfg, multi_season):
    """The 37% rule replayed over actual weekly sequences. It beats doing
    nothing but captures well under the full available edge."""
    _players, scored = multi_season
    result = backtest_lockin_strategies(scored, cfg, season="2025-26")
    per_week = result.strategy_per_week

    floor_value = per_week["last_game"]
    optimal = per_week["optimal_iid"]
    secretary = per_week["secretary"]

    assert secretary > floor_value           # better than nothing
    assert secretary < optimal               # but clearly beaten
    captured = (secretary - floor_value) / (optimal - floor_value)
    assert 0.2 < captured < 0.75


def test_lockin_backtest_needs_enough_data(cfg, multi_season):
    _players, scored = multi_season
    with pytest.raises(ValueError):
        backtest_lockin_strategies(scored, cfg, season="2025-26", min_games=10_000)


def test_lockin_backtest_counts_weeks_and_players(cfg, multi_season):
    _players, scored = multi_season
    result = backtest_lockin_strategies(scored, cfg, season="2025-26")
    assert result.n_weeks > 0
    assert result.n_players > 0
    frame = result.to_frame()
    assert "gap_vs_best" in frame.columns
    assert frame["gap_vs_best"].max() == pytest.approx(0.0)


# =========================================================================
# Weight tuning
# =========================================================================

def test_tuning_grid_evaluates_every_profile(cfg, multi_season):
    players, scored = multi_season
    grid = tune_projection_weights(
        scored, players, cfg, "2025-26", shrinkage_values=(0, 20)
    )
    assert not grid.empty
    assert set(grid["profile"]) <= set(WEIGHT_PROFILES)
    assert grid["spearman"].is_monotonic_decreasing   # sorted best-first
