"""Bonus interaction tests.

Handoff sec.4: "Do not silently assume whether 50+ also receives the 40+ bonus,
triple-double also receives double-double, [or] bonuses interact in any other
way. Write tests for every bonus interaction."

The engine makes each interaction a config switch. These tests pin down BOTH
branches of every switch, so that flipping a value in league.yaml after checking
the real Sleeper UI produces a predictable, already-tested result.

Bonus values under the provisional config:
    double_double   +2      points_40_plus  +3
    triple_double   +3      points_50_plus  +4
                            assists_15_plus +3
"""
from __future__ import annotations

import pytest

from src.config import config_with_overrides
from src.scoring import ScoringEngine
from tests.conftest import stat_line


@pytest.fixture
def engine(scoring):
    return ScoringEngine(scoring)


@pytest.fixture
def no_stack(no_stack_cfg):
    return ScoringEngine(no_stack_cfg.scoring)


def bonus_only(engine, **stats) -> float:
    """Isolate the bonus component of a stat line."""
    return engine.score_game_detailed(stat_line(**stats)).bonus_total


# =========================================================================
# Point threshold boundaries - the exact cases named in the handoff
# =========================================================================

def test_39_points_gets_no_point_bonus(engine):
    assert bonus_only(engine, points=39) == 0.0


def test_40_points_exactly_gets_40_bonus(engine):
    assert bonus_only(engine, points=40) == pytest.approx(3.0)


def test_41_points_gets_40_bonus(engine):
    assert bonus_only(engine, points=41) == pytest.approx(3.0)


def test_49_points_gets_only_40_bonus(engine):
    assert bonus_only(engine, points=49) == pytest.approx(3.0)


def test_50_points_exactly_stacks_both_point_bonuses(engine):
    """Default config assumes 40+ and 50+ stack. 3 + 4 = 7."""
    assert bonus_only(engine, points=50) == pytest.approx(7.0)


def test_51_points_stacks_both_point_bonuses(engine):
    assert bonus_only(engine, points=51) == pytest.approx(7.0)


def test_50_points_without_stacking_pays_only_the_higher_tier(no_stack):
    """The other branch of the UNVERIFIED assumption: only 50+ pays."""
    assert bonus_only(no_stack, points=50) == pytest.approx(4.0)


def test_40_points_without_stacking_is_unaffected(no_stack):
    """Turning stacking off must not change a line that only crosses 40."""
    assert bonus_only(no_stack, points=40) == pytest.approx(3.0)


def test_60_points_does_not_invent_a_third_tier(engine):
    """No 60+ bonus is configured, so 60 scores the same bonus as 50."""
    assert bonus_only(engine, points=60) == pytest.approx(7.0)


# =========================================================================
# Assist threshold
# =========================================================================

def test_14_assists_gets_no_assist_bonus(engine):
    assert bonus_only(engine, assists=14, points=5) == 0.0


def test_15_assists_exactly_gets_assist_bonus(engine):
    """15 ast alone is only one category at 10+, so no double-double."""
    assert bonus_only(engine, assists=15, points=5) == pytest.approx(3.0)


def test_16_assists_gets_assist_bonus(engine):
    assert bonus_only(engine, assists=16, points=5) == pytest.approx(3.0)


def test_assist_bonus_is_unaffected_by_point_stacking_switch(no_stack):
    """points_thresholds_stack must not leak into the assists tier."""
    assert bonus_only(no_stack, assists=15, points=5) == pytest.approx(3.0)


# =========================================================================
# Double-double / triple-double
# =========================================================================

def test_10_reb_10_ast_is_a_double_double(engine):
    assert bonus_only(engine, rebounds=10, assists=10, points=5) == pytest.approx(2.0)


def test_9_reb_10_ast_is_not_a_double_double(engine):
    assert bonus_only(engine, rebounds=9, assists=10, points=5) == 0.0


def test_10_pts_10_reb_10_ast_is_a_triple_double(engine):
    """VERIFIED stacking: triple-double pays TD + DD = 3 + 2 = 5."""
    assert bonus_only(engine, points=10, rebounds=10, assists=10) == pytest.approx(5.0)


def test_triple_double_without_stacking_pays_only_td(no_stack):
    assert bonus_only(no_stack, points=10, rebounds=10, assists=10) == pytest.approx(3.0)


def test_steals_and_blocks_count_toward_double_double(engine):
    """Sleeper counts pts/reb/ast/stl/blk - a 10stl/10blk line is a DD."""
    assert bonus_only(engine, steals=10, blocks=10, points=4) == pytest.approx(2.0)


def test_defensive_triple_double(engine):
    assert bonus_only(engine, points=12, steals=10, blocks=10) == pytest.approx(5.0)


