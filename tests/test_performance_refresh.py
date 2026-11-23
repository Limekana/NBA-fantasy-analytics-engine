from dataclasses import replace
import hashlib
import http.client
import json
from pathlib import Path
import threading
import time

import pandas as pd
import pytest

from test_league_integration import league_fixture
from test_league_refresh import refresh_fixture
from src.config import config_with_overrides
from src.ingestion.csv_source import CSVSource, game_log_files
from src.league.performance_refresh import refresh_performance, validate_refresh
from src.league.service import config_digest
from src.league.storage import league_dir
from src.league.refresh_server import RefreshController, make_server
from src.schemas import BOX_SCORE_FIELDS


@pytest.fixture
def performance_fixture(refresh_fixture):
    cfg,_=refresh_fixture
    cfg=config_with_overrides({'model':{'projection':{'season_weights':{}}}},base=cfg)
    cfg.model['projection']['season_weights']['2025-26']=1
    cfg=replace(cfg,paths={**cfg.paths,'raw':cfg.paths['repo']/'data/raw'})
    (cfg.paths['repo']/'data/external').mkdir(parents=True)
    rows=pd.DataFrame([dict(player_id=str(i),player_name=f'Player {i}',game_id='123',game_date='2026-04-01',
        season='2025-26',team='BOS',opponent='NYK',home=True,started=True,minutes=30,
        **{c:1 for c in BOX_SCORE_FIELDS}) for i in range(1,6)])
    raw=cfg.paths['raw']/'2025-26/original.csv';raw.parent.mkdir(parents=True)
    rows.to_csv(raw,index=False)
    output=cfg.paths['outputs']
    meta=json.loads((output/'league_forecast.json').read_text())
    meta['config_sha256']=config_digest(cfg)
    (output/'league_forecast.json').write_text(json.dumps(meta))
    (output/'league_lock_advisor.json').write_text('{}')

    def build(stage,seasons):
        stage.paths['outputs'].mkdir()
        for n in ['league_player_values.csv','league_forecast.json','league_lock_advisor.json']:
            (stage.paths['outputs']/n).write_bytes((output/n).read_bytes())
        meta=json.loads((output/'league_forecast.json').read_text())
        meta['sources']=[{'path':str(game_log_files(stage.paths['raw']/'2025-26')[0]),'sha256':'test'}]
        (stage.paths['outputs']/'league_forecast.json').write_text(json.dumps(meta))
    class Source:
        name='test NBA source'
        def fetch_game_logs(self,season):
            return rows.assign(points=2)
    return cfg,rows,Source(),build


def published(cfg):
    files=list(cfg.paths['outputs'].glob('*'))+[cfg.paths['raw']/'2025-26/current.json',cfg.paths['raw']/'2025-26/original.csv']
    return {p:p.read_bytes() if p.exists() else None for p in files}


def test_refresh_publishes_versioned_download_without_mutating_original(performance_fixture):
    cfg,rows,source,build=performance_fixture
    old=(cfg.paths['raw']/'2025-26/original.csv').read_bytes()
    result=refresh_performance(cfg,source,build=build)
    assert result['downloaded'] and result['performance_refreshed_at']
    assert (cfg.paths['raw']/'2025-26/original.csv').read_bytes()==old
    assert CSVSource(cfg.paths['raw']).fetch_game_logs('2025-26').points.eq(2).all()
    html=(cfg.paths['outputs']/'league_dashboard.html').read_text(encoding='utf-8')
    assert 'Refresh NBA performance' in html
    assert not (league_dir(cfg)/'refresh.lock').exists()
    result2=refresh_performance(cfg,source,build=build)
    assert len(CSVSource(cfg.paths['raw']).fetch_game_logs('2025-26'))==5
    assert len(list((cfg.paths['raw']/'2025-26/snapshots').glob('*.csv')))==2


