"""Configuration tests.

Engineering Rule 1: everything configurable. These tests exist so that a
regression which reintroduces a hardcoded league value fails loudly.
"""
from __future__ import annotations

import pytest

from src.config import ConfigError, config_with_overrides, load_config, validate


def test_config_loads_and_validates(cfg):
    assert validate(cfg) == []


def test_missing_config_directory_raises():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config/dir")


def test_league_basics(cfg):
    assert cfg.league.teams == 10
    assert cfg.league.game_mode == "lock_in"
    assert cfg.league.is_lock_in is True


def test_roster_size_excludes_ir(cfg):
    """IR is not a draftable slot, so draft rounds must not count it."""
    assert cfg.league.roster_size == 14
    assert "IR" not in cfg.league.starting_slots
    assert "BN" not in cfg.league.starting_slots


def test_draft_rounds_match_roster_size(cfg):
    assert cfg.league.draft["rounds"] == cfg.league.roster_size


def test_validation_catches_rounds_mismatch():
    broken = config_with_overrides({"league": {"draft": {"rounds": 99}}})
    assert any("draft.rounds" in p for p in validate(broken))


def test_validation_catches_bad_draft_slot():
    broken = config_with_overrides({"league": {"draft": {"my_draft_slot": 47}}})
    assert any("my_draft_slot" in p for p in validate(broken))


def test_validation_accepts_valid_draft_slot():
    ok = config_with_overrides({"league": {"draft": {"my_draft_slot": 4}}})
    assert validate(ok) == []


def test_validation_catches_non_numeric_scoring():
    broken = config_with_overrides({"league": {"scoring": {"points": "half"}}})
    assert any("must be numeric" in p for p in validate(broken))


def test_validation_catches_archetype_weights_not_summing_to_one():
    broken = config_with_overrides(
        {"model": {"draft_simulation": {"manager_archetypes": {"adp_follower": 0.5, "value_follower": 0.2}}}}
    )
    assert any("must sum to 1.0" in p for p in validate(broken))


def test_validation_catches_unknown_auto_lock_rule():
    broken = config_with_overrides({"league": {"lock_in": {"auto_lock_fallback": "coin_flip"}}})
    assert any("auto_lock_fallback" in p for p in validate(broken))


def test_overrides_do_not_mutate_the_base_config(cfg):
    original = cfg.scoring.stat_weights["points"]
    config_with_overrides({"league": {"scoring": {"points": 99.0}}}, base=cfg)
    assert cfg.scoring.stat_weights["points"] == original


def test_every_scoring_value_is_present_in_config(cfg):
    """Guards against a stat quietly acquiring a hardcoded default in the engine."""
    required = {
        "points", "rebounds", "assists", "steals", "blocks",
        "turnovers", "personal_fouls", "free_throws_made", "three_pointers_made",
    }
    assert required <= set(cfg.scoring.stat_weights)


def test_position_eligibility_covers_every_position(cfg):
    for position in ("PG", "SG", "SF", "PF", "C"):
        assert position in cfg.league.position_eligibility
        assert "UTIL" in cfg.league.position_eligibility[position]
