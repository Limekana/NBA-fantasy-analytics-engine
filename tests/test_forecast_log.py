"""Write-once pre-week forecasts and their scoring against final results."""
from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from test_league_integration import league_fixture
from test_league_refresh import refresh_fixture
from src.league.forecast_log import forecast_dir, forecast_report, save_week_forecast
from src.league.storage import league_dir, load_league


def utc(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


@pytest.fixture
def forecast_cfg(refresh_fixture):
    cfg, _bundle = refresh_fixture
    games = [{'date': d, 'home': 'BOS', 'away': 'NYK'} for d in ('2026-10-20', '2026-10-22', '2026-10-27', '2026-10-29')]
    players = {f'sleeper:{i}': {'sample': [10., 20., 30.], 'team': 'BOS', 'play_probability': .9} for i in range(1, 6)}
    (cfg.paths['outputs'] / 'league_lock_advisor.json').write_text(json.dumps({'players': players, 'schedule': {'games': games}}))
    return cfg


def write_results(cfg, current_week, points):
    root = forecast_dir(cfg).parent
    snapshot = root / 'snapshots/test'
    (snapshot / 'sleeper').mkdir(parents=True)
    (root / 'current.json').write_text(json.dumps({'path': 'snapshots/test'}))
    (snapshot / 'archive_manifest.json').write_text(json.dumps({'current_sleeper_week': current_week}))
    pd.DataFrame(points).to_csv(snapshot / 'sleeper/team_week_results.csv', index=False)


def test_default_week_is_next_unstarted_and_saves_are_write_once(forecast_cfg):
    saved = save_week_forecast(forecast_cfg, now=utc(24))
    assert saved['status'] == 'saved' and saved['week'] == 2 and saved['pre_week']
    path = forecast_dir(forecast_cfg) / 'week-02.json'
    before = path.read_bytes()
    record = json.loads(before)
    assert record['week_start'] == '2026-10-26' and record['first_game'] == '2026-10-27'
    assert all(p['games'] == 2 for t in record['teams'].values() for p in t['players'])
    again = save_week_forecast(forecast_cfg, now=utc(26))
    assert again['status'] == 'already_saved' and path.read_bytes() == before


def test_save_on_game_day_after_earliest_tipoff_is_marked_late(forecast_cfg):
    assert save_week_forecast(forecast_cfg, week=2, now=utc(27, 15))['pre_week']
    assert not save_week_forecast(forecast_cfg, week=1, now=utc(24))['pre_week']


def test_flagged_out_player_is_unavailable_in_the_forecast(forecast_cfg):
    from dataclasses import replace
    from src.league.forecast_log import _team_forecast
    from src.league.service import context
    state, catalogue, values, *_rest = context(forecast_cfg)
    advisor = json.loads((forecast_cfg.paths['outputs'] / 'league_lock_advisor.json').read_text())
    pid = next(p for tid in state.teams for p in state.roster(tid, active_only=True) if p in values)
    week = advisor['schedule']['games'][:2]
    healthy = _team_forecast([pid], values, catalogue, advisor, week, .5, forecast_cfg)
    assert healthy['players'][0]['value'] > 0 and healthy['expected_points'] > 0
    catalogue.players[pid] = replace(catalogue.players[pid], injury_status='Out')
    hurt = _team_forecast([pid], values, catalogue, advisor, week, .5, forecast_cfg)
    assert hurt['players'][0]['availability'] == 0 and hurt['expected_points'] == 0


def test_report_scores_finished_pre_week_saves_only(forecast_cfg):
    save_week_forecast(forecast_cfg, week=1, now=utc(24))  # late
    saved = save_week_forecast(forecast_cfg, week=2, now=utc(24))
    state, _ = load_league(forecast_cfg)
    ids = sorted(state.teams)
    record = json.loads((forecast_dir(forecast_cfg) / 'week-02.json').read_text(encoding='utf-8'))
    best = max(ids, key=lambda t: record['teams'][t]['expected_points'])
    rows = [{'week': 2, 'roster_id': int(t), 'matchup_id': 1, 'points': 200. if t == best else 100.} for t in ids]
    write_results(forecast_cfg, 3, rows)
    report = forecast_report(forecast_cfg)
    complete = [t for t in ids if not record['teams'][t]['missing_projections']]
    assert report['excluded_late_saves'] == [1] and report['pending_weeks'] == []
    assert report['scored_team_weeks'] == len(complete)
    if len(complete) == len(ids) == 2:
        assert report['matchup_picks_correct'] == '1 of 1'
    assert 'mean_absolute_error' in report and saved['pre_week']


def test_unfinished_week_stays_pending_and_tampering_is_detected(forecast_cfg):
    save_week_forecast(forecast_cfg, week=2, now=utc(24))
    write_results(forecast_cfg, 2, [{'week': 2, 'roster_id': 7, 'matchup_id': 1, 'points': 100.}])
    assert forecast_report(forecast_cfg)['pending_weeks'] == [2]
    path = forecast_dir(forecast_cfg) / 'week-02.json'
    path.write_text(path.read_text(encoding='utf-8').replace('"week": 2', '"week": 2 '), encoding='utf-8')
    with pytest.raises(ValueError, match='changed after saving'):
        forecast_report(forecast_cfg)
