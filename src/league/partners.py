"""Complementarity from measured relocation effects, never raw player sums."""
from src.trade import roster_strength


def relocation(roster_from, roster_to, cfg):
    source = roster_strength(roster_from, cfg).expected_weekly
    target = roster_strength(roster_to, cfg).expected_weekly
    results = []
    for p in roster_from:
        if p.health_risk:
            continue  # Do not advertise unresolved injury assets as useful offers.
        loss = source - roster_strength([q for q in roster_from if q.player_id != p.player_id], cfg).expected_weekly
        gain = roster_strength(roster_to + [p], cfg).expected_weekly - target
        results.append({'player_id': p.player_id, 'name': p.name, 'positions': list(p.positions or (p.position,)),
                        'source_removal_cost': loss, 'destination_addition_gain': gain,
                        'relocation_gain': gain - loss})
    return sorted(results, key=lambda x: (-x['relocation_gain'], x['player_id']))


def partners(team_id, rosters, cfg):
    """Screening score in FP/week; not an actual proposal or predicted acceptance.

    Adds are hypothetical before cuts, so a high score requires full two-sided
    evaluation. Score = min(best positive relocation gain in each direction).
    """
    results = []
    for other_id, other in rosters.items():
        if other_id == team_id:
            continue
        outgoing = relocation(rosters[team_id], other, cfg)
        incoming = relocation(other, rosters[team_id], cfg)
        a = max(0, outgoing[0]['relocation_gain']) if outgoing else 0
        b = max(0, incoming[0]['relocation_gain']) if incoming else 0
        results.append({'team_id': other_id, 'compatibility_fp_week': min(a, b),
                        'useful_outgoing': [x for x in outgoing if x['relocation_gain'] > 1e-8][:5],
                        'useful_incoming': [x for x in incoming if x['relocation_gain'] > 1e-8][:5]})
    return sorted(results, key=lambda r: (-r['compatibility_fp_week'], r['team_id']))
