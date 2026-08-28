"""Lock-In simulator tests.

Handoff sec.11 calls this "one of the most important components", so the tests
pin down both the mechanics (does the week walk work?) and the economics (does
the valuation actually reward what Lock-In rewards?).
"""
from __future__ import annotations

import numpy as np
import pytest

from src.config import load_config
from src.lockin import (
    LockInSimulator,
    OptimalIIDStrategy,
    PercentileStrategy,
    PlayerContext,
    ThresholdStrategy,
    continuation_values,
    reconstruct_weeks,
    simulate_week,
)


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def sim(cfg):
    return LockInSimulator(cfg.model, cfg.league.lock_in, rng=np.random.default_rng(7))


def ctx_for(sample) -> PlayerContext:
    arr = np.asarray(sample, dtype=float)
    return PlayerContext(distribution=arr, continuation_values=continuation_values(arr))


# =========================================================================
# Week mechanics
# =========================================================================

def test_the_handoff_example_week():
    """Handoff sec.10: Mon 34, Wed 47, Fri 39, Sun 51 -> perfect lock is 51."""
    scores = [34, 47, 39, 51]
    assert max(scores) == 51


def test_threshold_locks_the_first_qualifying_game():
    scores = [34, 47, 39, 51]
    result = simulate_week(scores, ThresholdStrategy(threshold=40), ctx_for(scores))
    assert result.locked_value == 47
    assert result.locked_index == 1
    assert result.regret == 4  # 51 was still to come


def test_threshold_too_high_falls_through_to_auto_lock():
    """Never locking means Sleeper takes the FINAL game, not the best one."""
    scores = [34, 47, 39, 51]
    result = simulate_week(scores, ThresholdStrategy(threshold=99), ctx_for(scores))
    assert result.locked_value == 51
    assert result.locked_index == 3


def test_auto_lock_fallback_takes_last_game_even_when_it_is_bad():
    """The verified Sleeper rule, and the reason inattention is costly."""
    scores = [55, 20, 12]
    result = simulate_week(scores, ThresholdStrategy(threshold=99), ctx_for(scores))
    assert result.locked_value == 12
    assert result.regret == 43


def test_auto_lock_fallback_is_configurable():
    scores = [55, 20, 12]
    result = simulate_week(
        scores, ThresholdStrategy(threshold=99), ctx_for(scores), auto_lock_fallback="first_game"
    )
    assert result.locked_value == 55


def test_final_game_is_never_offered_as_a_decision():
    """With no games remaining the choice is forced, so a strategy that would
    decline must still end up with the last game."""
    scores = [10, 20, 30]
    never = ThresholdStrategy(threshold=1000)
    assert simulate_week(scores, never, ctx_for(scores)).locked_index == 2


def test_single_game_week_returns_that_game(sim):
    scores = [41.0]
    assert simulate_week(scores, ThresholdStrategy(10), ctx_for(scores)).locked_value == 41.0


def test_empty_week_is_zero():
    result = simulate_week([], ThresholdStrategy(10), ctx_for([1.0]))
    assert result.locked_value == 0.0
    assert result.scores == ()


def test_percentile_strategy_is_self_calibrating():
    """The same 45 FP game is a lock for a modest player and not for a star."""
    modest = ctx_for(np.full(100, 25.0) + np.arange(100) * 0.1)
    elite = ctx_for(np.full(100, 55.0) + np.arange(100) * 0.1)
    strategy = PercentileStrategy(percentile=70)
    assert strategy.decide(45.0, 2, modest) is True
    assert strategy.decide(45.0, 2, elite) is False


# =========================================================================
# Optimal stopping
# =========================================================================

def test_continuation_values_increase_with_games_remaining():
    """More games left == more valuable to keep waiting."""
    values = continuation_values(np.random.default_rng(3).normal(35, 10, 500), max_games=6)
    assert values[1] < values[2] < values[3] < values[4]


def test_continuation_value_with_one_game_left_is_the_mean():
    sample = np.array([10.0, 20.0, 30.0, 40.0])
    assert continuation_values(sample)[1] == pytest.approx(25.0)


def test_optimal_strategy_gets_greedier_as_the_week_runs_out():
    """A 40 FP game worth passing on Monday is worth locking on Saturday."""
    sample = np.random.default_rng(5).normal(35, 12, 500)
    ctx = ctx_for(sample)
    strategy = OptimalIIDStrategy()
    assert strategy.decide(40.0, 3, ctx) is False   # plenty of chances left
    assert strategy.decide(40.0, 1, ctx) is True    # last chance to beat the mean


