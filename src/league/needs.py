"""Transparent positional priorities, separate from total roster value.

Needs are a heuristic: below-peer leading-player quality plus above-peer
single-player outage loss. Overlapping positions are not additive FP gains.
Peer benchmarks stay fixed when comparing a hypothetical trade.
"""
from statistics import median

from src.trade import optimal_lineup, expand_slots

POSITIONS = ('PG', 'SG', 'SF', 'PF', 'C')


def position_metrics(roster, cfg):
    lineup = optimal_lineup(roster, cfg)
    starters = set(lineup.starter_ids)
    rows = {}
    for pos in POSITIONS:
        if not set(cfg.position_eligibility.get(pos, ())) & set(expand_slots(cfg)):
            continue
        players = sorted((p for p in roster if pos in (p.positions or (p.position,))),
                         key=lambda p: (-p.weekly_value * p.availability, p.player_id))
        leader = players[0] if players else None
        loss = lineup.total - optimal_lineup([p for p in roster if p != leader], cfg).total if leader else 0.0
        rows[pos] = {'position': pos, 'leader': leader.player_id if leader else None,
                     'quality': leader.weekly_value * leader.availability if leader else 0.0,
                     'outage_loss': max(0.0, loss), 'eligible_count': len(players),
                     'bench_cover': [p.player_id for p in players if p.player_id not in starters]}
    return rows


def score_needs(metrics, references):
    rows = []
    for pos, item in metrics.items():
        peer = references[pos]
        production_gap = max(0.0, peer['median_quality'] - item['quality'])
        cover_gap = max(0.0, item['outage_loss'] - peer['median_outage_loss'])
        reason = ('Production + injury cover' if production_gap > 1e-8 and cover_gap > 1e-8 else
                  'Starting production' if production_gap > 1e-8 else
                  'Injury cover / star reliance' if cover_gap > 1e-8 else 'No above-median need')
        if not item['eligible_count']:
            reason = 'No eligible player'
        rows.append({**item, 'production_rank': 1 + sum(v > item['quality'] + 1e-8 for v in peer['qualities']),
                     'teams_compared': len(peer['qualities']) + 1,
                     'median_quality': peer['median_quality'], 'median_outage_loss': peer['median_outage_loss'],
                     'production_gap': production_gap, 'cover_gap': cover_gap,
                     'need_score': production_gap + cover_gap, 'reason': reason})
    rows.sort(key=lambda r: (r['eligible_count'] > 0, -round(r['need_score'], 8), POSITIONS.index(r['position'])))
    for i, row in enumerate(rows):
        row['priority'] = i + 1
    return rows


def league_needs(rosters, cfg, expected_teams):
    if len(rosters) != expected_teams or len(rosters) < 2:
        return {}  # No silent partial-league benchmark.
    raw = {tid: position_metrics(roster, cfg) for tid, roster in rosters.items()}
    result = {}
    for tid, metrics in raw.items():
        references = {}
        for pos in metrics:
            peers = [r[pos] for other, r in raw.items() if other != tid]
            references[pos] = {'qualities': [p['quality'] for p in peers],
                               'median_quality': median(p['quality'] for p in peers),
                               'median_outage_loss': median(p['outage_loss'] for p in peers)}
        result[tid] = {'rows': score_needs(metrics, references), 'references': references}
    return result


def trade_needs(before, after, cfg, references):
    b = score_needs(position_metrics(before, cfg), references)
    a = {r['position']: r for r in score_needs(position_metrics(after, cfg), references)}
    return [{'position': r['position'], 'before': r['need_score'], 'after': a[r['position']]['need_score'],
             'improvement': r['need_score'] - a[r['position']]['need_score'],
             'leader_changed': r['leader'] != a[r['position']]['leader'],
             'leader_before': r['leader'], 'leader_after': a[r['position']]['leader'],
             'eligible_before': r['eligible_count'], 'eligible_after': a[r['position']]['eligible_count'],
             'healthy_lineup_gain': optimal_lineup(after, cfg).total - optimal_lineup(before, cfg).total,
             'lineup_gain_without_original_leader':
                 optimal_lineup([p for p in after if p.player_id != r['leader']], cfg).total -
                 optimal_lineup([p for p in before if p.player_id != r['leader']], cfg).total,
             # A weaker replacement leader naturally costs less to lose. This
             # is not evidence of better backup coverage. Withhold positive
             # cover credit when the stressed player changes; keep any penalty.
             'search_improvement': r['production_gap'] - a[r['position']]['production_gap'] +
                 (min(0.0, r['cover_gap'] - a[r['position']]['cover_gap'])
                  if r['leader'] != a[r['position']]['leader'] else r['cover_gap'] - a[r['position']]['cover_gap']),
             'production_change': a[r['position']]['quality'] - r['quality'],
             'outage_loss_change': a[r['position']]['outage_loss'] - r['outage_loss']} for r in b]
