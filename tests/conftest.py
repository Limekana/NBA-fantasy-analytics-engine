"""Shared fixtures. Tests always load config explicitly so they never depend on
the developer's working copy of config/league.yaml being unmodified."""
from __future__ import annotations

import pytest

from src.config import config_with_overrides, load_config


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def scoring(cfg):
    return cfg.scoring


def stat_line(**kwargs) -> dict[str, float]:
    """Build a complete box score line, defaulting every unspecified stat to 0."""
    base = {
        "points": 0.0,
        "rebounds": 0.0,
        "assists": 0.0,
        "steals": 0.0,
        "blocks": 0.0,
        "turnovers": 0.0,
        "personal_fouls": 0.0,
        "free_throws_made": 0.0,
        "three_pointers_made": 0.0,
    }
    base.update(kwargs)
    return base


@pytest.fixture
def line():
    return stat_line


@pytest.fixture
def no_stack_cfg():
    """Config with both stacking switches turned OFF, to test the other branch."""
    return config_with_overrides(
        {
            "league": {
                "bonus_rules": {
                    "points_thresholds_stack": False,
                    "triple_double_stacks_with_double_double": False,
                }
            }
        }
    )
