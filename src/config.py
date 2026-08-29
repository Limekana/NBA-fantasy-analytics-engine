"""Configuration loading and validation.

Engineering Rule 1 (handoff sec.23): everything configurable, nothing about this
league hardcoded in ``src/``. Every module reads its parameters from here.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
EXTERNAL_DIR = DATA_DIR / "external"
OUTPUT_DIR = REPO_ROOT / "outputs"


class ConfigError(ValueError):
    """Raised when a configuration file is missing or internally inconsistent."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Missing configuration file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level.")
    return data


@dataclass(frozen=True)
class ScoringConfig:
    """The complete scoring ruleset, resolved from ``config/league.yaml``."""

    stat_weights: Mapping[str, float]
    bonuses: Mapping[str, float]
    rules: Mapping[str, Any]

    @property
    def dd_categories(self) -> tuple[str, ...]:
        return tuple(self.rules["double_double_categories"])

    @property
    def dd_threshold(self) -> int:
        return int(self.rules["double_double_threshold"])

    @property
    def td_stacks(self) -> bool:
        return bool(self.rules["triple_double_stacks_with_double_double"])

    @property
    def point_thresholds_stack(self) -> bool:
        return bool(self.rules["points_thresholds_stack"])

    @property
    def quad_pays_td(self) -> bool:
        return bool(self.rules.get("quadruple_double_pays_triple_double", True))


@dataclass(frozen=True)
class LeagueConfig:
    """Parsed ``config/league.yaml``."""

    raw: Mapping[str, Any]

    @property
    def teams(self) -> int:
        return int(self.raw["league"]["teams"])

    @property
    def game_mode(self) -> str:
        return str(self.raw["league"]["game_mode"])

    @property
    def is_lock_in(self) -> bool:
        return self.game_mode == "lock_in" and bool(
            self.raw.get("lock_in", {}).get("enabled", False)
        )

    @property
    def roster(self) -> Mapping[str, int]:
        return self.raw["roster"]

    @property
    def roster_size(self) -> int:
        """Total draftable roster spots (excludes IR, which is not drafted into)."""
        return sum(v for k, v in self.roster.items() if k != "IR")

    @property
    def starting_slots(self) -> Mapping[str, int]:
        return {k: v for k, v in self.roster.items() if k not in ("BN", "IR")}

    @property
    def position_eligibility(self) -> Mapping[str, list[str]]:
        return self.raw["position_eligibility"]

    @property
    def lock_in(self) -> Mapping[str, Any]:
        return self.raw.get("lock_in", {})

    @property
    def calendar(self) -> Mapping[str, Any]:
        return self.raw["calendar"]

    @property
    def draft(self) -> Mapping[str, Any]:
        return self.raw["draft"]

    @property
    def scoring(self) -> ScoringConfig:
        return ScoringConfig(
            stat_weights=dict(self.raw["scoring"]),
            bonuses=dict(self.raw["bonuses"]),
            rules=dict(self.raw["bonus_rules"]),
        )


@dataclass(frozen=True)
class AppConfig:
    """Everything the pipeline needs, loaded once."""

    league: LeagueConfig
    model: Mapping[str, Any]
    assumptions: Mapping[str, Any]
    sources: Mapping[str, Any]
    paths: dict[str, Path] = field(default_factory=dict)

    @property
    def scoring(self) -> ScoringConfig:
        return self.league.scoring


# Numeric fields that must be present and finite for the pipeline to be trustworthy.
_REQUIRED_SCORING_KEYS = (
    "points",
    "rebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "personal_fouls",
)
_REQUIRED_BONUS_RULE_KEYS = (
    "double_double_categories",
    "double_double_threshold",
    "triple_double_stacks_with_double_double",
    "points_thresholds_stack",
)


