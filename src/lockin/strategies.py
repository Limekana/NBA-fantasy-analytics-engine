"""Lock-In decision strategies.

Handoff sec.11. A strategy answers one question, repeatedly, during a fantasy
week: *the player just scored F, with k games still to come this week - do I lock
this game, or gamble on a better one?*

Every strategy is a callable with the signature::

    decide(score, games_remaining, context) -> bool   # True == lock now

``games_remaining`` counts games AFTER the one just played. When it is 0 the
decision is forced by the league's auto-lock rule, so strategies are never
consulted.

The strategies deliberately span the full realism spectrum, because the handoff
insists the clairvoyant number be labelled as an upper bound rather than
presented as a projection:

    perfect      oracle; sees the whole week in advance.       UPPER BOUND
    last_game    never decides; takes Sleeper's auto-lock.     LOWER BOUND
    threshold    lock at a fixed FP number.                    naive-realistic
    percentile   lock above the player's own p-th percentile.  self-calibrating
    optimal_iid  optimal stopping against the player's own     realistic
                 distribution, with no future knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

import numpy as np


@dataclass(frozen=True)
class PlayerContext:
    """What a strategy is allowed to know about the player before the week.

    Crucially this contains only *historical* information - never the scores of
    games later in the current week. That separation is what keeps the realistic
    strategies honest.
    """

    distribution: np.ndarray          # historical FP sample for this player
    continuation_values: np.ndarray   # W[k] = E[best achievable with k games left]

    @property
    def mean(self) -> float:
        return float(np.mean(self.distribution)) if self.distribution.size else 0.0


class Strategy(Protocol):
    name: str

    def decide(self, score: float, games_remaining: int, ctx: PlayerContext) -> bool:
        ...


@dataclass(frozen=True)
class ThresholdStrategy:
    """Lock the first game at or above a fixed fantasy-point threshold."""

    threshold: float
    name: str = "threshold"

    def decide(self, score: float, games_remaining: int, ctx: PlayerContext) -> bool:
        return bool(score >= self.threshold)


@dataclass(frozen=True)
class PercentileStrategy:
    """Lock the first game at or above the player's own p-th percentile.

    Self-calibrating: a 55 FP game is a lock for most players and merely average
    for an elite one, and this strategy encodes that automatically.
    """

    percentile: float
    name: str = "percentile"

    def decide(self, score: float, games_remaining: int, ctx: PlayerContext) -> bool:
        if ctx.distribution.size == 0:
            return False
        return bool(score >= float(np.percentile(ctx.distribution, self.percentile)))


@dataclass(frozen=True)
class OptimalIIDStrategy:
    """Optimal stopping against the player's own historical distribution.

    Standard backward induction for the "house selling" / best-choice problem
    with a known value distribution and no recall:

        W[1] = E[F]                        (last game: forced to take it)
        W[k] = E[max(F, W[k-1])]           (k games left: take F, or continue)

    Facing a score with ``k`` games still to come, continuing is worth ``W[k]``,
    so lock iff ``score >= W[k]``. This is the best a manager can do *without*
    knowing the future - which makes the gap between this and `perfect` an honest
    measure of how much clairvoyance is worth, and the gap between this and
    `last_game` a measure of how much attention is worth.
    """

    name: str = "optimal_iid"

    def decide(self, score: float, games_remaining: int, ctx: PlayerContext) -> bool:
        if games_remaining <= 0:
            return True
        values = ctx.continuation_values
        if games_remaining >= len(values):
            # More games than we precomputed; continuation value is monotonically
            # increasing in k, so use the largest we have (slightly conservative).
            continuation = values[-1] if len(values) else ctx.mean
        else:
            continuation = values[games_remaining]
        return bool(score >= continuation)


def continuation_values(distribution: np.ndarray, max_games: int = 8) -> np.ndarray:
    """Precompute W[k] for k = 0..max_games.

    W[0] is unused (no games remain, the decision is forced). W[1] = E[F].
    Computed from the empirical distribution, so it inherits the player's real
    skew - which matters enormously for boom/bust players, whose right tail makes
    waiting far more valuable than a normal approximation would suggest.
    """
    values = np.zeros(max_games + 1, dtype=float)
    if distribution.size == 0:
        return values
    sample = np.asarray(distribution, dtype=float)
    values[1] = float(np.mean(sample))
    for k in range(2, max_games + 1):
        values[k] = float(np.mean(np.maximum(sample, values[k - 1])))
    return values


def build_strategies(model_cfg: dict) -> dict[str, Strategy]:
    """Instantiate the configured strategy set from config/model.yaml."""
    lockin_cfg = model_cfg.get("lockin", {})
    return {
        "threshold": ThresholdStrategy(threshold=float(lockin_cfg.get("threshold_fp", 40.0))),
        "percentile": PercentileStrategy(percentile=float(lockin_cfg.get("percentile", 70))),
        "optimal_iid": OptimalIIDStrategy(),
    }
