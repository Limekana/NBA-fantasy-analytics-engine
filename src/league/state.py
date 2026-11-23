"""Pure replay and validation, without filesystem or HTTP dependencies."""
from __future__ import annotations
from copy import deepcopy

from src.league.models import LeagueState


def validate_state(state, catalogue, cfg, check_capacity=True):
    for pid, owner in state.ownership.items():
        if pid not in catalogue.players:
            raise ValueError(f'Unknown player ID: {pid}')
        if owner not in state.teams:
            raise ValueError(f'Unknown owner: {owner}')
    for team_id, reserved in state.reserve.items():
        if team_id not in state.teams or not reserved <= set(state.roster(team_id)):
            raise ValueError('IR player must be owned by that team')
        if len(reserved) > int(cfg.roster.get('IR', 0)):
            raise ValueError('IR capacity exceeded')
    if check_capacity and state.reserve_known:
        for tid in state.teams:
            if len(state.roster(tid, active_only=True)) > cfg.roster_size:
                raise ValueError(f'Active roster capacity exceeded: {state.teams[tid].manager}')


def apply_event(state, event, catalogue, cfg, check_capacity=True):
    if not event.event_id or not isinstance(event.sequence, int) or event.sequence < 1:
        raise ValueError('Event ID and positive integer sequence required')
    if event.sequence <= state.last_sequence:
        raise ValueError('Events must be applied in increasing sequence order')
    if event.kind not in {'draft', 'add', 'drop', 'add_drop', 'waiver', 'trade', 'reserve_snapshot'}:
        raise ValueError(f'Unknown event kind: {event.kind}')
    if event.kind != 'reserve_snapshot' and not event.transfers:
        raise ValueError('Player event requires transfers')
    if event.kind in {'draft', 'add'} and any(t.from_team is not None or t.to_team is None for t in event.transfers):
        raise ValueError('Add/draft must transfer a free agent to a team')
    if event.kind == 'drop' and any(t.from_team is None or t.to_team is not None for t in event.transfers):
        raise ValueError('Drop must transfer an owned player to free agency')
    if event.kind == 'trade' and any(t.from_team is None or t.to_team is None for t in event.transfers):
        raise ValueError('Trade transfers must name both owners')
    # Validate all legs against the same before-state, then apply atomically.
    seen = set()
    for t in event.transfers:
        if t.player_id in seen:
            raise ValueError('Player occurs twice in one transaction')
        seen.add(t.player_id)
        if t.player_id not in catalogue.players:
            raise ValueError(f'Unknown player ID: {t.player_id}')
        actual = state.ownership.get(t.player_id)
        if actual != t.from_team:
            raise ValueError(f'Ownership mismatch / duplicate ownership for {t.player_id}: '
                             f'expected {t.from_team}, actual {actual}')
        if t.from_team == t.to_team:
            raise ValueError('Transfer must change ownership')
        if t.to_team is not None and t.to_team not in state.teams:
            raise ValueError(f'Unknown destination team: {t.to_team}')
    out = deepcopy(state)
    for t in event.transfers:
        out.ownership.pop(t.player_id, None)
        for reserved in out.reserve.values():
            reserved.discard(t.player_id)
        if t.to_team is not None:
            out.ownership[t.player_id] = t.to_team
    if event.kind == 'reserve_snapshot':
        if event.transfers:
            raise ValueError('Reserve snapshot cannot transfer ownership')
        out.reserve = {tid: set(ids) for tid, ids in event.metadata['reserve'].items()}
        if set(out.reserve) != set(out.teams):
            raise ValueError('Reserve snapshot must cover all teams')
        out.reserve_known = True
    out.last_sequence = event.sequence
    out.verified = False
    out.observed_at = None
    validate_state(out, catalogue, cfg, check_capacity)
    return out


def replay(league_id, teams, events, catalogue, cfg, check_capacity=True):
    teams = list(teams)
    if len({t.team_id for t in teams}) != len(teams) or len(teams) != cfg.teams:
        raise ValueError('Team IDs must be unique and match configured team count')
    state = LeagueState(league_id, {t.team_id: t for t in teams})
    event_ids, sequences = set(), set()
    for event in sorted(events, key=lambda e: e.sequence):
        if event.event_id in event_ids or event.sequence in sequences:
            raise ValueError('Duplicate event ID or sequence')
        event_ids.add(event.event_id)
        sequences.add(event.sequence)
        state = apply_event(state, event, catalogue, cfg, check_capacity)
    if not state.reserve_known:
        state.notes.append('IR placements unknown; ownership reconstructed but active roster legality unverified')
    return state


def reconcile(state, rosters, catalogue, cfg):
    expected, reserve = {}, {}
    seen_teams = set()
    for row in rosters:
        tid = str(row['roster_id'])
        if tid in seen_teams or tid not in state.teams:
            raise ValueError(f'Duplicate/unknown Sleeper roster: {tid}')
        seen_teams.add(tid)
        reserve[tid] = {'sleeper:' + str(p) for p in row.get('reserve') or []}
        for raw in row.get('players') or []:
            pid = 'sleeper:' + str(raw)
            if pid in expected:
                raise ValueError(f'Sleeper snapshot has duplicate ownership: {pid}')
            expected[pid] = tid
    if seen_teams != set(state.teams):
        raise ValueError('Sleeper roster snapshot is incomplete')
    diffs = [f'{pid}: replay={state.ownership.get(pid)}, Sleeper={expected.get(pid)}'
             for pid in sorted(set(expected) | set(state.ownership))
             if expected.get(pid) != state.ownership.get(pid)]
    if diffs:
        raise ValueError('History does not reconcile; no invented events:\n' + '\n'.join(diffs))
    out = deepcopy(state)
    out.reserve, out.reserve_known = reserve, True
    validate_state(out, catalogue, cfg)
    out.verified = True
    out.notes = []
    return out
