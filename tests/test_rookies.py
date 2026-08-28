"""Rookie projection tests.

Rookies are pure prior - no NBA game logs exist - so these tests check the
machinery is honest about that rather than checking any number is "right".
"""
from __future__ import annotations

import numpy as np
import pytest

from src.config import load_config
from src.projections.rookies import (
    CV_BY_CONFIDENCE,
    build_rookie_projections,
    rookie_availability,
)
from src.scoring import ScoringEngine

PER36 = {
    "points": 18.0, "rebounds": 5.0, "assists": 4.0, "steals": 1.2, "blocks": 0.5,
    "turnovers": 2.8, "personal_fouls": 2.3, "free_throws_made": 3.5,
    "three_pointers_made": 1.8,
}


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def engine(cfg):
    return ScoringEngine(cfg.scoring)


def rookie_entry(**overrides):
    entry = {
        "team": "WAS", "position": "SF", "age": 19, "minutes": 30.0,
        "draft_pick": 1, "confidence": "high", "per36": dict(PER36),
        "note": "test",
    }
    entry.update(overrides)
    return entry


def test_no_rookies_configured_is_a_no_op(cfg, engine):
    projections, profiles = build_rookie_projections({}, cfg.model, engine)
    assert projections == [] and profiles == []


def test_rookie_without_per36_is_skipped(cfg, engine):
    assumptions = {"rookies": {"Someone": {"team": "BOS", "minutes": 20}}}
    projections, _ = build_rookie_projections(assumptions, cfg.model, engine)
    assert projections == []


def test_rookie_projection_scales_per36_by_minutes(cfg, engine):
    assumptions = {"rookies": {"Test Rookie": rookie_entry(minutes=36.0)}}
    projections, _ = build_rookie_projections(assumptions, cfg.model, engine)
    projection = projections[0]
    assert projection.projected_stats["points"] == pytest.approx(18.0)

    assumptions = {"rookies": {"Test Rookie": rookie_entry(minutes=18.0)}}
    projections, _ = build_rookie_projections(assumptions, cfg.model, engine)
    assert projections[0].projected_stats["points"] == pytest.approx(9.0)


def test_rookie_always_carries_an_assumption_note(cfg, engine):
    """Rule 7: a rookie's projection is entirely assumption and must say so."""
    assumptions = {"rookies": {"Test Rookie": rookie_entry()}}
    projections, _ = build_rookie_projections(assumptions, cfg.model, engine)
    assert "ASSUMPTION" in projections[0].assumption_notes
    assert "no NBA game logs" in projections[0].assumption_notes


def test_low_confidence_rookie_is_flagged_for_verification(cfg, engine):
    assumptions = {"rookies": {"Unsure Rookie": rookie_entry(confidence="low")}}
    projections, _ = build_rookie_projections(assumptions, cfg.model, engine)
    assert "VERIFY" in projections[0].assumption_notes


def test_confidence_drives_role_uncertainty(cfg, engine):
    def uncertainty(confidence):
        assumptions = {"rookies": {"R": rookie_entry(confidence=confidence)}}
        return build_rookie_projections(assumptions, cfg.model, engine)[0][0].role_uncertainty

    assert uncertainty("high") < uncertainty("medium") < uncertainty("low")


def test_confidence_drives_distribution_spread(cfg, engine):
    """Less certain rookies get wider outcome distributions."""
    def cv(confidence):
        assumptions = {"rookies": {"R": rookie_entry(confidence=confidence, draft_pick=15)}}
        profile = build_rookie_projections(assumptions, cfg.model, engine)[1][0]
        return profile.coefficient_of_variation

    assert cv("high") < cv("low")


def test_top_pick_is_less_volatile_than_a_late_pick(cfg, engine):
    """The user's point: a generational prospect is a narrower bet than a
    late-lottery project, even at the same confidence label."""
    def cv(pick):
        assumptions = {"rookies": {"R": rookie_entry(draft_pick=pick, confidence="medium")}}
        profile = build_rookie_projections(assumptions, cfg.model, engine)[1][0]
        return profile.coefficient_of_variation

    assert cv(1) < cv(25)


def test_rookie_profile_mean_matches_the_projection(cfg, engine):
    """The synthesised distribution must be centred on the deterministic line,
    not drift away from it."""
    assumptions = {"rookies": {"R": rookie_entry()}}
    projections, profiles = build_rookie_projections(assumptions, cfg.model, engine)
    expected = engine.score_game(projections[0].projected_stats)
    assert profiles[0].mean_fp == pytest.approx(expected, rel=0.02)


def test_rookie_profile_has_a_usable_distribution(cfg, engine):
    """The Lock-In simulator consumes fp_sample, so it must be populated."""
    assumptions = {"rookies": {"R": rookie_entry()}}
    _projections, profiles = build_rookie_projections(assumptions, cfg.model, engine)
    profile = profiles[0]
    assert profile.fp_sample.size > 100
    assert profile.std_fp > 0
    assert profile.floor < profile.median_fp < profile.ceiling


def test_rookie_bonus_rates_are_measured_not_guessed(cfg, engine):
    """Bonus rates come from the same simulated games as the scores, so they
    stay internally consistent with the distribution."""
    assumptions = {"rookies": {"R": rookie_entry(per36={**PER36, "rebounds": 13.0})}}
    _projections, profiles = build_rookie_projections(assumptions, cfg.model, engine)
    rates = profiles[0].bonus_rates
    assert "double_double_rate" in rates
    assert 0.0 <= rates["double_double_rate"] <= 1.0


def test_rebounding_rookie_has_a_higher_double_double_rate(cfg, engine):
    def dd_rate(rebounds):
        assumptions = {"rookies": {"R": rookie_entry(per36={**PER36, "rebounds": rebounds})}}
        return build_rookie_projections(assumptions, cfg.model, engine)[1][0].bonus_rates["double_double_rate"]

    assert dd_rate(13.0) > dd_rate(3.0)


def test_rookie_availability_prefers_an_explicit_value():
    assert rookie_availability({"expected_games": 41}, 82) == pytest.approx(0.5)


def test_rookie_availability_defaults_below_a_healthy_veteran():
    """Rookies lose games to G-League stints and DNP-CDs, not just injury."""
    assert rookie_availability({"confidence": "high"}) < 0.9
    assert rookie_availability({"confidence": "low"}) < rookie_availability({"confidence": "high"})


def test_configured_rookie_class_loads_and_is_sane(cfg, engine):
    """The real 2026 class in config/assumptions.yaml must actually build."""
    projections, profiles = build_rookie_projections(cfg.assumptions, cfg.model, engine)
    if not projections:
        pytest.skip("no rookies configured")

    assert len(projections) == len(profiles)
    for projection, profile in zip(projections, profiles):
        assert projection.projected_minutes > 0
        assert profile.mean_fp > 0
        assert "ASSUMPTION" in projection.assumption_notes
        # Nobody should project as a top-5 overall player off a rookie prior.
        assert profile.mean_fp < 45.0