def test_optimal_strategy_beats_naive_threshold_on_average(sim):
    sample = np.random.default_rng(11).normal(35, 12, 400)
    optimal = sim.expected_weekly_value(sample, 4, "optimal_iid")
    threshold = sim.expected_weekly_value(sample, 4, "threshold")
    assert optimal >= threshold


# =========================================================================
# Valuation economics - the properties that make Lock-In different
# =========================================================================

def test_perfect_is_an_upper_bound_on_every_realistic_strategy(sim):
    """Handoff sec.2: never present clairvoyance without labelling it."""
    sample = np.random.default_rng(13).normal(35, 12, 400)
    perfect = sim.expected_weekly_value(sample, 4, "perfect")
    for name in ("optimal_iid", "threshold", "percentile", "last_game"):
        assert sim.expected_weekly_value(sample, 4, name) <= perfect + 1e-9


def test_last_game_is_a_lower_bound_and_equals_the_mean(sim):
    """Doing nothing scores the player's raw average - no Lock-In benefit at all."""
    sample = np.random.default_rng(17).normal(35, 12, 400)
    assert sim.expected_weekly_value(sample, 4, "last_game") == pytest.approx(float(np.mean(sample)))


def test_more_games_per_week_is_worth_more(sim):
    """The central schedule effect: extra games are extra draws to choose from."""
    sample = np.random.default_rng(19).normal(35, 10, 400)
    values = [sim.expected_weekly_value(sample, g, "optimal_iid") for g in (1, 2, 3, 4)]
    assert values == sorted(values)
    assert values[3] > values[0]


def test_one_game_week_has_no_lock_in_advantage(sim):
    """With a single game there is no decision to make, so value == mean."""
    sample = np.random.default_rng(23).normal(35, 10, 300)
    assert sim.expected_weekly_value(sample, 1, "optimal_iid") == pytest.approx(float(np.mean(sample)))


def test_volatility_is_an_asset_in_lock_in(sim, cfg):
    """Two players, same mean: the volatile one is worth strictly more.

    This is the headline result that separates a Lock-In model from an ordinary
    FP/G ranking, and the reason the handoff insists on distributions.
    """
    rng = np.random.default_rng(29)
    pmf = cfg.model["schedule"]["fallback_games_per_week_pmf"]
    steady = rng.normal(35, 5, 500)
    volatile = rng.normal(35, 15, 500)
    steady = steady - steady.mean() + 35.0
    volatile = volatile - volatile.mean() + 35.0

    steady_profile = sim.profile("steady", steady, pmf)
    volatile_profile = sim.profile("volatile", volatile, pmf)

    assert steady_profile.mean_fp == pytest.approx(volatile_profile.mean_fp, abs=1e-6)
    assert volatile_profile.lockin_value > steady_profile.lockin_value
    assert volatile_profile.lock_in_advantage > steady_profile.lock_in_advantage


def test_profile_reports_every_configured_strategy(sim, cfg):
    sample = np.random.default_rng(31).normal(35, 10, 300)
    profile = sim.profile("p", sample, cfg.model["schedule"]["fallback_games_per_week_pmf"])
    for name in cfg.model["lockin"]["report_strategies"]:
        assert name in profile.strategy_values


def test_profile_gap_metrics_are_consistent(sim, cfg):
    sample = np.random.default_rng(37).normal(35, 11, 300)
    profile = sim.profile("p", sample, cfg.model["schedule"]["fallback_games_per_week_pmf"])
    assert profile.clairvoyance_gap >= -1e-9
    assert profile.lock_in_advantage == pytest.approx(profile.lockin_value - profile.mean_fp)
    assert profile.decision_value == pytest.approx(profile.lockin_value - profile.auto_value)


def test_empty_distribution_is_handled(sim, cfg):
    profile = sim.profile("ghost", np.array([]), cfg.model["schedule"]["fallback_games_per_week_pmf"])
    assert profile.lockin_value == 0.0
    assert profile.mean_fp == 0.0


# =========================================================================
# Week reconstruction from real game logs
# =========================================================================

def test_reconstruct_weeks_groups_monday_to_sunday():
    # 2025-10-20 is a Monday; 2025-10-26 is the Sunday of the same fantasy week.
    dates = ["2025-10-20", "2025-10-22", "2025-10-26", "2025-10-27"]
    weeks = reconstruct_weeks(dates, [30, 40, 50, 60])
    assert weeks == [[30, 40, 50], [60]]


def test_reconstruct_weeks_sorts_out_of_order_input():
    dates = ["2025-10-26", "2025-10-20", "2025-10-22"]
    assert reconstruct_weeks(dates, [50, 30, 40]) == [[30, 40, 50]]


