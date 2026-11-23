"""Post-draft commands, separated from the legacy draft CLI."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
from src.config import load_config


def register(sub):
    for name in ('league-sync', 'league-import', 'league-init-manual', 'league-build',
                 'league-rosters', 'team-profile', 'free-agents', 'trade-partners',
                 'league-trade', 'league-dashboard', 'league-append', 'league-matchups',
                 'league-archive', 'league-serve', 'league-forecast-save', 'league-forecast-report',
                 'league-weekly'):
        p = sub.add_parser(name, help='post-draft: ' + name.replace('league-', '').replace('-', ' '))
        p.set_defaults(func=run)
        if name == 'league-serve':
            p.add_argument('--port', type=int, default=8765)
        if name == 'league-forecast-save':
            p.add_argument('--week', type=int, help='default: next week that has not started')
        if name == 'league-matchups':
            p.add_argument('--week', type=int, required=True)
        if name == 'league-archive':
            p.add_argument('--through-week', type=int, help='default: current Sleeper NBA week')
        if name == 'league-sync':
            p.add_argument('--through-round', type=int, help='default: detect current league leg from Sleeper')
        if name in ('league-import', 'league-append'):
            p.add_argument('--file', type=Path, required=True)
        if name in ('team-profile', 'free-agents', 'trade-partners', 'league-trade'):
            p.add_argument('--team', default=None)
            p.add_argument('--horizon', choices=['regular', 'playoffs'], default='regular')
        if name == 'free-agents':
            p.add_argument('--top', type=int, default=20)
        if name == 'league-trade':
            p.add_argument('--opponent', required=True)
            p.add_argument('--give', default='', help='comma-separated names or Sleeper IDs')
            p.add_argument('--get', default='', help='comma-separated names or Sleeper IDs')
            p.add_argument('--stress', type=float, default=10, help='projection error percent')
            p.add_argument('--weeks-left', type=int)
            p.add_argument('--no-replacements', action='store_true')


def run(args):
    try:
        result = dispatch(args, load_config())
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, FileNotFoundError, KeyError, OSError) as exc:
        print(f'League command stopped: {exc}')
        return 2


def dispatch(args, cfg):
    from src.league.storage import load_league, fetch_bundle, import_bundle, instance_config
    from src.league.service import context, require_team
    command = args.command
    if command == 'league-serve':
        from src.league.refresh_server import serve
        return serve(cfg, args.port)
    if command == 'league-matchups':
        from src.league.matchups import sync_matchups
        return sync_matchups(cfg, args.week)
    if command == 'league-archive':
        from src.league.season_archive import archive_season
        return archive_season(cfg, args.through_week)
    if command == 'league-forecast-save':
        from src.league.forecast_log import save_week_forecast
        return save_week_forecast(cfg, args.week)
    if command == 'league-forecast-report':
        from src.league.forecast_log import forecast_report
        return forecast_report(cfg)
    if command == 'league-weekly':
        # One step before each week: new rosters and games, then a write-once forecast.
        from src.league.forecast_log import save_week_forecast
        from src.league.refresh_server import refresh_league
        from src.league.season_archive import archive_season
        from src.league.service import build_forecast, snapshot
        from src.reporting.league_dashboard import render_league_dashboard
        refresh = refresh_league(cfg)
        archive = archive_season(cfg, None)
        _path, meta = build_forecast(cfg)
        render_league_dashboard(snapshot(cfg), cfg.paths['outputs'] / 'league_dashboard.html')
        return {'roster_changes': refresh['changes'], 'games_through': archive['nba_history_through'],
                'forecast_built': meta['generated_at'], 'saved_forecast': save_week_forecast(cfg)}
    if command == 'league-build':
        from src.league.service import build_forecast
        path, meta = build_forecast(cfg)
        return {'forecast': str(path), **meta}
    if command in ('league-sync', 'league-import', 'league-init-manual'):
        if command == 'league-init-manual':
            from src.league.manual import import_manual
            state, catalogue = import_manual(cfg)
        else:
            bundle = fetch_bundle(cfg, args.through_round) if command == 'league-sync' else json.loads(args.file.read_text(encoding='utf-8-sig'))
            state, catalogue = import_bundle(cfg, bundle)
        return {'verified': state.verified, 'events': state.last_sequence,
                'teams': {t.manager: len(state.roster(t.team_id)) for t in state.teams.values()}, 'notes': state.notes}
    if command == 'league-append':
        from src.league.storage import append_manual
        return {'appended': append_manual(cfg, json.loads(args.file.read_text(encoding='utf-8-sig')))}
    if command == 'league-dashboard':
        from src.league.service import snapshot
        from src.reporting.league_dashboard import render_league_dashboard
        output = cfg.paths['outputs'] / 'league_dashboard.html'
        data = snapshot(cfg)
        render_league_dashboard(data, output)
        return {'dashboard': str(output), 'verified_ownership': data['verified'],
                'issues': {t['manager']: t['issues'] for t in data['teams'] if t['issues']}}
    if command == 'league-rosters':
        state, catalogue = load_league(cfg)
        return {'verified': state.verified, 'notes': state.notes,
                'teams': [{**asdict(t), 'players': [{'id': pid, 'name': catalogue.players[pid].name,
                    'reserve': pid in state.reserve.get(t.team_id, set())} for pid in state.roster(t.team_id)]}
                    for t in state.teams.values()]}
    state, catalogue, values, records, meta, rosters, issues, pool = context(cfg, args.horizon)
    team_name = args.team or instance_config(cfg)['my_team']
    tid, roster = require_team(state, rosters, issues, team_name)
    common = {'team': team_name, 'horizon': args.horizon, 'verified_ownership': state.verified,
              'forecast_generated_at': meta['generated_at'], 'notes': state.notes,
              'player_names': {pid: p.name for pid, p in catalogue.players.items() if pid in values},
              'excluded_teams': {state.teams[k].manager: v for k, v in issues.items()}}
    if command == 'team-profile':
        from src.league.needs import league_needs
        from src.league.profiles import profile, relative_profiles
        from src.league.replacements import replaceability, positional_market_gaps
        profiles = {k: profile(r, cfg.league) for k, r in rosters.items()}
        if len(profiles) == cfg.league.teams:
            relative_profiles(profiles)
        return {**common, **profiles[tid], 'positional_needs': league_needs(rosters, cfg.league, cfg.league.teams).get(tid),
                'replaceability': replaceability(roster, pool, cfg.league),
                'positional_market_gaps': positional_market_gaps(roster, pool, cfg.league)}
    if command == 'free-agents':
        from src.league.replacements import add_drop_candidates
        return {**common, 'pool_size': len(pool), 'moves': add_drop_candidates(roster, pool, cfg.league, args.top)}
    if command == 'trade-partners':
        from src.league.partners import partners
        return {**common, 'partners': [{**r, 'manager': state.teams[r['team_id']].manager}
                                     for r in partners(tid, rosters, cfg.league)]}
    if command == 'league-trade':
        from src.league.trades import evaluate_two_team
        other_tid, other = require_team(state, rosters, issues, args.opponent)
        if tid == other_tid:
            raise ValueError('Select two different teams')
        def ids(text):
            return [catalogue.resolve(x.strip()).player_id for x in text.split(',') if x.strip()]
        weeks = args.weeks_left if args.weeks_left is not None else (meta['playoff_weeks'] if args.horizon == 'playoffs' else cfg.league.calendar['regular_season_weeks'])
        result = evaluate_two_team(roster, other, ids(args.give), ids(args.get), cfg.league,
            weeks=weeks, stress=args.stress/100, free_agents=pool, include_replacements=not args.no_replacements)
        from src.league.needs import league_needs, trade_needs
        needs = league_needs(rosters, cfg.league, cfg.league.teams)
        for team_id, before, side in [(tid, roster, result['team_a']), (other_tid, other, result['team_b'])]:
            if team_id in needs:
                after_ids = [pid for _, pid in side['after_profile']['lineup']] + side['after_profile']['bench']
                side['positional_needs'] = trade_needs(before, [values[pid] for pid in after_ids], cfg.league, needs[team_id]['references'])
        result['classification'] = result['classification'].replace('TEAM A', team_name.upper()).replace('TEAM B', args.opponent.upper())
        return {**common, 'opponent': args.opponent, **result}
    raise ValueError('Unknown league command')