def validate(cfg: AppConfig) -> list[str]:
    """Return a list of human-readable configuration problems (empty == valid).

    Kept as a pure function returning warnings rather than raising, so the CLI can
    show every problem at once instead of one per run.
    """
    problems: list[str] = []
    league = cfg.league

    for key in _REQUIRED_SCORING_KEYS:
        if key not in league.raw.get("scoring", {}):
            problems.append(f"scoring.{key} is missing from config/league.yaml")

    for key in _REQUIRED_BONUS_RULE_KEYS:
        if key not in league.raw.get("bonus_rules", {}):
            problems.append(f"bonus_rules.{key} is missing from config/league.yaml")

    for name, value in league.raw.get("scoring", {}).items():
        if not isinstance(value, (int, float)):
            problems.append(f"scoring.{name} must be numeric, got {value!r}")

    for name, value in league.raw.get("bonuses", {}).items():
        if not isinstance(value, (int, float)):
            problems.append(f"bonuses.{name} must be numeric, got {value!r}")

    if league.teams < 2:
        problems.append("league.teams must be >= 2")

    rounds = league.draft.get("rounds")
    if rounds is not None and rounds != league.roster_size:
        problems.append(
            f"draft.rounds ({rounds}) != draftable roster size ({league.roster_size}). "
            "One of the two is wrong."
        )

    slot = league.draft.get("my_draft_slot")
    if slot is not None and not (1 <= int(slot) <= league.teams):
        problems.append(
            f"draft.my_draft_slot ({slot}) must be between 1 and {league.teams}"
        )

    if league.game_mode == "lock_in" and not league.raw.get("lock_in", {}).get("enabled"):
        problems.append("league.game_mode is lock_in but lock_in.enabled is false")

    fallback = league.lock_in.get("auto_lock_fallback")
    if fallback not in (None, "last_game", "first_game", "best_game"):
        problems.append(f"lock_in.auto_lock_fallback has unknown value {fallback!r}")

    archetypes = cfg.model.get("draft_simulation", {}).get("manager_archetypes", {})
    if archetypes:
        total = sum(archetypes.values())
        if abs(total - 1.0) > 1e-6:
            problems.append(
                f"model.draft_simulation.manager_archetypes must sum to 1.0, got {total}"
            )

    weights = cfg.model.get("projection", {}).get("season_weights", {})
    if weights and any(w < 0 for w in weights.values()):
        problems.append("model.projection.season_weights must all be >= 0")

    return problems


def load_config(config_dir: Path | str | None = None) -> AppConfig:
    """Load every configuration file. Not cached, so tests can mutate freely."""
    directory = Path(config_dir) if config_dir else CONFIG_DIR
    cfg = AppConfig(
        league=LeagueConfig(raw=_read_yaml(directory / "league.yaml")),
        model=_read_yaml(directory / "model.yaml"),
        assumptions=_read_yaml(directory / "assumptions.yaml"),
        sources=_read_yaml(directory / "sources.yaml"),
        paths={
            "repo": REPO_ROOT,
            "raw": RAW_DIR,
            "processed": PROCESSED_DIR,
            "external": EXTERNAL_DIR,
            "outputs": OUTPUT_DIR,
        },
    )
    return cfg


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide config singleton for normal (non-test) use."""
    return load_config()


def config_with_overrides(overrides: Mapping[str, Any], base: AppConfig | None = None) -> AppConfig:
    """Return a copy of the config with a deep-merged override applied.

    Used by tests and by sensitivity analysis ("what if the commissioner drops the
    50-point bonus?") without mutating the on-disk YAML.
    """
    base = base or load_config()
    raw = copy.deepcopy(dict(base.league.raw))
    model = copy.deepcopy(dict(base.model))

    def deep_merge(target: dict, patch: Mapping[str, Any]) -> dict:
        for key, value in patch.items():
            # An EMPTY mapping means "clear this key". Recursing into it would
            # merge nothing and leave the original untouched, which silently
            # turns an override into a no-op - and made an ablation study
            # report that a component had zero effect when it had never
            # actually been switched off.
            if isinstance(value, Mapping) and not value:
                target[key] = {}
            elif isinstance(value, Mapping) and isinstance(target.get(key), dict):
                deep_merge(target[key], value)
            else:
                target[key] = value
        return target

    deep_merge(raw, overrides.get("league", {}))
    deep_merge(model, overrides.get("model", {}))

    return AppConfig(
        league=LeagueConfig(raw=raw),
        model=model,
        assumptions=copy.deepcopy(dict(base.assumptions)),
        sources=copy.deepcopy(dict(base.sources)),
        paths=dict(base.paths),
    )