def test_reconstruct_weeks_empty():
    assert reconstruct_weeks([], []) == []


# =========================================================================
# The vectorised fast path must agree with the reference implementation
# =========================================================================

@pytest.mark.parametrize("games", [2, 3, 4, 5])
@pytest.mark.parametrize("strategy_name", ["optimal_iid", "threshold", "percentile"])
def test_vectorised_lockin_matches_scalar(sim, games, strategy_name):
    """The fast path exists only for speed; it must not change the answer.

    Both paths draw from the same distribution, so they agree up to Monte Carlo
    noise. 20k weeks keeps the standard error well under the 0.5 FP tolerance.
    """
    sample = np.random.default_rng(41).normal(35, 12, 400)
    vectorised = sim._simulate_expected(sample, games, strategy_name, n_weeks=20000)
    reference = sim._simulate_expected_loop(sample, games, strategy_name, n_weeks=20000)
    assert vectorised == pytest.approx(reference, abs=0.5)


def test_vectorised_respects_auto_lock_fallback(cfg):
    """A strategy that never locks must fall through to the configured rule."""
    from src.lockin import LockInSimulator

    sample = np.array([10.0, 20.0, 30.0, 90.0])
    for rule, expected in (("last_game", "last"), ("first_game", "first"), ("best_game", "best")):
        simulator = LockInSimulator(
            {**cfg.model, "lockin": {**cfg.model["lockin"], "threshold_fp": 10_000.0}},
            {"auto_lock_fallback": rule},
            rng=np.random.default_rng(3),
        )
        value = simulator._simulate_expected(sample, 3, "threshold", n_weeks=4000)
        if expected == "best":
            assert value > float(np.mean(sample))
        else:
            # first/last game of an i.i.d. week are both unconditioned draws.
            assert value == pytest.approx(float(np.mean(sample)), abs=2.0)


# =========================================================================
# The 37% / secretary rule - a common suggestion, and the wrong tool here
# =========================================================================

def test_secretary_rule_applied_to_the_handoff_example():
    """Week [34, 47, 39, 51]: observe floor(4/e)=1 game, then take the first
    game beating 34. That is 47 - and it forfeits the 51 that came later."""
    from src.lockin import simulate_secretary_week

    assert simulate_secretary_week([34, 47, 39, 51]) == 47.0


def test_secretary_rule_is_degenerate_in_a_two_game_week():
    """floor(2/e) = 0, so there is no observation phase at all: it locks the
    first game unconditionally, which is just an unconditioned draw."""
    from src.lockin import simulate_secretary_week

    assert simulate_secretary_week([10.0, 90.0]) == 10.0


def test_secretary_rule_falls_through_to_auto_lock():
    from src.lockin import simulate_secretary_week

    # Nothing after the observation phase beats 90, so the auto-lock applies.
    assert simulate_secretary_week([90.0, 10.0, 20.0, 30.0]) == 30.0


@pytest.mark.parametrize("games", [2, 3, 4, 5])
def test_secretary_rule_loses_to_optimal_stopping(sim, games):
    """The 37% rule maximises P(picking the single best game).

    Lock-In pays expected points, not a prize for picking the maximum, and the
    player's distribution is known rather than hidden. Optimal stopping uses both
    facts; the secretary rule discards them, and measurably loses.
    """
    sample = np.random.default_rng(101).normal(35, 12, 500)
    optimal = sim._simulate_expected(sample, games, "optimal_iid", n_weeks=30000)
    secretary = sim._simulate_expected(sample, games, "secretary", n_weeks=30000)
    assert optimal > secretary


def test_secretary_rule_captures_less_than_half_the_available_edge(sim):
    """Quantifies the cost: the gap between never-locking and optimal is the
    prize; the 37% rule collects under half of it."""
    sample = np.random.default_rng(103).normal(35, 12, 500)
    floor_value = sim.expected_weekly_value(sample, 4, "last_game")
    optimal = sim._simulate_expected(sample, 4, "optimal_iid", n_weeks=30000)
    secretary = sim._simulate_expected(sample, 4, "secretary", n_weeks=30000)

    available = optimal - floor_value
    captured = secretary - floor_value
    assert 0.0 < captured < 0.6 * available


def test_secretary_strategy_rejects_the_stateless_interface():
    """It is history-dependent, so it must not silently pretend otherwise."""
    from src.lockin import SecretaryStrategy

    strategy = SecretaryStrategy()
    with pytest.raises(NotImplementedError):
        strategy.decide(40.0, 2, ctx_for([30.0, 40.0]))
