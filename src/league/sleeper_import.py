"""Normalize Sleeper payloads without assuming draft slots equal roster IDs."""
from __future__ import annotations
from datetime import datetime, timezone
from src.league.models import Team, Transfer, LeagueEvent
from src.draft.simulator import snake_pick_order


def iso_time(ms):
    return datetime.fromtimestamp(float(ms) / 1000, timezone.utc).isoformat() if ms is not None else None


def teams_from_sleeper(users, rosters, draft, manager_aliases, draft_order=()):
    user_by_id = {str(u['user_id']): u for u in users}
    slots = {str(tid): int(slot) for slot, tid in (draft.get('slot_to_roster_id') or {}).items()}
    if (len(slots) != len(rosters) or set(slots.values()) != set(range(1, len(rosters)+1))
            or set(slots) != {str(r['roster_id']) for r in rosters}):
        raise ValueError('Draft must supply complete slot_to_roster_id mapping')
    alias_index = {}
    for manager, aliases in manager_aliases.items():
        for alias in [manager, *aliases]:
            key = alias.casefold()
            if key in alias_index and alias_index[key] != manager:
                raise ValueError('Conflicting manager aliases')
            alias_index[key] = manager
    teams = []
    for roster in rosters:
        tid, uid = str(roster['roster_id']), str(roster.get('owner_id') or '')
        user = user_by_id.get(uid, {})
        candidates = [user.get('display_name', ''), user.get('username', ''),
                      (user.get('metadata') or {}).get('team_name', '')]
        matches = {alias_index[v.casefold()] for v in candidates if v and v.casefold() in alias_index}
        if draft_order:
            expected = draft_order[slots[tid] - 1]
            if matches and matches != {expected}:
                raise ValueError(f'Alias disagrees with reported draft order for roster {tid}')
            matches = {expected}
        if len(matches) != 1:
            raise ValueError(f'Manager mapping unresolved for roster {tid}: {candidates}')
        teams.append(Team(tid, matches.pop(), slots[tid], uid, user.get('display_name', '')))
    if len({t.manager for t in teams}) != len(teams):
        raise ValueError('One manager matched more than one roster')
    return teams


def draft_events(picks, draft, teams, catalogue, rounds):
    n = len(teams)
    if draft.get('type') != 'snake' or draft.get('status') != 'complete':
        raise ValueError('Require a completed snake draft')
    if len(picks) != n * rounds or {int(p['pick_no']) for p in picks} != set(range(1, n * rounds + 1)):
        raise ValueError('Incomplete or duplicate draft picks')
    slot_to_team = {t.draft_slot: t.team_id for t in teams}
    order = snake_pick_order(n, rounds)
    result = []
    for row in sorted(picks, key=lambda x: int(x['pick_no'])):
        number = int(row['pick_no'])
        slot = int(row['draft_slot'])
        if slot != order[number - 1] + 1:
            raise ValueError(f'Draft snake order mismatch at pick {number}')
        tid = str(row.get('roster_id') or slot_to_team[slot])
        if tid not in {t.team_id for t in teams}:
            raise ValueError(f'Unknown pick recipient: {tid}')
        pid = catalogue.resolve('sleeper:' + str(row['player_id'])).player_id
        result.append(LeagueEvent(f"draft:{draft['draft_id']}:{number}", number, 'draft',
                                 (Transfer(pid, None, tid),), source='sleeper',
                                 source_id=str(draft['draft_id']),
                                 metadata={'pick_no': number, 'round': (number-1)//n+1, 'draft_slot': slot}))
    return result


def transaction_events(payloads, start_sequence):
    by_id = {}
    for row in payloads:
        if row.get('status') != 'complete':
            continue
        if not (row.get('status_updated') or row.get('created')):
            raise ValueError('Completed transaction lacks chronology timestamps')
        tid = str(row['transaction_id'])
        if tid in by_id and by_id[tid] != row:
            raise ValueError(f'Conflicting copies of transaction {tid}')
        by_id[tid] = row
    result = []
    rows = sorted(by_id.values(), key=lambda r: (
        int(r.get('status_updated') or r.get('created') or 0), int(r['transaction_id'])))
    for row in rows:
        if row.get('draft_picks') or row.get('waiver_budget'):
            raise ValueError(f"Transaction {row['transaction_id']} contains non-player assets; explicit support required")
        if row.get('type') not in {'trade', 'waiver', 'free_agent'}:
            raise ValueError(f"Unknown Sleeper transaction type: {row.get('type')}")
        adds, drops = row.get('adds') or {}, row.get('drops') or {}
        legs = tuple(Transfer('sleeper:' + str(pid), str(drops[pid]) if pid in drops else None,
                              str(adds[pid]) if pid in adds else None)
                     for pid in sorted(set(adds) | set(drops)))
        if not legs:
            continue
        kind = 'trade' if row['type'] == 'trade' else ('add_drop' if adds and drops else 'add' if adds else 'drop')
        if row['type'] == 'waiver':
            kind = 'waiver'
        result.append(LeagueEvent('sleeper:' + str(row['transaction_id']), start_sequence + len(result) + 1,
                                 kind, legs, iso_time(row.get('status_updated') or row.get('created')),
                                 source='sleeper', source_id=str(row['transaction_id']),
                                 metadata={'created': iso_time(row.get('created')), 'leg': row.get('leg')}))
    return result
