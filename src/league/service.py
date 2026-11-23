"""Validated projection/ownership boundary shared by CLI and offline UI."""
from __future__ import annotations
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.league.identity import clean_id
from src.league.storage import load_league, instance_config, league_dir, enrich_birth_dates
from src.league.profiles import profile, relative_profiles
from src.league.partners import partners
from src.trade import RosterPlayer, expand_slots, validate_players


def forecast_paths(cfg):
    root = cfg.paths['outputs']
    return root / 'league_player_values.csv', root / 'league_forecast.json'


def config_digest(cfg):
    import yaml
    return hashlib.sha256(yaml.safe_dump({'league': dict(cfg.league.raw),
        'model': dict(cfg.model), 'assumptions': dict(cfg.assumptions)}, sort_keys=True).encode()).hexdigest()


def build_forecast(cfg, seasons=None):
    from src.pipeline import run_pipeline
    from src.ingestion.csv_source import discover_seasons
    target_season = cfg.league.raw.get('meta', {}).get('season')
    seasons = seasons or discover_seasons(cfg.paths['raw'], before=target_season)[-3:]
    if not seasons:
        seasons = sorted(cfg.model['projection']['season_weights'], reverse=True)
    players_path = cfg.paths['repo'] / 'data/external/players.csv'
    schedule_path = cfg.paths['repo'] / f"data/external/schedule_{cfg.league.raw['meta']['season']}.csv"
    # Explicitly require reviewed real inputs; never build a league forecast from demo data.
    players = pd.read_csv(players_path, dtype={'sleeper_id': str})
    players, birth_date_sources = enrich_birth_dates(cfg, players)
    state, catalogue = load_league(cfg)
    # Latest reconciled identity supplies current teams and all positions; the
    # original metadata CSV stays immutable and still supplies age/history fields.
    for index, row in players.iterrows():
        identity = catalogue.players.get('sleeper:' + clean_id(row.sleeper_id))
        if identity:
            players.at[index, 'team'] = identity.team or None
            players.at[index, 'positions'] = '|'.join(identity.positions)
            players.at[index, 'position'] = identity.positions[0] if identity.positions else None
    schedule = pd.read_csv(schedule_path)
    # Games already played this season (written by league-archive) update projections.
    current_logs = None
    if target_season and (cfg.paths['raw'] / target_season / 'current.json').exists():
        from src.pipeline import load_seasons
        current_logs = load_seasons(cfg, [target_season])
    result = run_pipeline(cfg, seasons, players=players, schedule=schedule, current_logs=current_logs)
    if result.is_synthetic or result.board.empty:
        raise ValueError('League forecast requires nonempty real-data projections')
    values, meta = forecast_paths(cfg)
    values.parent.mkdir(parents=True, exist_ok=True)
    result.board.to_csv(values, index=False)
    from src.league.lock_advisor import schedule_payload
    from src.schedule.context import fit_game_context
    advisor = {'players': {}, 'schedule': schedule_payload(schedule),
               'game_context': fit_game_context(result.scored_logs), 'negative_scores_preserved': True}
    for row in result.board.to_dict('records'):
        sid = clean_id(row.get('sleeper_id'))
        distribution = result.lockin_distributions.get(str(row.get('player_id')))
        if sid and distribution:
            advisor['players']['sleeper:' + sid] = {**distribution, 'team': clean_id(row.get('team')),
                'play_probability': float(row['availability_probability'])}
    advisor_path = values.parent / 'league_lock_advisor.json'
    advisor_path.write_text(json.dumps(advisor, allow_nan=False), encoding='utf-8')
    sources = [players_path, schedule_path, *birth_date_sources]
    pointer = json.loads((league_dir(cfg) / 'current.json').read_text(encoding='utf-8'))
    sources.append(league_dir(cfg) / pointer['observation'])
    for season in list(seasons) + ([target_season] if current_logs is not None else []):
        from src.ingestion.csv_source import game_log_files
        sources += game_log_files(cfg.paths['raw'] / season)
        pointer_path = cfg.paths['raw'] / season / 'current.json'
        if pointer_path.exists():
            sources.append(pointer_path)
    provenance = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                  for p in sources if p.is_file()]
    metadata = {'generated_at': datetime.now(timezone.utc).isoformat(), 'seasons': seasons,
                'schema_version': 2, 'warnings': result.warnings, 'sources': provenance,
                'performance_history_through': str(pd.to_datetime(result.scored_logs.game_date).max().date()),
                'in_season_through': (str(pd.to_datetime(current_logs.game_date).max().date())
                                      if current_logs is not None and not current_logs.empty else None),
                'in_season_games': int(len(current_logs)) if current_logs is not None else 0,
                'game_context': advisor['game_context'],
                'ownership_observed_at': state.observed_at,
                'config_sha256': config_digest(cfg), 'values_sha256': hashlib.sha256(values.read_bytes()).hexdigest(),
                'advisor_sha256': hashlib.sha256(advisor_path.read_bytes()).hexdigest()}
    meta.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return values, metadata


