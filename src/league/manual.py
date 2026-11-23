"""Import user-provided evidence without fabricating dates, IDs, or IR moves."""
from dataclasses import asdict
import json
from pathlib import Path
import pandas as pd

from src.draft.simulator import snake_pick_order
from src.league.identity import PlayerCatalog
from src.league.models import Team, Transfer, LeagueEvent
from src.league.events import append_events
from src.league.state import replay
from src.league.storage import instance_config, league_dir, archive_payload, publish_pointer, events_digest


def import_manual(cfg):
    instance = instance_config(cfg)
    manual = instance['manual']
    root = league_dir(cfg)
    if (root / 'current.json').exists():
        raise ValueError('A current state already exists; append explicit events or sync Sleeper')
    catalogue = PlayerCatalog.from_frame(pd.read_csv(cfg.paths['external'] / 'players.csv', dtype={'sleeper_id': str}))
    teams = [Team(f'manual:{i}', name, i) for i, name in enumerate(instance['draft_order'], 1)]
    draft = (cfg.paths['repo'] / manual['draft_file']).read_text(encoding='utf-8-sig')
    names = [x.strip() for x in draft.splitlines() if x.strip() and not x.lstrip().startswith('#')]
    picks = {i: name for i, name in enumerate(names, 1)}
    for number, entry in manual['missing_picks'].items():
        if number in picks:
            raise ValueError(f'Manual completion overlaps an existing draft pick: {number}')
        picks[int(number)] = entry['player']
    count = cfg.league.teams * cfg.league.draft['rounds']
    if set(picks) != set(range(1, count + 1)):
        raise ValueError('Manual draft has missing/extra picks')
    corrections = manual.get('name_corrections', {})
    order = snake_pick_order(cfg.league.teams, cfg.league.draft['rounds'])
    events = []
    for number in range(1, count+1):
        name = picks[number]
        pid = catalogue.resolve(corrections.get(name, name)).player_id
        events.append(LeagueEvent(f'manual:draft:{number}', number, 'draft',
                                 (Transfer(pid, None, teams[order[number-1]].team_id),),
                                 metadata={'pick_no': number, 'raw_name': name,
                                           'note': manual['missing_picks'].get(number, {}).get('note', '')}))
    team_ids = {t.manager: t.team_id for t in teams}
    for i, row in enumerate(manual['transactions'], 1):
        legs = []
        tid = team_ids[row['team']]
        if row.get('drop'):
            legs.append(Transfer(catalogue.resolve(row['drop']).player_id, tid, None))
        if row.get('add'):
            legs.append(Transfer(catalogue.resolve(row['add']).player_id, None, tid))
        kind = 'add_drop' if len(legs) == 2 else 'add' if row.get('add') else 'drop'
        events.append(LeagueEvent(f'manual:transaction:{i}', count+i, kind, tuple(legs),
                                 metadata={'order_only': True}))
    state = replay(instance['league_id'], teams, events, catalogue, cfg.league)
    for manager, field in [('Emil', 'current_emil'), ('Mymmeli', 'current_mymmeli')]:
        expected = {catalogue.resolve(n).player_id for n in manual[field]}
        if expected != set(state.roster(team_ids[manager])):
            raise ValueError(f'Manual reconstruction disagrees with reported {manager} roster')
    state.notes += ['Manual history; transaction dates and IR movements unknown',
                    'Pick 140 Darius Acuff inferred from reported Mymmeli roster; awaiting Sleeper confirmation']
    append_events(root / 'manual_events.jsonl', events)
    saved = {'league_id': instance['league_id'], 'teams': [asdict(t) for t in teams],
             'players': [asdict(p) for p in catalogue.players.values()], 'reserve': {},
             'source': 'manual', 'event_count': len(events), 'notes': state.notes,
             'events_sha256': events_digest(events)}
    observation = archive_payload(root, 'manual-state', saved, 'user:reported draft and transaction sequence')
    publish_pointer(root, observation)
    return state, catalogue