def test_quadruple_double_pays_triple_double_by_default(engine):
    assert bonus_only(engine, points=10, rebounds=10, assists=10, blocks=10) == pytest.approx(5.0)


def test_quadruple_double_can_be_configured_off():
    cfg = config_with_overrides(
        {"league": {"bonus_rules": {"quadruple_double_pays_triple_double": False}}}
    )
    engine = ScoringEngine(cfg.scoring)
    # Falls back to double-double only.
    assert bonus_only(engine, points=10, rebounds=10, assists=10, blocks=10) == pytest.approx(2.0)


def test_double_double_threshold_is_configurable():
    cfg = config_with_overrides({"league": {"bonus_rules": {"double_double_threshold": 8}}})
    engine = ScoringEngine(cfg.scoring)
    assert bonus_only(engine, rebounds=8, assists=8, points=5) == pytest.approx(2.0)


def test_double_double_categories_are_configurable():
    """A league that only counts pts/reb/ast must not award a stl/blk DD."""
    cfg = config_with_overrides(
        {"league": {"bonus_rules": {"double_double_categories": ["points", "rebounds", "assists"]}}}
    )
    engine = ScoringEngine(cfg.scoring)
    assert bonus_only(engine, steals=10, blocks=10, points=4) == 0.0


# =========================================================================
# Cross-bonus interaction - the "any other way" the handoff warned about
# =========================================================================

def test_40_point_triple_double_collects_every_applicable_bonus(engine):
    """TD(3) + DD(2) + 40+(3) = 8."""
    assert bonus_only(engine, points=40, rebounds=10, assists=10) == pytest.approx(8.0)


def test_50_point_triple_double_collects_every_applicable_bonus(engine):
    """TD(3) + DD(2) + 40+(3) + 50+(4) = 12."""
    assert bonus_only(engine, points=50, rebounds=10, assists=10) == pytest.approx(12.0)


def test_15_assist_triple_double_collects_dd_td_and_assist_bonus(engine):
    """TD(3) + DD(2) + 15ast(3) = 8."""
    assert bonus_only(engine, points=15, rebounds=10, assists=15) == pytest.approx(8.0)


def test_the_maximal_line_collects_all_five_bonuses(engine):
    """50 pts, 15 ast, 10 reb: TD(3)+DD(2)+40(3)+50(4)+15ast(3) = 15."""
    breakdown = engine.score_game_detailed(stat_line(points=50, rebounds=10, assists=15))
    assert breakdown.bonus_total == pytest.approx(15.0)
    assert len(breakdown.bonuses_awarded) == 5


def test_maximal_line_with_all_stacking_off(no_stack):
    """TD(3) + 50(4) + 15ast(3) = 10; DD and 40+ suppressed."""
    assert bonus_only(no_stack, points=50, rebounds=10, assists=15) == pytest.approx(10.0)


def test_bonus_values_come_from_config():
    cfg = config_with_overrides({"league": {"bonuses": {"double_double": 10}}})
    engine = ScoringEngine(cfg.scoring)
    assert bonus_only(engine, rebounds=10, assists=10, points=5) == pytest.approx(10.0)


def test_negative_stats_never_trigger_bonuses(engine):
    """Defensive: bad source data must not manufacture a bonus."""
    assert bonus_only(engine, points=-5, rebounds=-10) == 0.0


# =========================================================================
# Vectorised path agrees on every bonus case
# =========================================================================

@pytest.mark.parametrize(
    "stats",
    [
        {"points": 39}, {"points": 40}, {"points": 41}, {"points": 49},
        {"points": 50}, {"points": 51}, {"points": 60},
        {"assists": 14, "points": 5}, {"assists": 15, "points": 5},
        {"rebounds": 10, "assists": 10, "points": 5},
        {"points": 10, "rebounds": 10, "assists": 10},
        {"points": 50, "rebounds": 10, "assists": 15},
        {"steals": 10, "blocks": 10, "points": 4},
        {"points": 10, "rebounds": 10, "assists": 10, "blocks": 10},
    ],
)
@pytest.mark.parametrize("stacking", [True, False])
def test_vectorised_bonus_matches_scalar(stats, stacking):
    import pandas as pd

    cfg = config_with_overrides(
        {
            "league": {
                "bonus_rules": {
                    "points_thresholds_stack": stacking,
                    "triple_double_stacks_with_double_double": stacking,
                }
            }
        }
    )
    engine = ScoringEngine(cfg.scoring)
    row = stat_line(**stats)
    scalar = engine.score_game(row)
    vector = engine.score_dataframe(pd.DataFrame([row]))["fantasy_points"].iloc[0]
    assert vector == pytest.approx(scalar)