def load_values(cfg, catalogue, horizon='regular'):
    values_path, meta_path = forecast_paths(cfg)
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    if meta['config_sha256'] != config_digest(cfg):
        raise ValueError('Rules/model assumptions changed: run league-build again')
    if meta['values_sha256'] != hashlib.sha256(values_path.read_bytes()).hexdigest():
        raise ValueError('Forecast checksum mismatch: run league-build again')
    board = pd.read_csv(values_path, dtype={'sleeper_id': str, 'player_id': str})
    prefix = 'playoff_' if horizon == 'playoffs' else ''
    required = {'sleeper_id', prefix+'conditional_lockin_value', prefix+'weekly_availability'}
    if not required <= set(board):
        raise ValueError(f'Forecast does not provide {horizon} weekly values; run league-build')
    if 'is_synthetic' in board and board.is_synthetic.astype(str).str.lower().eq('true').any():
        raise ValueError('Synthetic rows cannot enter league analytics')
    players, records, unmatched = {}, {}, []
    for row in board.to_dict('records'):
        sid = clean_id(row.get('sleeper_id'))
        pid = 'sleeper:' + sid
        if not sid or pid not in catalogue.players:
            unmatched.append(row['player_name'])
            continue
        if pid in players:
            raise ValueError(f'Multiple projections crosswalk to {pid}')
        identity = catalogue.players[pid]
        if horizon == 'playoffs' and str(row.get('playoff_used_real_schedule')).lower() != 'true':
            unmatched.append(row['player_name'] + ' (no playoff schedule)')
            continue
        if not identity.team or identity.status != 'ACT':
            unmatched.append(row['player_name'] + ' (not on an active NBA roster)')
            continue
        if clean_id(row.get('team')) != identity.team:
            unmatched.append(row['player_name'] + ' (team changed; rebuild forecast)')
            continue
        if not identity.positions:
            continue
        value = float(row[prefix+'conditional_lockin_value'])
        availability = float(row[prefix+'weekly_availability'])
        player = RosterPlayer(pid, identity.name, identity.positions[0], value, availability,
                              positions=identity.positions, injury_status=identity.injury_status or '')
        validate_players([player])
        players[pid] = player
        records[pid] = {'notes': clean_id(row.get('assumption_notes')),
                        'historical_games': int(row['historical_games']),
                        'used_schedule': str(row.get(prefix+'used_real_schedule')).lower() == 'true'}
    return players, records, {**meta, 'unmatched_projection_rows': len(unmatched),
                             'horizon': horizon, 'playoff_weeks': int(board.playoff_weeks.max())}


def context(cfg, horizon='regular'):
    state, catalogue = load_league(cfg)
    values, records, meta = load_values(cfg, catalogue, horizon)
    rosters, issues = {}, {}
    for tid in state.teams:
        ids = state.roster(tid, active_only=True)
        errors = []
        # Name the reason when the latest Sleeper identity alone excludes a
        # forecast row, so a rebuild is not mistaken for the remedy.
        missing = [catalogue.players[pid].name + (
            f" (Sleeper status {catalogue.players[pid].status or 'blank'}, no active NBA team)"
            if not catalogue.players[pid].team or catalogue.players[pid].status != 'ACT' else '')
            for pid in ids if pid not in values]
        if missing:
            errors.append('Missing projections: ' + ', '.join(missing))
        if len(ids) > cfg.league.roster_size:
            errors.append('Resolve IR/over-limit active roster before analytics')
        if errors:
            issues[tid] = errors
        else:
            rosters[tid] = [values[pid] for pid in ids]
    # No unowned retired/inactive/no-team catalogue entries in the replacement market.
    pool = [p for pid, p in values.items() if pid not in state.ownership and
            catalogue.players[pid].team and catalogue.players[pid].status == 'ACT']
    return state, catalogue, values, records, meta, rosters, issues, pool