@pytest.mark.parametrize('failure',['network','partial','missing','build','commit'])
def test_performance_failure_keeps_raw_pointer_and_published_forecast(performance_fixture,monkeypatch,failure):
    cfg,rows,source,build=performance_fixture
    before=published(cfg)
    if failure=='network':
        def fail(_):raise OSError('NBA unavailable')
        source.fetch_game_logs=fail
    if failure=='partial':source.fetch_game_logs=lambda _:rows.iloc[:2]
    if failure=='missing':source.fetch_game_logs=lambda _:rows.drop(columns=['turnovers'])
    if failure=='build':
        def build(*_):raise ValueError('Bad forecast')
    if failure=='commit':
        import src.league.refresh_server as module
        original=module.atomic_bytes
        failed=False
        def fail(path,data):
            nonlocal failed
            if Path(path).name=='league_dashboard.html' and not failed:
                failed=True;raise OSError('Publication failed')
            return original(path,data)
        monkeypatch.setattr(module,'atomic_bytes',fail)
    with pytest.raises((ValueError,OSError)):
        refresh_performance(cfg,source,build=build)
    assert published(cfg)==before
    assert not (league_dir(cfg)/'refresh.lock').exists()


def test_versioned_pointer_rejects_checksum_and_path_escape(performance_fixture):
    cfg,rows,source,build=performance_fixture
    refresh_performance(cfg,source,build=build)
    path=cfg.paths['raw']/'2025-26/current.json'
    item=json.loads(path.read_text());item['sha256']='bad';path.write_text(json.dumps(item))
    with pytest.raises(ValueError,match='checksum'):game_log_files(path.parent)
    item['file']='../../escape.csv';path.write_text(json.dumps(item))
    with pytest.raises(ValueError,match='path'):game_log_files(path.parent)


def test_performance_and_sleeper_refresh_share_a_job_lock(performance_fixture):
    cfg,_,source,build=performance_fixture
    started=threading.Event();release=threading.Event()
    def performance(_):started.set();release.wait(5);return {'downloaded':True}
    controller=RefreshController(cfg,performance=performance)
    server=make_server(cfg,0,controller)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(headers):
        con=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        con.request('POST','/api/refresh-performance',body=b'{}',headers=headers)
        response=con.getresponse();body=response.read();status=response.status;con.close()
        return status,body
    try:
        assert request({})[0]==403
        headers={'Origin':f'http://127.0.0.1:{server.server_port}','X-Refresh-Token':controller.token,'Content-Type':'application/json'}
        status,body=request(headers)
        assert status==202 and started.wait(2)
        assert controller.start('sleeper')[1]['job_id']==json.loads(body)['job_id']
        assert controller.status()['kind']=='performance'
    finally:
        release.set();server.shutdown();server.server_close();thread.join(3)


def test_null_scores_and_synthetic_downloads_are_rejected(performance_fixture):
    _,rows,_,_=performance_fixture
    with pytest.raises(ValueError):validate_refresh(rows.assign(points=float('nan')),rows,'2025-26')
    with pytest.raises(ValueError):validate_refresh(rows.assign(is_synthetic=True),rows,'2025-26')


def _drop_rostered_projection(cfg):
    import hashlib
    from src.league.storage import load_league
    state,_=load_league(cfg)
    pid=next(p for tid in state.teams for p in state.roster(tid,active_only=True))
    output=cfg.paths['outputs']
    values=pd.read_csv(output/'league_player_values.csv',dtype={'sleeper_id':str})
    values[values.sleeper_id!=pid.split(':')[1]].to_csv(output/'league_player_values.csv',index=False)
    meta=json.loads((output/'league_forecast.json').read_text())
    meta['values_sha256']=hashlib.sha256((output/'league_player_values.csv').read_bytes()).hexdigest()
    (output/'league_forecast.json').write_text(json.dumps(meta))


def test_known_published_roster_issue_does_not_block_performance_refresh(performance_fixture):
    from src.league.service import snapshot
    from src.reporting.league_dashboard import render_league_dashboard
    cfg,_rows,source,build=performance_fixture
    _drop_rostered_projection(cfg)
    data=snapshot(cfg)
    assert any(t['issues'] for t in data['teams'])
    render_league_dashboard(data,cfg.paths['outputs']/'league_dashboard.html')
    result=refresh_performance(cfg,source,build=build)
    assert result['downloaded']


def test_new_roster_issue_still_blocks_performance_refresh(performance_fixture):
    cfg,_rows,source,build=performance_fixture
    _drop_rostered_projection(cfg)
    with pytest.raises(ValueError,match='lacks complete verified active-roster coverage: .*Missing projections'):
        refresh_performance(cfg,source,build=build)
