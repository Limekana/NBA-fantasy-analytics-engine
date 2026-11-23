"""Separately timestamped, read-only head-to-head observations from Sleeper."""
import hashlib
import json

from src.league.storage import league_dir, load_league, archive_payload


def validate_matchups(payload, team_ids):
    if not isinstance(payload, list) or not payload:
        raise ValueError('No matchup data returned for this week')
    ids = [str(r['roster_id']) for r in payload]
    if len(ids) != len(set(ids)) or set(ids) != set(team_ids):
        raise ValueError('Matchup rosters do not match this league')
    groups = {}
    for r in payload:
        if r.get('matchup_id') is not None:
            groups.setdefault(str(r['matchup_id']), []).append(str(r['roster_id']))
    if any(len(v) != 2 for v in groups.values()):
        raise ValueError('Expected head-to-head matchup pairs (or explicit byes)')
    return groups


def sync_matchups(cfg, week, fetch=None):
    from src.ingestion.sleeper_source import _get_json
    if not isinstance(week, int) or not 1 <= week <= 52:
        raise ValueError('Matchup week must be 1..52')
    state, _ = load_league(cfg)
    url = f'https://api.sleeper.app/v1/league/{state.league_id}/matchups/{week}'
    payload = (fetch or _get_json)(url)
    validate_matchups(payload, state.teams)
    root = league_dir(cfg)
    path = archive_payload(root, f'matchups-{week}', payload, url)
    manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    pointer = {'week': week, 'path': str(path.relative_to(root)), **manifest}
    (root / f'matchups-week-{week}.json').write_text(json.dumps(pointer), encoding='utf-8')
    return {'week': week, 'retrieved_at': manifest['retrieved_at'], 'rows': len(payload)}


def load_matchups(cfg, state):
    root, weeks = league_dir(cfg), {}
    for file in root.glob('matchups-week-*.json'):
        pointer = json.loads(file.read_text(encoding='utf-8'))
        path = (root / pointer['path']).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != pointer['sha256']:
            raise ValueError('Matchup observation checksum/path mismatch')
        rows = json.loads(path.read_text(encoding='utf-8'))
        pairs = validate_matchups(rows, state.teams)
        weeks[str(pointer['week'])] = {'retrieved_at': pointer['retrieved_at'],
            'source_url': pointer['source_url'], 'sha256': pointer['sha256'], 'pairs': pairs, 'rows': rows}
    return weeks