def require_team(state, rosters, issues, name):
    tid = state.team(name).team_id
    if tid in issues:
        raise ValueError(f'{name}: ' + '; '.join(issues[tid]))
    return tid, rosters[tid]


def snapshot(cfg):
    state, catalogue, values, records, meta, rosters, issues, pool = context(cfg)
    from src.league.matchups import load_matchups
    advisor = {'players': {}, 'schedule': {'games': []}}
    if meta.get('advisor_sha256'):
        path = cfg.paths['outputs'] / 'league_lock_advisor.json'
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta['advisor_sha256']:
            raise ValueError('Lock advisor checksum mismatch: run league-build')
        advisor = json.loads(path.read_text(encoding='utf-8'))
        advisor['players'] = {pid: p for pid, p in advisor['players'].items() if pid in values}
    profiles = {tid: profile(roster, cfg.league) for tid, roster in rosters.items()}
    if len(profiles) == cfg.league.teams:
        relative_profiles(profiles)
    partner_results = {tid: partners(tid, rosters, cfg.league) for tid in rosters}
    try:
        _, _, playoff_values, _, _, playoff_rosters, playoff_issues, playoff_pool = context(cfg, 'playoffs')
    except ValueError:
        playoff_values, playoff_rosters, playoff_issues, playoff_pool = {}, {}, {}, []
    from src.league.needs import league_needs
    needs = league_needs(rosters, cfg.league, cfg.league.teams)
    playoff_needs = league_needs(playoff_rosters, cfg.league, cfg.league.teams)
    def payload(player):
        return {'id': player.player_id, 'name': player.name, 'position': player.position,
                'positions': list(player.positions), 'value': player.weekly_value,
                'availability': player.availability, 'isPick': False, 'slots': [],
                'injury_status': player.injury_status, 'health_risk': player.health_risk}
    teams = []
    for tid, t in state.teams.items():
        teams.append({**asdict(t), 'roster': state.roster(tid, True), 'reserve': sorted(state.reserve.get(tid, [])),
                      'issues': issues.get(tid, []), 'profile': profiles.get(tid),
                      'partners': partner_results.get(tid, []),
                      'positional_needs': needs.get(tid), 'playoff_positional_needs': playoff_needs.get(tid),
                      'playoff_profile': profile(playoff_rosters[tid], cfg.league) if tid in playoff_rosters else None})
    return {'schema_version': 1, 'league_id': state.league_id, 'verified': state.verified,
            'ownership_observed_at': state.observed_at,
            'my_team': instance_config(cfg)['my_team'], 'teams': teams,
            'players': {pid: {**payload(p), **records[pid]} for pid, p in values.items()},
            'identities': {pid: asdict(p) for pid, p in catalogue.players.items() if pid in state.ownership},
            'playoff_players': {pid: payload(p) for pid, p in playoff_values.items()},
            'free_agents': [p.player_id for p in pool], 'playoff_free_agents': [p.player_id for p in playoff_pool],
            # Listed so they can be found, never valued: no forecast row exists (often new rookies).
            'unprojected_free_agents': sorted(({'id': pid, 'name': p.name, 'team': p.team, 'positions': list(p.positions),
                                                'injury_status': p.injury_status}
                                               for pid, p in catalogue.players.items()
                                               if pid not in state.ownership and pid not in values
                                               and p.team and p.status == 'ACT'), key=lambda r: r['name']),
            'notes': state.notes + ['IR players excluded from active-roster scenarios; return timing not forecast',
                'Injury flags are snapshot data, not medical clearance. Flagged assets are excluded from automatic trades and claims; manual results remain provisional.',
                'Roster expectation models joint independent weekly absences, including backups; 512 fixed scenarios in leagues with more than eight active roster spots. Small gains are approximate.',
                'Full-league percentiles withheld unless every team has complete projection coverage'],
            'forecast': meta, 'lock_advisor': advisor, 'matchups': load_matchups(cfg, state),
            'cfg': {'slots': expand_slots(cfg.league), 'slotNames': list(cfg.league.starting_slots),
                    'elig': dict(cfg.league.position_eligibility), 'rosterLimit': cfg.league.roster_size,
                    'weeksRemaining': int(cfg.league.calendar['regular_season_weeks']), 'stress': .1}}
