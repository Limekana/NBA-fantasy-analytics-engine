"""Free-agent decisions measured by expected lineup changes."""
from src.trade import roster_strength, trim_roster


def best_add(roster, pool, cfg):
    have = {p.player_id for p in roster}
    baseline = roster_strength(roster, cfg).expected_weekly
    candidates = [(roster_strength(roster + [p], cfg).expected_weekly - baseline, p)
                  for p in pool if p.player_id not in have and not p.health_risk]
    if not candidates:
        return None, 0.0
    gain, player = max(candidates, key=lambda x: (round(x[0], 8), x[1].player_id))
    return player, gain


def fill_opened_slots(before_count, after, pool, cfg):
    """Fill slots newly opened by this trade; does not reward pre-existing holes."""
    after = list(after)
    additions = []
    room = min(cfg.roster_size - len(after), max(0, before_count - len(after)))
    for _ in range(room):
        candidate, gain = best_add(after, pool, cfg)
        if candidate is None or gain < -1e-8:
            break
        additions.append(candidate)
        after.append(candidate)
    return after, additions


def add_drop_candidates(roster, pool, cfg, top=20):
    baseline = roster_strength(roster, cfg).expected_weekly
    results = []
    for incoming in pool:
        if incoming.health_risk:
            continue
        if incoming.player_id in {p.player_id for p in roster}:
            continue
        after, cuts = trim_roster(roster + [incoming], cfg)
        if incoming not in after:
            continue
        results.append({'add': incoming.player_id, 'drop': [p.player_id for p in cuts],
                        'delta_weekly': roster_strength(after, cfg).expected_weekly - baseline})
    return sorted(results, key=lambda r: (-r['delta_weekly'], r['add']))[:top]


def replaceability(roster, pool, cfg):
    baseline = roster_strength(roster, cfg).expected_weekly
    results = {}
    for player in roster:
        remaining = [p for p in roster if p.player_id != player.player_id]
        replacement, _ = best_add(remaining, pool, cfg)
        after = remaining + ([replacement] if replacement else [])
        results[player.player_id] = {'replacement': replacement.player_id if replacement else None,
            'loss_after_replacement': baseline - roster_strength(after, cfg).expected_weekly}
    return results


def positional_market_gaps(roster, pool, cfg):
    """Best legal add/drop gain available at each position; overlapping categories."""
    result = {}
    for pos in cfg.position_eligibility:
        moves = add_drop_candidates(roster, [p for p in pool if pos in (p.positions or (p.position,))], cfg, 1)
        result[pos] = moves[0] if moves else None
    return result
