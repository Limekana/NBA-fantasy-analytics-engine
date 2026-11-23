"""Write-once pre-week team forecasts, scored later against final Sleeper results.

Season-average team strength cannot be checked against a single week, so each
save prices that week's real game counts. A saved week is never overwritten: a
forecast edited after the fact could not measure anything. Saves made after the
week's first game are kept but marked late and excluded from headline scores.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pandas as pd

from src.league.service import context, forecast_paths
from src.league.storage import league_dir
from src.league.weekly import weekly_value
from src.trade import roster_strength

SCHEMA_VERSION = 1
# Tipoff times are not loaded; the earliest NBA starts are around noon US Eastern.
EARLIEST_TIPOFF_UTC_HOUR = 16
UNAVAILABLE_FLAGS = {'out', 'ir', 'sus', 'suspended', 'pup'}


def forecast_dir(cfg):
    return league_dir(cfg) / 'seasons' / str(cfg.league.raw['meta']['season']) / 'forecasts'


def week_table(games):
    """Fantasy weeks on the shared Monday anchor: {week: sorted fixture dates}."""
    dates = sorted(date.fromisoformat(g['date']) for g in games)
    if not dates:
        raise ValueError('No schedule in the forecast; run league-build')
    anchor = dates[0] - timedelta(days=dates[0].weekday())
    weeks = {}
    for day in dates:
        weeks.setdefault((day - anchor).days // 7 + 1, []).append(day)
    return anchor, weeks


def _cutoff(first_game):
    return datetime(first_game.year, first_game.month, first_game.day, EARLIEST_TIPOFF_UTC_HOUR, tzinfo=timezone.utc)


def _load(path):
    data = path.read_bytes()
    manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError(f'Saved forecast changed after saving: {path.name}')
    return json.loads(data)


def _team_forecast(ids, values, catalogue, advisor, week_games, correlation, cfg):
    players, rows, missing = [], [], []
    for pid in ids:
        identity = catalogue.players[pid]
        player, entry = values.get(pid), advisor['players'].get(pid)
        if player is None or entry is None:
            missing.append(identity.name)
            continue
        games = sum(identity.team in (g['home'], g['away']) for g in week_games)
        unavailable = (identity.injury_status or '').strip().lower() in UNAVAILABLE_FLAGS
        value, available = 0.0, 0.0
        if games and not unavailable:
            value, available = weekly_value(entry['sample'], {games: 1}, float(entry['play_probability']), correlation)
        rows.append({'id': pid, 'name': identity.name, 'nba_team': identity.team, 'games': games,
                     'value': value, 'availability': available, 'injury_status': identity.injury_status})
        if value > 0 and available > 0:
            players.append(replace(player, weekly_value=value, availability=available))
    strength = roster_strength(players, cfg.league)
    starters = set(strength.lineup.starter_ids)
    for row in rows:
        row['starter'] = row['id'] in starters
    return {'expected_points': strength.expected_weekly, 'missing_projections': missing, 'players': rows}


def save_week_forecast(cfg, week=None, now=None):
    """Save one week's forecast once. Returns the existing record if already saved."""
    now = now or datetime.now(timezone.utc)
    state, catalogue, values, _records, meta, _rosters, _issues, _pool = context(cfg)
    if not state.verified:
        raise ValueError('Ownership is not verified; refresh from Sleeper before saving a forecast')
    _, meta_path = forecast_paths(cfg)
    advisor_path = meta_path.parent / 'league_lock_advisor.json'
    if meta.get('advisor_sha256') and hashlib.sha256(advisor_path.read_bytes()).hexdigest() != meta['advisor_sha256']:
        raise ValueError('Lock advisor checksum mismatch: run league-build')
    advisor = json.loads(advisor_path.read_text(encoding='utf-8'))
    games = advisor['schedule']['games']
    anchor, weeks = week_table(games)
    if week is None:
        upcoming = [w for w, days in sorted(weeks.items()) if now < _cutoff(days[0])]
        if not upcoming:
            raise ValueError('No upcoming fantasy week in the schedule')
        week = upcoming[0]
    if week not in weeks:
        raise ValueError(f'Week {week} has no loaded fixtures')
    path = forecast_dir(cfg) / f'week-{week:02d}.json'
    if path.exists():
        saved = _load(path)
        return {'status': 'already_saved', 'week': week, 'saved_at': saved['saved_at'], 'pre_week': saved['pre_week']}
    start = anchor + timedelta(days=7 * (week - 1))
    week_games = [g for g in games if start <= date.fromisoformat(g['date']) <= start + timedelta(days=6)]
    correlation = float(cfg.model.get('weekly_availability', {}).get('correlation', 0.5))
    teams = {tid: {'manager': team.manager, **_team_forecast(state.roster(tid, active_only=True), values, catalogue,
                                                             advisor, week_games, correlation, cfg)}
             for tid, team in state.teams.items()}
    record = {'schema_version': SCHEMA_VERSION, 'league_id': str(state.league_id), 'week': week,
              'week_start': start.isoformat(), 'week_end': (start + timedelta(days=6)).isoformat(),
              'first_game': weeks[week][0].isoformat(), 'saved_at': now.isoformat(),
              'pre_week': now < _cutoff(weeks[week][0]), 'rosters_observed_at': state.observed_at,
              'forecast_generated_at': meta['generated_at'], 'in_season_through': meta.get('in_season_through'),
              'config_sha256': meta['config_sha256'], 'values_sha256': meta['values_sha256'],
              'advisor_sha256': meta.get('advisor_sha256'),
              'method': 'Exact Lock-In value for this week\'s game count; players flagged Out count as unavailable; '
                        'best legal lineup with perfect locks, so real scores are expected to run lower',
              'teams': teams}
    data = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'xb') as handle:  # 'x' refuses to replace an existing forecast
        handle.write(data)
    path.with_suffix('.manifest.json').write_text(json.dumps(
        {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'saved_at': record['saved_at']}), encoding='utf-8')
    return {'status': 'saved', 'path': str(path), 'week': week, 'pre_week': record['pre_week'],
            'teams': {t['manager']: round(t['expected_points'], 1) for t in teams.values()}}


