"""Durable source snapshots and identity-preserving league loading."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import uuid
import os
from pathlib import Path

import pandas as pd
import yaml

from src.league.events import read_events, append_events
from src.league.identity import PlayerCatalog
from src.league.models import Team, LeagueEvent, Transfer
from src.league.state import replay, reconcile
from src.league.sleeper_import import teams_from_sleeper, draft_events, transaction_events


def instance_config(cfg):
    return yaml.safe_load((cfg.paths['repo'] / 'config/league_instance.yaml').read_text(encoding='utf-8'))


def league_dir(cfg):
    lid = str(instance_config(cfg)['league_id'])
    if not lid.isdigit():
        raise ValueError('League ID must contain only digits')
    return cfg.paths['repo'] / 'data/league' / lid


def events_digest(events):
    return hashlib.sha256(json.dumps([e.to_dict() for e in events], sort_keys=True).encode()).hexdigest()


def publish_pointer(root, observation):
    path = Path(root) / 'current.json'
    temp = path.with_name(f'current-{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps({'observation': str(observation.relative_to(root))}), encoding='utf-8')
    os.replace(temp, path)


def archive_payload(root, label, payload, source_url, retrieved_at=None):
    directory = Path(root) / 'raw'
    directory.mkdir(parents=True, exist_ok=True)
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    digest = hashlib.sha256(data).hexdigest()
    path = directory / f'{label}-{uuid.uuid4().hex}.json'
    path.write_bytes(data)
    path.with_suffix('.manifest.json').write_text(json.dumps({
        'source_url': source_url, 'retrieved_at': retrieved_at, 'sha256': digest,
        'bytes': len(data), 'dataset': label,
    }, indent=2), encoding='utf-8')
    return path


def load_cached_player_catalog(root):
    """Load the last validated full Sleeper catalogue without making a request."""
    root = Path(root)
    pointer_path = root / 'players-catalog-current.json'
    if not pointer_path.exists():
        raise FileNotFoundError(pointer_path)
    pointer = json.loads(pointer_path.read_text(encoding='utf-8'))
    path = (root / pointer['path']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Invalid Sleeper player-catalogue path')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pointer.get('sha256'):
        raise ValueError('Sleeper player-catalogue checksum mismatch')
    return json.loads(raw), pointer, path


def enrich_birth_dates(cfg, players):
    """Attach stable DOBs to reviewed metadata using the cached Sleeper source."""
    players = players.copy()
    if 'sleeper_id' not in players.columns:
        return players, []
    root = league_dir(cfg)
    try:
        payload, _pointer, catalogue_path = load_cached_player_catalog(root)
    except FileNotFoundError:
        return players, []
    birth_dates = {str(sid): record.get('birth_date') for sid, record in payload.items()
                   if record.get('birth_date')}
    latest = players['sleeper_id'].astype(str).map(birth_dates)
    if 'birth_date' in players:
        players['birth_date'] = latest.combine_first(players['birth_date'])
    else:
        players['birth_date'] = latest
    return players, [root / 'players-catalog-current.json', catalogue_path]


def load_archived_player_metadata(cfg, season):
    """Load checksummed player metadata captured during a specific NBA season."""
    root = league_dir(cfg) / 'seasons' / str(season)
    pointer_path = root / 'current.json'
    if not pointer_path.exists():
        raise FileNotFoundError(pointer_path)
    pointer = json.loads(pointer_path.read_text(encoding='utf-8'))
    snapshot = (root / pointer['path']).resolve()
    if not snapshot.is_relative_to(root.resolve()):
        raise ValueError('Invalid season archive pointer')
    manifest_path = snapshot / 'archive_manifest.json'
    manifest_raw = manifest_path.read_bytes()
    if hashlib.sha256(manifest_raw).hexdigest() != pointer.get('manifest_sha256'):
        raise ValueError('Season archive manifest checksum mismatch')
    manifest = json.loads(manifest_raw)
    dataset = manifest.get('datasets', {}).get('sleeper_nba_player_crosswalk')
    if not dataset:
        raise FileNotFoundError(f'No archived player crosswalk for {season}')
    path = (snapshot / dataset['path']).resolve()
    if not path.is_relative_to(snapshot) or hashlib.sha256(path.read_bytes()).hexdigest() != dataset['sha256']:
        raise ValueError('Archived player metadata checksum/path mismatch')
    return pd.read_csv(path, dtype={'sleeper_id': str, 'nba_player_id': str}), [manifest_path, path]


def cached_player_catalog(root, fetch=None, max_age=timedelta(hours=24)):
    """Read the large Sleeper NBA catalogue cache or refresh it once per day."""
    from src.ingestion.sleeper_source import _get_json
    root = Path(root)
    pointer_path = root / 'players-catalog-current.json'
    url = 'https://api.sleeper.app/v1/players/nba'
    if pointer_path.exists():
        payload, pointer, _path = load_cached_player_catalog(root)
        retrieved = datetime.fromisoformat(pointer['retrieved_at'].replace('Z', '+00:00'))
        age = datetime.now(timezone.utc) - retrieved
        if timedelta(0) <= age < max_age:
            return payload
    payload = (fetch or _get_json)(url)
    path = archive_payload(root, 'players', payload, url)
    manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    pointer = {'path': str(path.relative_to(root)), 'sha256': manifest['sha256'],
               'retrieved_at': manifest['retrieved_at'], 'source_url': url}
    temp = pointer_path.with_name(f'players-catalog-current-{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps(pointer, indent=2), encoding='utf-8')
    os.replace(temp, pointer_path)
    return payload


def fetch_bundle(cfg, through_round=None, source=None, *, archive_composite=True):
    from src.ingestion.sleeper_source import SleeperSource, _get_json
    source = source or SleeperSource()
    root, lid = league_dir(cfg), instance_config(cfg)['league_id']
    base = f'https://api.sleeper.app/v1/league/{lid}'
    bundle = {'league_id': lid, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
              'transaction_rounds': []}
    calls = [('league', lambda: source.fetch_league_settings(lid), base),
             ('users', lambda: source.fetch_users(lid), base + '/users'),
             ('drafts', lambda: source.fetch_drafts(lid), base + '/drafts')]
    for label, call, url in calls:
        bundle[label] = call()
        archive_payload(root, label, bundle[label], url)
    current_round = int(bundle['league'].get('settings', {}).get('leg', 0))
    through_round = current_round if through_round is None else through_round
    if not 0 <= through_round <= 1000:
        raise ValueError('Invalid transaction round')
    bundle['transaction_rounds'] = list(range(0, through_round + 1))
    if through_round < current_round:
        raise ValueError('Transaction coverage must reach the current league leg')
    drafts = [d for d in bundle['drafts'] if d.get('status') == 'complete' and
              str(d.get('draft_id')) == str(bundle['league'].get('draft_id'))]
    if len(drafts) != 1:
        raise ValueError('Select/implement the relevant draft explicitly: expected exactly one completed draft')
    did = drafts[0]['draft_id']
    for label, call, url in [
        ('draft', lambda: source.fetch_draft(did), f'https://api.sleeper.app/v1/draft/{did}'),
        ('picks', lambda: source.fetch_picks(did), f'https://api.sleeper.app/v1/draft/{did}/picks'),
    ]:
        bundle[label] = call()
        archive_payload(root, label, bundle[label], url)
    bundle['players'] = cached_player_catalog(root)
    bundle['transactions'] = []
    for week in bundle['transaction_rounds']:
        payload = source.fetch_transactions(lid, week)
        archive_payload(root, f'transactions-{week}', payload, base + f'/transactions/{week}')
        bundle['transactions'].extend(payload)
    # Last fetch: reduce snapshot races; replay mismatch still fails closed.
    bundle['rosters'] = source.fetch_rosters(lid)
    archive_payload(root, 'rosters', bundle['rosters'], base + '/rosters')
    if archive_composite:
        archive_payload(root, 'bundle', bundle, 'composite:documented Sleeper GET endpoints')
    return bundle


def catalog_from_payload(payload):
    rows = []
    for sid, p in payload.items():
        name = p.get('full_name') or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if name:
            rows.append({'sleeper_id': str(sid), 'player_name': name, 'team': p.get('team'),
                         'positions': '|'.join(p.get('fantasy_positions') or [p.get('position') or '']),
                         'status': p.get('status'), 'injury_status': p.get('injury_status')})
    return PlayerCatalog.from_frame(pd.DataFrame(rows))


def import_bundle(cfg, bundle):
    instance, root = instance_config(cfg), league_dir(cfg)
    if (root / 'refresh.lock').exists():
        raise ValueError('Dashboard refresh in progress; retry when it finishes')
    lid = instance['league_id']
    if str(bundle.get('league_id')) != lid or str(bundle['league'].get('league_id')) != lid:
        raise ValueError('Snapshot is for a different league')
    archive_payload(root, 'import-bundle', bundle, 'supplied:Sleeper endpoint bundle', bundle.get('retrieved_at'))
    from src.ingestion.sleeper_source import diff_scoring
    from collections import Counter
    differences = diff_scoring(bundle['league'], {**cfg.league.raw['scoring'], **cfg.league.raw['bonuses']})
    live_positions = Counter(bundle['league'].get('roster_positions', []))
    if live_positions and live_positions != Counter({k: v for k, v in cfg.league.roster.items() if k != 'IR' and v}):
        differences.append('Roster positions differ from config')
    settings = bundle['league'].get('settings', {})
    if 'game_mode' in settings and cfg.league.is_lock_in and settings['game_mode'] != 1:
        differences.append('Expected Sleeper Lock-In game mode')
    if bundle['league'].get('season') and str(bundle['league']['season']) != str(cfg.league.raw['meta']['season']).split('-')[0]:
        differences.append('League season differs from forecast season')
    if 'reserve_slots' in settings and settings['reserve_slots'] != cfg.league.roster.get('IR', 0):
        differences.append('IR capacity differs from config')
    if 'playoff_week_start' in settings and settings['playoff_week_start'] != cfg.league.raw['playoffs']['start_week']:
        differences.append('Playoff start differs from config')
    if differences:
        raise ValueError('Live rules differ: ' + '; '.join(differences))
    if bundle['league'].get('sport') != 'nba':
        raise ValueError('Expected an NBA league payload')
    if int(bundle['league']['total_rosters']) != cfg.league.teams:
        raise ValueError('Live team count differs from config')
    if not bundle.get('transaction_rounds') or 0 not in bundle['transaction_rounds']:
        raise ValueError('Transaction coverage must include preseason round 0')
    rounds = bundle['transaction_rounds']
    if sorted(set(rounds)) != list(range(max(rounds)+1)):
        raise ValueError('Transaction round coverage has gaps')
    if max(rounds) < int(settings.get('leg', 0)):
        raise ValueError('Transaction coverage stops before current league leg')
    catalog = catalog_from_payload(bundle['players'])
    teams = teams_from_sleeper(bundle['users'], bundle['rosters'], bundle['draft'],
                               instance['manager_aliases'], instance['draft_order'])
    events = draft_events(bundle['picks'], bundle['draft'], teams, catalog, cfg.league.draft['rounds'])
    events += transaction_events(bundle['transactions'], len(events))
    current = replay(lid, teams, events, catalog, cfg.league, check_capacity=False)
    current = reconcile(current, bundle['rosters'], catalog, cfg.league)
    current.observed_at = bundle.get('retrieved_at')
    # Canonical API event history is separate from provisional manual evidence.
    path = root / 'sleeper_events.jsonl'
    old = read_events(path)
    old_transfer_events = [e for e in old if e.kind != 'reserve_snapshot']
    if [e.to_dict() for e in old_transfer_events] != [e.to_dict() for e in events[:len(old_transfer_events)]]:
        raise ValueError('Earlier API history changed; review archived sources before rewriting any history')
    # Keep canonical transaction order stable; reserve state is a separate source observation.
    append_events(path, events)
    saved = {'league_id': lid, 'teams': [asdict(t) for t in teams],
             'players': [asdict(p) for p in catalog.players.values()],
             'reserve': {k: sorted(v) for k, v in current.reserve.items()},
             'verified_at': bundle.get('retrieved_at'), 'event_count': len(events),
             'source': 'sleeper', 'notes': current.notes, 'events_sha256': events_digest(events)}
    # Publish a new observation only after replay fully agrees with the API snapshot.
    observation = archive_payload(root, 'verified-state', saved, 'derived:replay reconciled against Sleeper rosters')
    publish_pointer(root, observation)
    return current, catalog


def load_league(cfg):
    from src.league.identity import PlayerIdentity
    root = league_dir(cfg)
    pointer = json.loads((root / 'current.json').read_text(encoding='utf-8'))
    path = (root / pointer['observation']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Invalid observation path')
    saved = json.loads(path.read_text(encoding='utf-8'))
    manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Observation checksum mismatch')
    catalogue = PlayerCatalog(PlayerIdentity(**{**p, 'positions': tuple(p['positions'])}) for p in saved['players'])
    events = read_events(root / ('sleeper_events.jsonl' if saved['source'] == 'sleeper' else 'manual_events.jsonl'))
    state = replay(saved['league_id'], [Team(**t) for t in saved['teams']], events, catalogue, cfg.league,
                   check_capacity=False)
    state.reserve = {k: set(v) for k, v in saved.get('reserve', {}).items()}
    state.reserve_known = saved['source'] == 'sleeper'
    prefix = events[:saved['event_count']]
    if saved.get('events_sha256') and events_digest(prefix) != saved['events_sha256']:
        raise ValueError('Previously observed event history changed')
    state.verified = state.reserve_known and saved['event_count'] == len(events)
    if not state.verified:
        state.reserve_known = False
    state.notes = saved.get('notes', []) + ([] if state.verified else ['Unverified ownership or IR state'])
    state.observed_at = saved.get('verified_at') if state.verified else None
    from src.league.state import validate_state
    validate_state(state, catalogue, cfg.league)
    return state, catalogue


def append_manual(cfg, payload):
    """Append a user-recorded event to a provisional history, never API history."""
    root = league_dir(cfg)
    pointer = json.loads((root / 'current.json').read_text(encoding='utf-8'))
    saved = json.loads((root / pointer['observation']).read_text(encoding='utf-8'))
    if saved['source'] != 'manual':
        raise ValueError('API-backed league: use league-sync to append completed transactions; manual log is separate')
    state, catalogue = load_league(cfg)
    event = LeagueEvent.from_dict(payload)
    if event.source != 'manual':
        raise ValueError('Manual append cannot assert API provenance')
    return append_events(root / 'manual_events.jsonl', [event], validator=lambda events:
        replay(state.league_id, state.teams.values(), events, catalogue, cfg.league))
