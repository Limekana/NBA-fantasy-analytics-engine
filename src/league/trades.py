"""Two-sided orchestration around the existing exact roster-strength function."""
from __future__ import annotations
from dataclasses import asdict, replace
import math

from src.trade import apply_trade, trim_roster, roster_strength, _scale, TradeEvaluation, TradeSide, ExactAvailabilityConfig
from src.league.replacements import fill_opened_slots
from src.league.profiles import profile


def side_result(before, after, give, get, cuts, additions, cfg, weeks, stress):
    # Players on both rosters (e.g. a claim also in the free-move baseline) are not stressed.
    incoming = {p.player_id for p in get + additions} - {p.player_id for p in before}
    outgoing = {p.player_id for p in give + cuts}
    def delta(sign):
        b = [_scale(p, 1 + sign * stress) if p.player_id in outgoing else p for p in before]
        a = [_scale(p, 1 - sign * stress) if p.player_id in incoming else p for p in after]
        return roster_strength(a, cfg).expected_weekly - roster_strength(b, cfg).expected_weekly
    value = TradeEvaluation(roster_strength(before, cfg), roster_strength(after, cfg),
                            TradeSide(tuple(give)), TradeSide(tuple(get)), tuple(p.name for p in cuts),
                            weeks, stress, delta(1), delta(-1))
    risks = {p.player_id: p.injury_status for p in before + after if p.health_risk}
    def health_delta(sign=0):
        # Sensitivity scenario, NOT a claim that these players miss the season.
        b = [replace(_scale(p, 1 + sign * stress) if p.player_id in outgoing else p,
                     availability=0.0) if p.health_risk else
             (_scale(p, 1 + sign * stress) if p.player_id in outgoing else p) for p in before]
        a = [replace(_scale(p, 1 - sign * stress) if p.player_id in incoming else p,
                     availability=0.0) if p.health_risk else
             (_scale(p, 1 - sign * stress) if p.player_id in incoming else p) for p in after]
        return roster_strength(a, cfg).expected_weekly - roster_strength(b, cfg).expected_weekly
    return {'before': value.before.expected_weekly, 'after': value.after.expected_weekly,
            'injury_risks': risks, 'health_delta': health_delta() if risks else value.delta_weekly,
            'health_pessimistic': health_delta(1) if risks else value.pessimistic_weekly,
            'traded_injury_risk': any(p.health_risk for p in give + get + additions + cuts),
            'delta_weekly': value.delta_weekly, 'delta_starters': value.delta_starters,
            'delta_depth': value.delta_depth, 'delta_season': value.delta_season,
            'pessimistic': value.pessimistic_weekly, 'optimistic': value.optimistic_weekly,
            'drops': [p.player_id for p in cuts], 'pickups': [p.player_id for p in additions],
            'before_profile': profile(before, cfg), 'after_profile': profile(after, cfg)}


def free_moves(roster, pool, claims, cfg):
    """Up to len(claims) positive standalone add/drops over the top-valued free
    agents plus the claimed players; mirrors freeLeagueMoves in league_math.js."""
    from src.league.replacements import add_drop_candidates
    top = sorted(pool, key=lambda p: (-p.weekly_value, p.player_id))[:10]
    pool_ids, top_ids = {p.player_id for p in pool}, {p.player_id for p in top}
    candidates = top + [p for p in claims if p.player_id in pool_ids and p.player_id not in top_ids]
    after, moves = list(roster), []
    for _ in claims:
        have = {p.player_id for p in after}
        best = add_drop_candidates(after, [p for p in candidates if p.player_id not in have], cfg, 1)
        if not best or best[0]['delta_weekly'] <= 1e-8:
            break
        incoming = next(p for p in candidates if p.player_id == best[0]['add'])
        after, _cuts = trim_roster(after + [incoming], cfg)
        moves.append({'add': best[0]['add'], 'drop': best[0]['drop'], 'gain': best[0]['delta_weekly']})
    return after, moves


