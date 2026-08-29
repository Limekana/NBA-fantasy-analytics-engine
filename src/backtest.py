"""Backtesting.

Handoff §20: *"This is mandatory before trusting the model... If the sophisticated
model doesn't beat simple baselines, investigate why. Do not add complexity simply
to make the model look more advanced."*

Two independent backtests live here, answering two different questions.

**1. Projection backtest** (`backtest_projections`)
   *Does our projection rank players better than trivial alternatives?*
   Walk-forward: train on seasons up to N-1, project season N, score against what
   actually happened in season N. The baselines are deliberately hard to beat -
   "last season's FP/game" is a genuinely strong predictor, and a model that
   cannot beat it has earned no trust.

**2. Lock-In strategy backtest** (`backtest_lockin_strategies`)
   *Which locking policy would actually have banked the most points?*
   This one needs no projection at all: reconstruct real fantasy weeks from the
   game log and replay each policy over the actual chronological sequences. No
   resampling, no distributional assumption - just what each rule would have
   scored. It is the cleanest evidence in the whole system.

The strict rule throughout: **a backtest may never see data from the season it is
predicting.** Every leak is a way to fool yourself into drafting badly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from src.distributions import build_profiles
from src.lockin import LockInSimulator, reconstruct_weeks, simulate_secretary_week
from src.lockin.simulator import LAST_GAME, PERFECT, SECRETARY
from src.lockin.strategies import (
    OptimalIIDStrategy,
    PercentileStrategy,
    PlayerContext,
    ThresholdStrategy,
    continuation_values,
)
from src.projections import compute_per36, project_players
from src.scoring import ScoringEngine
from src.valuation import linear_score


@dataclass
class BacktestMetrics:
    """How well a set of predictions ranked players against what happened."""

    name: str
    n_players: int
    spearman: float
    pearson: float
    mae: float
    rmse: float
    top_n_hit_rate: Mapping[int, float] = field(default_factory=dict)
    mean_rank_error: float = 0.0

    def to_row(self) -> dict:
        row = {
            "method": self.name,
            "n_players": self.n_players,
            "spearman": round(self.spearman, 4),
            "pearson": round(self.pearson, 4),
            "mae": round(self.mae, 3),
            "rmse": round(self.rmse, 3),
            "mean_rank_error": round(self.mean_rank_error, 2),
        }
        row.update({f"top{n}_hit": round(v, 3) for n, v in self.top_n_hit_rate.items()})
        return row


def score_predictions(
    name: str,
    predicted: pd.Series,
    actual: pd.Series,
    top_ns: Sequence[int] = (10, 25, 50, 100),
) -> BacktestMetrics:
    """Compare a prediction against realised value.

    Rank correlation is the headline, not squared error: drafting is a ranking
    problem. Being uniformly 10% low costs nothing if the order is right.
    """
    joined = pd.DataFrame({"pred": predicted, "actual": actual}).dropna()
    if len(joined) < 3:
        return BacktestMetrics(name, len(joined), np.nan, np.nan, np.nan, np.nan)

    predicted_values = joined["pred"].to_numpy(dtype=float)
    actual_values = joined["actual"].to_numpy(dtype=float)

    spearman = float(scipy_stats.spearmanr(predicted_values, actual_values).statistic)
    pearson = float(scipy_stats.pearsonr(predicted_values, actual_values).statistic)

    # Rank agreement in the region that actually matters - the top of the board.
    predicted_rank = (-joined["pred"]).rank(method="min")
    actual_rank = (-joined["actual"]).rank(method="min")
    hit_rates: dict[int, float] = {}
    for n in top_ns:
        if n > len(joined):
            continue
        predicted_top = set(joined.index[predicted_rank <= n])
        actual_top = set(joined.index[actual_rank <= n])
        hit_rates[n] = len(predicted_top & actual_top) / n

    return BacktestMetrics(
        name=name,
        n_players=len(joined),
        spearman=spearman,
        pearson=pearson,
        mae=float(np.mean(np.abs(predicted_values - actual_values))),
        rmse=float(np.sqrt(np.mean((predicted_values - actual_values) ** 2))),
        top_n_hit_rate=hit_rates,
        mean_rank_error=float(np.mean(np.abs(predicted_rank - actual_rank))),
    )


def _season_actuals(scored: pd.DataFrame, season: str) -> pd.DataFrame:
    """Realised per-game and total fantasy value for one season."""
    subset = scored[scored["season"] == season]
    if subset.empty:
        return pd.DataFrame()
    grouped = subset.groupby("player_id")
    return pd.DataFrame(
        {
            "actual_fp_game": grouped["fantasy_points"].mean(),
            "actual_total_fp": grouped["fantasy_points"].sum(),
            "actual_games": grouped.size(),
        }
    )


def backtest_projections(
    scored_logs: pd.DataFrame,
    players: pd.DataFrame,
    cfg,
    target_season: str,
    min_games_prior: int = 20,
    min_games_target: int = 20,
    include_bonus: bool = True,
) -> tuple[list[BacktestMetrics], pd.DataFrame]:
    """Walk-forward test: project ``target_season`` using only earlier seasons.

    Returns (metrics per method, the per-player comparison frame).

    Baselines, in increasing order of difficulty:
      * ``prior_fp_game``    - last season's FP/game. The one to beat.
      * ``prior_total_fp``   - last season's total, which conflates rate with health.
      * ``prior_minutes``    - minutes alone, a pure opportunity proxy.
      * ``model``            - our projection.
    """
    seasons = sorted(scored_logs["season"].dropna().unique())
    if target_season not in seasons:
        raise ValueError(f"target season {target_season} not present in the data")
    prior_seasons = [s for s in seasons if s < target_season]
    if not prior_seasons:
        raise ValueError(
            f"no seasons before {target_season}; a backtest needs at least one "
            "prior season to train on"
        )

    # --- STRICT: everything below sees only prior_seasons ---
    train = scored_logs[scored_logs["season"].isin(prior_seasons)]
    actuals = _season_actuals(scored_logs, target_season)
    actuals = actuals[actuals["actual_games"] >= min_games_target]
    if actuals.empty:
        raise ValueError(f"no players with >= {min_games_target} games in {target_season}")

    most_recent = max(prior_seasons)
    recent = train[train["season"] == most_recent]
    recent_grouped = recent.groupby("player_id")
    prior = pd.DataFrame(
        {
            "prior_fp_game": recent_grouped["fantasy_points"].mean(),
            "prior_total_fp": recent_grouped["fantasy_points"].sum(),
            "prior_minutes": recent_grouped["minutes"].mean(),
            "prior_games": recent_grouped.size(),
        }
    )
    prior = prior[prior["prior_games"] >= min_games_prior]

    # --- our model, trained only on prior seasons ---
    engine = ScoringEngine(cfg.scoring)
    per36 = compute_per36(train)
    projections = project_players(per36, players, cfg.model, cfg.assumptions)
    profile_index = {p.player_id: p for p in build_profiles(train, cfg.model)}

    from src.valuation import project_bonus_points

    model_rows = {}
    for projection in projections:
        profile = profile_index.get(projection.player_id)
        if profile is None:
            continue
        base = linear_score(projection.projected_stats, engine)
        bonus = project_bonus_points(profile, projection.projected_stats, engine) if include_bonus else 0.0
        model_rows[projection.player_id] = base + bonus
    model = pd.Series(model_rows, name="model_fp_game")

    comparison = actuals.join(prior, how="inner").join(model, how="left").dropna(subset=["model_fp_game"])
    if comparison.empty:
        raise ValueError("no players survived the join between prior seasons and the target season")

    metrics = [
        score_predictions("model", comparison["model_fp_game"], comparison["actual_fp_game"]),
        score_predictions("baseline_prior_fp_game", comparison["prior_fp_game"], comparison["actual_fp_game"]),
        score_predictions("baseline_prior_total_fp", comparison["prior_total_fp"], comparison["actual_fp_game"]),
        score_predictions("baseline_prior_minutes", comparison["prior_minutes"], comparison["actual_fp_game"]),
    ]
    return metrics, comparison


@dataclass
class LockInBacktestResult:
    """What each locking policy would actually have banked, on real weeks."""

    strategy_totals: Mapping[str, float]
    strategy_per_week: Mapping[str, float]
    n_weeks: int
    n_players: int

    def to_frame(self) -> pd.DataFrame:
        rows = [
            {
                "strategy": name,
                "total_fp": round(total, 1),
                "fp_per_week": round(self.strategy_per_week[name], 3),
            }
            for name, total in sorted(self.strategy_totals.items(), key=lambda kv: -kv[1])
        ]
        frame = pd.DataFrame(rows)
        if not frame.empty:
            best = frame["fp_per_week"].max()
            frame["gap_vs_best"] = (frame["fp_per_week"] - best).round(3)
        return frame


def backtest_lockin_strategies(
    scored_logs: pd.DataFrame,
    cfg,
    season: str | None = None,
    min_games: int = 20,
    week_start_weekday: int = 1,
) -> LockInBacktestResult:
    """Replay every locking policy over real, chronological fantasy weeks.

    This is the strongest evidence available for how to play Lock-In, because it
    involves no projection and no distributional assumption - it replays what
    actually happened, week by week, in the real order the games occurred.

    Each player's decision thresholds are fitted on their *own history excluding
    the current season* where possible, falling back to a leave-one-week-out
    estimate. Fitting on the same week you are grading would leak the answer.
    """
    logs = scored_logs if season is None else scored_logs[scored_logs["season"] == season]
    if logs.empty:
        raise ValueError("no game logs to backtest")

    lockin_cfg = cfg.model.get("lockin", {})
    threshold_fp = float(lockin_cfg.get("threshold_fp", 40.0))
    percentile = float(lockin_cfg.get("percentile", 70))
    auto_lock = cfg.league.lock_in.get("auto_lock_fallback", "last_game")

    strategies = {
        "threshold": ThresholdStrategy(threshold=threshold_fp),
        "percentile": PercentileStrategy(percentile=percentile),
        "optimal_iid": OptimalIIDStrategy(),
    }
    names = [PERFECT, LAST_GAME, SECRETARY, *strategies]
    totals = {name: 0.0 for name in names}
    week_count = 0
    player_count = 0

    from src.lockin.simulator import simulate_week

    for player_id, group in logs.groupby("player_id", sort=False):
        if len(group) < min_games:
            continue
        ordered = group.sort_values("game_date")
        weeks = reconstruct_weeks(
            ordered["game_date"], ordered["fantasy_points"], week_start_weekday
        )
        if len(weeks) < 3:
            continue
        player_count += 1

        for index, week in enumerate(weeks):
            if not week:
                continue
            week_count += 1

            # Leave-one-week-out: fit thresholds on every OTHER week, so the week
            # being graded never informs the decision rule used on it.
            history = [fp for j, other in enumerate(weeks) if j != index for fp in other]
            sample = np.asarray(history, dtype=float)
            if sample.size == 0:
                sample = np.asarray(week, dtype=float)

            ctx = PlayerContext(
                distribution=sample,
                continuation_values=continuation_values(sample, max_games=max(8, len(week))),
            )

            totals[PERFECT] += max(week)
            totals[LAST_GAME] += week[-1]
            totals[SECRETARY] += simulate_secretary_week(week, auto_lock=auto_lock)
            for name, strategy in strategies.items():
                totals[name] += simulate_week(week, strategy, ctx, auto_lock).locked_value

    if week_count == 0:
        raise ValueError(
            f"no player had >= {min_games} games and >= 3 reconstructable weeks"
        )

    return LockInBacktestResult(
        strategy_totals=totals,
        strategy_per_week={k: v / week_count for k, v in totals.items()},
        n_weeks=week_count,
        n_players=player_count,
    )


# =========================================================================
# Weight tuning - handoff sec.6: "The exact weights should be empirically
# tested rather than assumed."
# =========================================================================

# Candidate recency profiles, most-recent season first. The right answer depends
# on how fast players actually change year to year, which is an empirical
# question - heavy recency wins when year-over-year drift is large, and longer
# blends win when it is small and single seasons are noisy.
WEIGHT_PROFILES: dict[str, list[float]] = {
    "last_season_only":   [1.00],
    "very_recent":        [0.80, 0.20],
    "recent":             [0.70, 0.20, 0.10],
    "handoff_default":    [0.60, 0.30, 0.10],
    "balanced":           [0.50, 0.30, 0.20],
    "flat":               [0.34, 0.33, 0.33],
}


def tune_projection_weights(
    scored_logs: pd.DataFrame,
    players: pd.DataFrame,
    cfg,
    target_season: str,
    shrinkage_values: Sequence[float] = (0, 10, 20, 40),
    profiles: Mapping[str, Sequence[float]] | None = None,
) -> pd.DataFrame:
    """Grid-search season weights and shrinkage against a held-out season.

    Returns one row per configuration, sorted by Spearman correlation.

    **Read the result with suspicion.** Picking the best row from a grid searched
    on a single held-out season is itself a form of overfitting - with three
    seasons of data there is only one honest test, and tuning consumes it. The
    output is a guide to which region of the parameter space is sensible, not a
    licence to adopt the top row. Prefer a profile that is good across several
    shrinkage values over one that spikes at a single point.
    """
    from src.config import config_with_overrides

    profiles = profiles or WEIGHT_PROFILES
    seasons = sorted(scored_logs["season"].dropna().unique())
    prior_seasons = [s for s in seasons if s < target_season]
    if not prior_seasons:
        raise ValueError(f"no seasons before {target_season} to train on")

    ordered_prior = sorted(prior_seasons, reverse=True)   # most recent first
    rows = []

    for profile_name, weights in profiles.items():
        usable = list(weights)[: len(ordered_prior)]
        if not usable:
            continue
        season_weights = {season: float(w) for season, w in zip(ordered_prior, usable)}

        for shrinkage in shrinkage_values:
            candidate = config_with_overrides(
                {
                    "model": {
                        "projection": {
                            "season_weights": season_weights,
                            "shrinkage_games_k": float(shrinkage),
                        }
                    }
                },
                base=cfg,
            )
            try:
                metrics, _ = backtest_projections(scored_logs, players, candidate, target_season)
            except ValueError:
                continue

            model = next((m for m in metrics if m.name == "model"), None)
            baseline = next((m for m in metrics if m.name == "baseline_prior_fp_game"), None)
            if model is None:
                continue
            rows.append(
                {
                    "profile": profile_name,
                    "weights": "/".join(f"{w:.2f}" for w in usable),
                    "shrinkage_k": shrinkage,
                    "spearman": round(model.spearman, 4),
                    "vs_baseline": round(model.spearman - baseline.spearman, 4) if baseline else np.nan,
                    "mae": round(model.mae, 3),
                    "top25_hit": round(model.top_n_hit_rate.get(25, np.nan), 3),
                }
            )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("spearman", ascending=False).reset_index(drop=True)


# =========================================================================
# Diagnosis - when the model loses to a baseline, find out WHY
# =========================================================================

def bootstrap_spearman_difference(
    comparison: pd.DataFrame,
    model_column: str = "model_fp_game",
    baseline_column: str = "prior_fp_game",
    actual_column: str = "actual_fp_game",
    n_boot: int = 2000,
    seed: int = 20262027,
) -> dict:
    """Is the model-vs-baseline gap real, or sampling noise?

    A raw difference in Spearman is easy to over-read. Both numbers are computed
    on the *same* players, so resampling players (rather than comparing two
    independent standard errors) is the right way to ask whether the ordering
    would survive a different draw of the league.

    Returns the observed difference, a 95% interval, and how often the model wins
    across resamples. If the interval straddles zero, the two methods are not
    distinguishable on this much data and preferring either on the basis of this
    number alone is overfitting to one season.
    """
    rng = np.random.default_rng(seed)
    frame = comparison[[model_column, baseline_column, actual_column]].dropna()
    n = len(frame)
    if n < 20:
        return {"n": n, "observed": np.nan, "insufficient_data": True}

    model = frame[model_column].to_numpy(dtype=float)
    baseline = frame[baseline_column].to_numpy(dtype=float)
    actual = frame[actual_column].to_numpy(dtype=float)

    def spearman(x, y):
        return float(scipy_stats.spearmanr(x, y).statistic)

    observed = spearman(model, actual) - spearman(baseline, actual)

    differences = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        # A resample can degenerate (all-identical values); skip those draws.
        try:
            differences[i] = spearman(model[idx], actual[idx]) - spearman(baseline[idx], actual[idx])
        except Exception:  # noqa: BLE001
            differences[i] = np.nan

    differences = differences[np.isfinite(differences)]
    return {
        "n": n,
        "observed": observed,
        "ci_low": float(np.percentile(differences, 2.5)),
        "ci_high": float(np.percentile(differences, 97.5)),
        "p_model_better": float(np.mean(differences > 0)),
        "insufficient_data": False,
    }


# Each ablation turns ONE piece of the model off. Comparing them against the
# full model shows which components earn their place and which are adding noise.
ABLATIONS: dict[str, dict] = {
    "no_age_curve": {"model": {"projection": {"age_curve": {}}}},
    "no_shrinkage": {"model": {"projection": {"shrinkage_games_k": 0}}},
    "last_season_only": {},          # handled specially - needs the season list
    "no_bonus_projection": {},       # handled specially - a function argument
}


def diagnose_projection(
    scored_logs: pd.DataFrame,
    players: pd.DataFrame,
    cfg,
    target_season: str,
) -> pd.DataFrame:
    """Ablation study: switch each model component off and see what happens.

    A component whose removal IMPROVES the score is actively hurting. That is the
    thing to find when the model loses to a naive baseline, and it is far more
    informative than a single aggregate number telling you something is wrong.
    """
    from src.config import config_with_overrides

    seasons = sorted(scored_logs["season"].dropna().unique())
    prior_seasons = [s for s in seasons if s < target_season]
    if not prior_seasons:
        raise ValueError(f"no seasons before {target_season} to train on")
    most_recent = max(prior_seasons)

    rows = []

    def score_variant(label: str, variant_cfg, include_bonus: bool = True) -> float | None:
        try:
            metrics, _ = backtest_projections(
                scored_logs, players, variant_cfg, target_season, include_bonus=include_bonus
            )
        except ValueError:
            return None
        model = next((m for m in metrics if m.name == "model"), None)
        baseline = next((m for m in metrics if m.name == "baseline_prior_fp_game"), None)
        if model is None:
            return None
        rows.append(
            {
                "variant": label,
                "spearman": round(model.spearman, 4),
                "vs_baseline": round(model.spearman - baseline.spearman, 4) if baseline else np.nan,
                "top25_hit": round(model.top_n_hit_rate.get(25, np.nan), 3),
                "mae": round(model.mae, 3),
            }
        )
        return model.spearman

    full = score_variant("FULL MODEL", cfg)

    score_variant("no_age_curve", config_with_overrides(ABLATIONS["no_age_curve"], base=cfg))
    score_variant("no_shrinkage", config_with_overrides(ABLATIONS["no_shrinkage"], base=cfg))
    score_variant(
        "last_season_only",
        config_with_overrides(
            {"model": {"projection": {"season_weights": {most_recent: 1.0}}}}, base=cfg
        ),
    )
    score_variant("no_bonus_projection", cfg, include_bonus=False)

    frame = pd.DataFrame(rows)
    if not frame.empty and full is not None:
        # Positive delta = the model is BETTER without this component.
        frame["removing_helps_by"] = (frame["spearman"] - full).round(4)
        frame.loc[frame["variant"] == "FULL MODEL", "removing_helps_by"] = np.nan
    return frame
