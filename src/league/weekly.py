"""Translate game availability into a conditional weekly Lock-In value."""
from math import comb, isfinite


def played_games_pmf(schedule_pmf, game_availability, correlation=0.5):
    """Mixture of independent missed games and whole-week absences.

    correlation=0 gives Binomial(n,p); correlation=1 gives all-or-none weeks.
    The mixture preserves E[played games]=n*p. Correlation is an explicit,
    uncalibrated assumption, not measured weekly injury probability.
    """
    p, c = float(game_availability), float(correlation)
    if not isfinite(p) or not 0 <= p <= 1 or not 0 <= c <= 1:
        raise ValueError('Availability and correlation must be between 0 and 1')
    total = sum(schedule_pmf.values())
    if not isfinite(total) or total <= 0:
        raise ValueError('Empty schedule probability mass')
    result = {}
    for n, weight in schedule_pmf.items():
        if int(n) != n or n < 0 or not isfinite(weight) or weight < 0:
            raise ValueError('Invalid schedule probability')
        n = int(n)
        for k in range(n + 1):
            mass = (1-c) * comb(n, k) * p**k * (1-p)**(n-k)
            mass += c * ((1-p if k == 0 else 0) + (p if k == n else 0))
            result[k] = result.get(k, 0) + weight / total * mass
    return result


def weekly_value(sample, schedule_pmf, availability, correlation=0.5):
    """Weekly Lock-In value given at least one playable game, and that probability.

    Uses the advisor's rule (lock_advisor.continuation): appearances are not
    known in advance and a DNP in the team's final game scores 0. The
    correlation share of weeks is all-or-none: every game or no game.
    """
    from src.league.lock_advisor import continuation
    pmf = played_games_pmf(schedule_pmf, availability, correlation)
    usable = 1 - pmf.get(0, 0)
    if usable <= 1e-12:
        return 0.0, 0.0
    sample = [float(x) for x in sample]
    p, c = float(availability), float(correlation)
    total = sum(schedule_pmf.values())
    expected = 0.0
    for n, weight in schedule_pmf.items():
        if int(n) > 0 and weight > 0:
            independent = continuation(sample, int(n), p)
            whole_week = p * continuation(sample, int(n), 1.0)
            expected += weight / total * ((1 - c) * independent + c * whole_week)
    return expected / usable, usable