def classify(a, b, tolerance=1e-8):
    if a.get('traded_injury_risk') or b.get('traded_injury_risk'):
        return 'INJURY REVIEW REQUIRED'
    def sign(r):
        if min(r['pessimistic'], r.get('health_pessimistic', r['pessimistic'])) > tolerance:
            return 1
        if r['optimistic'] < -tolerance:
            return -1
        return 0
    sa, sb = sign(a), sign(b)
    return {(1, 1): 'WIN-WIN', (1, -1): 'FAVOURS TEAM A', (-1, 1): 'FAVOURS TEAM B',
            (-1, -1): 'LOSE-LOSE'}.get((sa, sb), 'TOO CLOSE TO CALL')


def evaluate_two_team(roster_a, roster_b, give_a, give_b, cfg, weeks=22, stress=.1,
                      free_agents=(), include_replacements=True):
    cfg = ExactAvailabilityConfig(cfg)
    if weeks < 0 or not math.isfinite(stress) or not 0 <= stress <= 1:
        raise ValueError('Invalid weeks or stress')
    if len(roster_a) > cfg.roster_size or len(roster_b) > cfg.roster_size:
        raise ValueError('Resolve reserve/IR before evaluating over-limit rosters')
    ids_a, ids_b = {p.player_id for p in roster_a}, {p.player_id for p in roster_b}
    if ids_a & ids_b:
        raise ValueError('Duplicate ownership across teams')
    pool_ids = [p.player_id for p in free_agents]
    if len(pool_ids) != len(set(pool_ids)) or set(pool_ids) & (ids_a | ids_b):
        raise ValueError('Free-agent pool contains owned/duplicate players')
    a_out = [p for p in roster_a if p.player_id in set(give_a)]
    b_out = [p for p in roster_b if p.player_id in set(give_b)]
    after_a = apply_trade(roster_a, give_a, b_out)
    after_b = apply_trade(roster_b, give_b, a_out)
    after_a, cuts_a = trim_roster(after_a, cfg)
    after_b, cuts_b = trim_roster(after_b, cfg)
    adds_a, adds_b = [], []
    if include_replacements:
        # Only the net sender opens slots in an ordinary two-team player trade.
        # Opponent cuts enter the hypothetical post-waiver pool; claims are not guaranteed.
        after_a, adds_a = fill_opened_slots(len(roster_a), after_a, list(free_agents) + cuts_b, cfg)
        taken = {p.player_id for p in adds_a}
        after_b, adds_b = fill_opened_slots(len(roster_b), after_b,
                         [p for p in list(free_agents) + cuts_a if p.player_id not in taken], cfg)
    # A claim is only trade value beyond the add/drop the side could make anyway,
    # so a side receiving claims is compared with its roster after as many free moves.
    base_a, moves_a = free_moves(roster_a, list(free_agents), adds_a, cfg) if adds_a else (roster_a, [])
    base_b, moves_b = free_moves(roster_b, list(free_agents), adds_b, cfg) if adds_b else (roster_b, [])
    a = side_result(base_a, after_a, a_out, b_out, cuts_a, adds_a, cfg, weeks, stress)
    b = side_result(base_b, after_b, b_out, a_out, cuts_b, adds_b, cfg, weeks, stress)
    if adds_a:
        a['baseline_free_moves'] = moves_a
    if adds_b:
        b['baseline_free_moves'] = moves_b
    return {'team_a': a, 'team_b': b, 'classification': classify(a, b),
            'availability_integration': 'Exact joint independent weekly availability',
            'health_assumption': 'Sensitivity: all injury-flagged players unavailable together; not a season-long absence forecast. Blank injury fields do not verify health.',
            'replacement_assumption': 'Best pool claim after waiver clearance; not a guaranteed acquisition',
            'stress_assumption': 'Fixed selected cuts/pickups, stressed projections; not a confidence interval'}
