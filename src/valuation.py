"""Player valuation: where facts, projections and Lock-In mechanics combine.

This is the module that produces the number a draft pick is actually made on, so
it is also where the two subtlest modelling decisions in the project live.

**1. Bonuses are non-linear, so you cannot score the average stat line.**

    The obvious implementation - project a per-game stat line, push it through
    the scoring engine - silently loses most of the bonus value. Scoring is
    linear in the counting stats, so for those::

        E[0.5*PTS + REB + ...] == 0.5*E[PTS] + E[REB] + ...

    but bonuses are threshold functions, and ``E[f(X)] != f(E[X])`` for those. A
    player averaging 26 points never crosses 40 on their average line, yet
    collects the 40-point bonus in perhaps 5% of real games. Scoring the mean
    line prices that at zero. So the linear part is computed from the projected
    line, and expected bonus points are projected *separately* from historical
    bonus rates, scaled by the projected change in production.

**2. Lock-In needs a distribution, not a mean.**

    The Lock-In simulator resamples a player's game-level distribution, so the
    projection has to hand it a full distribution rather than a point estimate.
    The player's historical FP sample is rescaled to the projected mean, which
    preserves their empirical skew - the fat right tail that makes Lock-In
    valuable in the first place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from src.distributions.profile import PlayerProfile
from src.lockin import LockInSimulator
from src.projections.baseline import PlayerProjection
from src.projections.games_played import GamesPlayedProjection
from src.schedule import expected_games_per_week, team_pmf_or_fallback
from src.scoring import ScoringEngine

# Bonus rate -> the config bonus key that pays it.
BONUS_RATE_TO_KEY = {
    "double_double_rate": "double_double",
    "triple_double_rate": "triple_double",
    "40_point_rate": "points_40_plus",
    "50_point_rate": "points_50_plus",
    "15_assist_rate": "assists_15_plus",
}


@dataclass
class PlayerValuation:
    """The complete valuation record for one player."""

    player_id: str
    player_name: str
    team: str
    position: str
    age: float

    # Facts (measured)
    historical_fp_per_game: float
    historical_games: int

    # Projections
    projected_minutes: float
    projected_fp_per_game: float
    projected_base_fp: float
    projected_bonus_fp: float
    projected_games: float
    games_floor: float
    games_ceiling: float
    availability_probability: float

    # Distribution
    median_fp: float
    floor_fp: float
    ceiling_fp: float
    std_fp: float
    percentiles: Mapping[int, float]
    threshold_probabilities: Mapping[int, float]
    bonus_rates: Mapping[str, float]

    # Lock-In
    games_per_week: float
    lockin_weekly_value: float
    lockin_perfect_weekly: float
    lockin_auto_weekly: float
    lock_in_advantage: float
    used_real_schedule: bool

    # Season totals
    projected_season_value: float
    projected_weeks_available: float

    # Risk / provenance
    risk_score: float
    role_uncertainty: float
    archetype: str = ""
    assumption_notes: str = ""
    is_synthetic: bool = False

    def to_row(self) -> dict:
        row = {
            "player_name": self.player_name,
            "team": self.team,
            "position": self.position,
            "age": self.age,
            "projected_fp_game": round(self.projected_fp_per_game, 2),
            "projected_games": round(self.projected_games, 1),
            "projected_season_value": round(self.projected_season_value, 1),
            "lockin_value": round(self.lockin_weekly_value, 2),
            "lock_in_advantage": round(self.lock_in_advantage, 2),
            "lockin_perfect": round(self.lockin_perfect_weekly, 2),
            "lockin_auto": round(self.lockin_auto_weekly, 2),
            "games_per_week": round(self.games_per_week, 2),
            "median_fp": round(self.median_fp, 2),
            "floor": round(self.floor_fp, 2),
            "ceiling": round(self.ceiling_fp, 2),
            "std_dev": round(self.std_fp, 2),
            "risk": round(self.risk_score, 3),
            "role_uncertainty": round(self.role_uncertainty, 3),
            "archetype": self.archetype,
            "historical_fp_game": round(self.historical_fp_per_game, 2),
            "historical_games": self.historical_games,
            "projected_minutes": round(self.projected_minutes, 1),
            "projected_base_fp": round(self.projected_base_fp, 2),
            "projected_bonus_fp": round(self.projected_bonus_fp, 2),
            "games_floor": round(self.games_floor, 1),
            "games_ceiling": round(self.games_ceiling, 1),
            "availability_probability": round(self.availability_probability, 3),
            "used_real_schedule": self.used_real_schedule,
            "assumption_notes": self.assumption_notes,
            "is_synthetic": self.is_synthetic,
        }
        row.update({f"p{p}": round(v, 2) for p, v in self.percentiles.items()})
        row.update({f"p_fp_ge_{t}": round(v, 4) for t, v in self.threshold_probabilities.items()})
        row.update({k: round(v, 4) for k, v in self.bonus_rates.items()})
        return row


def linear_score(stats: Mapping[str, float], engine: ScoringEngine) -> float:
    """The bonus-free part of the score. Exact under averaging."""
    return sum(
        float(stats.get(stat, 0.0)) * float(weight)
        for stat, weight in engine.cfg.stat_weights.items()
    )


def project_bonus_points(
    profile: PlayerProfile,
    projected_stats: Mapping[str, float],
    engine: ScoringEngine,
) -> float:
    """Expected bonus points per game for the projected season.

    Historical bonus *rates* are the base. They are then scaled by the projected
    change in the driving stat, because a player whose minutes jump from 22 to 32
    will cross bonus thresholds more often than they used to - and one whose role
    shrinks will cross them less.

    The scaling is super-linear (exponent > 1) because bonus thresholds sit in the
    right tail of the distribution: a 15% lift in scoring raises the frequency of
    40-point games by considerably more than 15%. The exponent is a deliberate,
    documented approximation - a full re-simulation of the shifted distribution
    would be more precise but far less inspectable, and the handoff is explicit
    that transparency beats sophistication here.
    """
    if not profile.bonus_rates:
        return 0.0

    # Which projected stat drives each bonus's frequency. DD/TD depend on the
    # whole line rather than one stat, so they are driven by the aggregate change
    # in counting production instead.
    driver = {
        "40_point_rate": "points",
        "50_point_rate": "points",
        "15_assist_rate": "assists",
    }
    aggregate_ratio = _production_ratio(profile, projected_stats)

    total = 0.0
    for rate_name, rate in profile.bonus_rates.items():
        bonus_key = BONUS_RATE_TO_KEY.get(rate_name)
        if bonus_key is None or bonus_key not in engine.cfg.bonuses:
            continue

        stat = driver.get(rate_name)
        if stat is None:
            ratio = aggregate_ratio
        elif profile.per_game_stats.get(stat, 0.0) > 0:
            ratio = float(projected_stats.get(stat, 0.0)) / profile.per_game_stats[stat]
        else:
            ratio = 1.0

        # Tail-sensitivity exponent: crossing a high threshold is disproportionately
        # sensitive to the mean of the underlying distribution. Single-stat
        # thresholds (40/50 pts, 15 ast) sit further into the tail than DD/TD.
        exponent = 2.0 if stat is not None else 1.3
        scaled = float(np.clip(rate * (max(ratio, 0.0) ** exponent), 0.0, 1.0))
        total += scaled * float(engine.cfg.bonuses[bonus_key])

    return total


def _production_ratio(profile: PlayerProfile, projected_stats: Mapping[str, float]) -> float:
    """Projected counting production relative to historical, across the DD stats.

    Used to scale double-double and triple-double rates, which respond to the
    whole stat line rather than to any single category.
    """
    categories = ("points", "rebounds", "assists", "steals", "blocks")
    historical = sum(profile.per_game_stats.get(c, 0.0) for c in categories)
    if historical <= 0:
        return 1.0
    projected = sum(float(projected_stats.get(c, 0.0)) for c in categories)
    return projected / historical


def rescale_distribution(sample: np.ndarray, target_mean: float) -> np.ndarray:
    """Shift a player's historical FP sample to a projected mean, keeping shape.

    Multiplicative rescaling preserves the coefficient of variation and the skew,
    which is what the Lock-In simulator actually consumes. An additive shift would
    distort the right tail that drives Lock-In value.
    """
    if sample.size == 0:
        return sample
    current = float(np.mean(sample))
    if current <= 0:
        return np.full_like(sample, max(target_mean, 0.0))
    return np.clip(sample * (target_mean / current), 0.0, None)


def compute_risk(
    availability_probability: float,
    coefficient_of_variation: float,
    role_uncertainty: float,
    weights: Mapping[str, float],
) -> float:
    """Composite 0-1 risk score. Higher means less trustworthy, not less valuable.

    Note the deliberate asymmetry with Lock-In: game-to-game variance is a *risk*
    in a season-long sense but an *asset* under Lock-In. Both facts are reported
    separately rather than netted into one number, so the draft board can show
    a high-ceiling, high-risk player as exactly that.
    """
    availability_risk = 1.0 - float(availability_probability)
    variance_risk = float(np.clip(coefficient_of_variation / 0.6, 0.0, 1.0))
    return float(
        np.clip(
            float(weights.get("availability", 0.4)) * availability_risk
            + float(weights.get("variance", 0.3)) * variance_risk
            + float(weights.get("role", 0.3)) * float(role_uncertainty),
            0.0,
            1.0,
        )
    )


def build_valuations(
    profiles: Sequence[PlayerProfile],
    projections: Sequence[PlayerProjection],
    games_projections: Mapping[str, GamesPlayedProjection],
    team_pmfs: Mapping[str, Mapping[int, float]],
    league_cfg,
    model_cfg: Mapping,
    engine: ScoringEngine | None = None,
    archetypes: Mapping[str, str] | None = None,
) -> list[PlayerValuation]:
    """Combine every component into a per-player valuation."""
    engine = engine or ScoringEngine(league_cfg.scoring)
    simulator = LockInSimulator(model_cfg, league_cfg.lock_in)
    profile_index = {p.player_id: p for p in profiles}
    risk_weights = model_cfg.get("risk", {}).get("weights", {})
    weeks_in_season = int(league_cfg.calendar.get("regular_season_weeks", 22))
    season_length = int(model_cfg.get("games_played", {}).get("season_length", 82))
    archetypes = archetypes or {}

    valuations: list[PlayerValuation] = []
    for projection in projections:
        profile = profile_index.get(projection.player_id)
        if profile is None:
            continue

        # --- per-game projection, with the non-linear bonus correction ---
        base_fp = linear_score(projection.projected_stats, engine)
        bonus_fp = project_bonus_points(profile, projection.projected_stats, engine)
        projected_fp = base_fp + bonus_fp

        # --- distribution, rescaled to the projection ---
        sample = rescale_distribution(profile.fp_sample, projected_fp)

        # --- schedule ---
        pmf, used_real = team_pmf_or_fallback(projection.team, team_pmfs, model_cfg)

        # --- Lock-In valuation ---
        lock_profile = simulator.profile(projection.player_id, sample, pmf)

        # --- availability ---
        games = games_projections.get(projection.player_id)
        projected_games = games.expected_games if games else float(profile.games)
        availability = games.availability_probability if games else profile.games / season_length

        # In Lock-In, availability reduces the number of *usable* weeks and thins
        # the games within a week, rather than scaling a season point total.
        weeks_available = weeks_in_season * float(np.clip(availability / 0.85, 0.0, 1.0))
        weeks_available = min(weeks_available, weeks_in_season)
        season_value = lock_profile.lockin_value * weeks_available

        notes = "; ".join(
            n for n in (projection.assumption_notes, games.assumption_notes if games else "") if n
        )
        if not used_real:
            notes = "; ".join(filter(None, [notes, "ASSUMPTION: fallback games-per-week PMF (no real schedule loaded)"]))

        percentile_scale = projected_fp / profile.mean_fp if profile.mean_fp > 0 else 1.0

        valuations.append(
            PlayerValuation(
                player_id=projection.player_id,
                player_name=projection.player_name,
                team=projection.team,
                position=projection.position,
                age=projection.age,
                historical_fp_per_game=profile.mean_fp,
                historical_games=profile.games,
                projected_minutes=projection.projected_minutes,
                projected_fp_per_game=projected_fp,
                projected_base_fp=base_fp,
                projected_bonus_fp=bonus_fp,
                projected_games=projected_games,
                games_floor=games.games_floor if games else float(profile.games),
                games_ceiling=games.games_ceiling if games else float(profile.games),
                availability_probability=availability,
                median_fp=profile.median_fp * percentile_scale,
                floor_fp=profile.floor * percentile_scale,
                ceiling_fp=profile.ceiling * percentile_scale,
                std_fp=profile.std_fp * percentile_scale,
                percentiles={p: v * percentile_scale for p, v in profile.percentiles.items()},
                threshold_probabilities=dict(profile.threshold_probabilities),
                bonus_rates=dict(profile.bonus_rates),
                games_per_week=expected_games_per_week(pmf),
                lockin_weekly_value=lock_profile.lockin_value,
                lockin_perfect_weekly=lock_profile.perfect_value,
                lockin_auto_weekly=lock_profile.auto_value,
                lock_in_advantage=lock_profile.lock_in_advantage,
                used_real_schedule=used_real,
                projected_season_value=season_value,
                projected_weeks_available=weeks_available,
                risk_score=compute_risk(
                    availability, profile.coefficient_of_variation, projection.role_uncertainty, risk_weights
                ),
                role_uncertainty=projection.role_uncertainty,
                archetype=archetypes.get(projection.player_id, ""),
                assumption_notes=notes,
                is_synthetic=projection.is_synthetic or profile.is_synthetic,
            )
        )
    return valuations


def valuations_to_frame(valuations: Sequence[PlayerValuation]) -> pd.DataFrame:
    if not valuations:
        return pd.DataFrame()
    frame = pd.DataFrame([v.to_row() for v in valuations])
    return frame.sort_values("projected_season_value", ascending=False).reset_index(drop=True)
