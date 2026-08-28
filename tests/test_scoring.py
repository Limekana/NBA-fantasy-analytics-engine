"""Base scoring tests - the stat weights, before any bonus interaction.

Engineering Rule 4: this file must pass before any projection work is trusted.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.config import config_with_overrides
from src.scoring import ScoringEngine
from tests.conftest import stat_line


@pytest.fixture
def engine(scoring):
    return ScoringEngine(scoring)


def test_empty_stat_line_scores_zero(engine, line):
    assert engine.score_game(line()) == 0.0


def test_missing_stats_are_treated_as_zero(engine):
    """A source that omits a column must not crash or silently invent value."""
    assert engine.score_game({"points": 10}) == pytest.approx(5.0)


def test_none_and_nan_are_treated_as_zero(engine):
    assert engine.score_game({"points": 10, "rebounds": None, "assists": float("nan")}) == pytest.approx(5.0)


@pytest.mark.parametrize(
    "stat,value,expected",
    [
        ("points", 20, 10.0),          # 0.5/pt
        ("rebounds", 7, 7.0),          # 1/reb
        ("assists", 6, 6.0),           # 1/ast
        ("steals", 3, 6.0),            # 2/stl
        ("blocks", 2, 4.0),            # 2/blk
        ("free_throws_made", 8, 2.0),  # 0.25/ftm
        ("three_pointers_made", 5, 2.5),
    ],
)
def test_individual_positive_weights(engine, line, stat, value, expected):
    assert engine.score_game(line(**{stat: value})) == pytest.approx(expected)


def test_turnovers_are_penalised(engine, line):
    """Handoff sec.25: do NOT ignore turnovers."""
    assert engine.score_game(line(turnovers=5)) == pytest.approx(-5.0)


def test_personal_fouls_are_penalised(engine, line):
    """Handoff sec.25: do NOT ignore personal fouls."""
    assert engine.score_game(line(personal_fouls=4)) == pytest.approx(-4.0)


def test_full_stat_line_matches_hand_calculation(engine, line):
    # 25 pts, 5 reb, 5 ast, 1 stl, 1 blk, 3 to, 2 pf, 6 ftm, 3 3pm
    # = 12.5 + 5 + 5 + 2 + 2 - 3 - 2 + 1.5 + 1.5 = 24.5, no bonuses
    stats = line(points=25, rebounds=5, assists=5, steals=1, blocks=1,
                 turnovers=3, personal_fouls=2, free_throws_made=6,
                 three_pointers_made=3)
    assert engine.score_game(stats) == pytest.approx(24.5)


def test_breakdown_decomposes_exactly(engine, line):
    stats = line(points=30, rebounds=12, assists=4, steals=2, turnovers=3)
    b = engine.score_game_detailed(stats)
    assert b.base + b.bonus_total == pytest.approx(b.total)
    assert sum(b.stat_points.values()) == pytest.approx(b.base)
    assert sum(b.bonuses_awarded.values()) == pytest.approx(b.bonus_total)


def test_scoring_reads_from_config_not_hardcoded():
    """Engineering Rule 1: changing the YAML must change every valuation."""
    doubled = config_with_overrides({"league": {"scoring": {"points": 1.0}}})
    assert ScoringEngine(doubled.scoring).score_game({"points": 20}) == pytest.approx(20.0)


def test_removing_a_bonus_from_config_disables_it():
    """A bonus absent from config must never fire with an invented default."""
    stripped = config_with_overrides({"league": {"bonuses": {}}})
    stripped.league.raw["bonuses"] = {}
    stats = stat_line(points=50, rebounds=10, assists=15)
    assert ScoringEngine(stripped.scoring).score_game(stats) == pytest.approx(50.0)


def test_stat_not_in_config_scores_zero(engine, line):
    """Unknown stat keys are ignored rather than guessed at."""
    stats = line(points=10)
    stats["flagrant_fouls"] = 3
    assert engine.score_game(stats) == pytest.approx(5.0)


# --- vectorised path must agree with the scalar path exactly ---------------

def test_vectorised_matches_scalar(engine):
    rows = [
        stat_line(points=40, rebounds=10, assists=10),
        stat_line(points=50, rebounds=3, assists=2),
        stat_line(points=9, rebounds=11, assists=15, steals=1),
        stat_line(points=0),
        stat_line(points=12, rebounds=10, assists=10, steals=10, blocks=10),
        stat_line(points=25, turnovers=6, personal_fouls=5),
    ]
    df = pd.DataFrame(rows)
    scored = engine.score_dataframe(df)
    expected = [engine.score_game(r) for r in rows]
    assert scored["fantasy_points"].tolist() == pytest.approx(expected)


def test_vectorised_handles_empty_frame(engine):
    df = pd.DataFrame(columns=list(stat_line().keys()))
    out = engine.score_dataframe(df)
    assert out.empty
    assert "fantasy_points" in out.columns


def test_vectorised_handles_missing_columns(engine):
    """Ingestion sources vary; a partial frame must still score correctly."""
    df = pd.DataFrame([{"points": 20, "rebounds": 4}])
    out = engine.score_dataframe(df)
    # 20 pts * 0.5 + 4 reb * 1 = 14, no bonus (only one category at 10+).
    assert out["fantasy_points"].iloc[0] == pytest.approx(14.0)


def test_vectorised_awards_bonus_on_partial_frame(engine):
    """20 points and 10 rebounds is a double-double even in a two-column frame."""
    df = pd.DataFrame([{"points": 20, "rebounds": 10}])
    out = engine.score_dataframe(df)
    assert out["fantasy_points"].iloc[0] == pytest.approx(22.0)
    assert bool(out["bonus_double_double"].iloc[0]) is True
