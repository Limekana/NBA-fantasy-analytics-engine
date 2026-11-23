"""Roster effects measured in weekly fantasy points, with explicit coverage."""
from __future__ import annotations
from dataclasses import asdict
import math

from src.trade import roster_strength, optimal_lineup, expand_slots, eligibility_map


def profile(players, cfg):
    strength = roster_strength(players, cfg)
    lineup_ids = set(strength.lineup.starter_ids)
    # Expected-strength removal also prices a bench player's insurance contribution.
    removal = {p.player_id: strength.expected_weekly - roster_strength(
        [q for q in players if q.player_id != p.player_id], cfg).expected_weekly for p in players}
    starters = [p for p in players if p.player_id in lineup_ids]
    no_bench = roster_strength(starters, cfg)
    elig = eligibility_map(cfg)
    coverage, weakness = {}, {}
    for slot in dict.fromkeys(expand_slots(cfg)):
        eligible = [p for p in players if slot in (set(p.slots) if p.slots else
                    {s for pos in (p.positions or (p.position,)) for s in elig.get(pos, ())})]
        coverage[slot] = len(eligible)
        # Stress all eligible players for this slot absent: overlapping exposures,
        # not an additive decomposition. It includes the opportunity cost of UTIL.
        ids = {p.player_id for p in eligible}
        weakness[slot] = strength.healthy_weekly - optimal_lineup(
            [p for p in players if p.player_id not in ids], cfg).total
    total = strength.healthy_weekly
    concentration = sum((p.weekly_value / total)**2 for p in starters) if total else None
    positional = {}
    for position in cfg.position_eligibility:
        assets = sorted([p for p in players if position in (p.positions or (p.position,))],
                        key=lambda p: (removal[p.player_id], p.player_id))
        positional[position] = {'players': len(assets),
            'lowest_removal_cost': removal[assets[0].player_id] if assets else None,
            'most_replaceable_asset': assets[0].player_id if assets else None,
            'highest_removal_cost': removal[assets[-1].player_id] if assets else None}
    return {
        'healthy_weekly': total, 'expected_weekly': strength.expected_weekly,
        'absence_cost': strength.depth_cost,
        'bench_insurance': strength.expected_weekly - no_bench.expected_weekly,
        'lineup': list(strength.lineup.assignments), 'bench': list(strength.lineup.bench),
        'healthy_marginal': {p.player_id: strength.marginal_value.get(p.player_id, 0) for p in players},
        'expected_marginal': removal, 'coverage': coverage, 'slot_absence_exposure': weakness,
        'concentration_hhi': concentration,
        'positional_redundancy': positional,
        'missing_slots': len(expand_slots(cfg)) - len(strength.lineup.assignments),
    }


def relative_profiles(profiles):
    """Percentile among the other teams; ties count half. No invented scale."""
    keys = ('expected_weekly', 'bench_insurance', 'absence_cost', 'concentration_hhi')
    for tid, record in profiles.items():
        record['league_relative'] = {}
        for key in keys:
            x = record[key]
            others = [p[key] for t, p in profiles.items() if t != tid and p[key] is not None]
            if x is None or not others:
                continue
            mean = sum(others) / len(others)
            sd = math.sqrt(sum((y-mean)**2 for y in others) / len(others))
            record['league_relative'][key] = {
                'percentile': 100 * (sum(y < x for y in others) + .5 * sum(y == x for y in others)) / len(others),
                'z_score': (x-mean)/sd if sd > 1e-12 else None,
                'comparison_teams': len(others),
                'higher_is_better': key in ('expected_weekly', 'bench_insurance'),
            }
    return profiles