def _latest_results(cfg):
    root = forecast_dir(cfg).parent
    pointer = json.loads((root / 'current.json').read_text(encoding='utf-8'))
    snapshot = root / pointer['path']
    manifest = json.loads((snapshot / 'archive_manifest.json').read_text(encoding='utf-8'))
    return pd.read_csv(snapshot / 'sleeper/team_week_results.csv'), int(manifest['current_sleeper_week'])


def forecast_report(cfg):
    """Score saved forecasts on finished weeks; run league-archive first for new results."""
    saved = [_load(p) for p in sorted(forecast_dir(cfg).glob('week-[0-9][0-9].json'))]
    if not saved:
        return {'saved_weeks': [], 'scored_team_weeks': 0, 'note': 'No forecasts saved yet.'}
    results, current_week = _latest_results(cfg)
    rows, matchups, pending, late = [], [], [], []
    for forecast in saved:
        week = forecast['week']
        if week >= current_week:
            pending.append(week)
            continue
        if not forecast['pre_week']:
            late.append(week)
            continue
        actual = results[results.week == week]
        by_id = {str(r.roster_id): r for r in actual.itertuples()}
        mean_actual = actual.points.mean()
        for tid, team in forecast['teams'].items():
            if tid in by_id and not team['missing_projections']:
                rows.append({'week': week, 'manager': team['manager'], 'forecast': team['expected_points'],
                             'actual': float(by_id[tid].points), 'naive': mean_actual})
        for _matchup, pair in actual.groupby('matchup_id'):
            a, b = [str(x) for x in pair.roster_id]
            ta, tb = forecast['teams'].get(a), forecast['teams'].get(b)
            if ta and tb and not ta['missing_projections'] and not tb['missing_projections']:
                pick = a if ta['expected_points'] >= tb['expected_points'] else b
                winner = int(pair.sort_values('points').roster_id.iloc[-1])
                matchups.append(pick == str(winner))
    report = {'saved_weeks': [f['week'] for f in saved], 'pending_weeks': pending, 'excluded_late_saves': late,
              'scored_team_weeks': len(rows)}
    if rows:
        frame = pd.DataFrame(rows)
        error = frame.forecast - frame.actual
        report.update(
            mean_error=round(float(error.mean()), 2), mean_absolute_error=round(float(error.abs().mean()), 2),
            naive_league_average_error=round(float((frame.naive - frame.actual).abs().mean()), 2),
            rank_correlation=round(float(frame.forecast.corr(frame.actual, method='spearman')), 3) if len(frame) > 2 else None,
            matchup_picks_correct=f'{sum(matchups)} of {len(matchups)}',
            by_manager={m: {'weeks': len(g), 'mean_forecast': round(float(g.forecast.mean()), 1),
                            'mean_actual': round(float(g.actual.mean()), 1)} for m, g in frame.groupby('manager')})
    return report
