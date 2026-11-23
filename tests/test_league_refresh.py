"""Transactional refresh, loopback security and UI contract."""
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import threading
import time

import pandas as pd
import pytest

from test_league_integration import league_fixture
from src.league.storage import import_bundle, league_dir, load_league
from src.league.service import forecast_paths, config_digest
from src.league.refresh_server import refresh_league, make_server, RefreshController


@pytest.fixture
def refresh_fixture(league_fixture):
    cfg,bundle=league_fixture
    import_bundle(cfg,bundle)
    values,meta=forecast_paths(cfg);values.parent.mkdir()
    pd.DataFrame([{'player_name':f'Player {i}','sleeper_id':str(i),'player_id':str(i),
        'conditional_lockin_value':20.,'weekly_availability':.9,'historical_games':40,
        'playoff_weeks':3,'team':'BOS'} for i in range(1,6)]).to_csv(values,index=False)
    meta.write_text(json.dumps({'config_sha256':config_digest(cfg),'generated_at':'2026-09-06',
        'values_sha256':hashlib.sha256(values.read_bytes()).hexdigest(),'warnings':[], 'sources':[], 'seasons':['test']}))
    (values.parent/'league_dashboard.html').write_text("<html>old connect-src 'none'</html>")
    changed=deepcopy(bundle)
    changed['retrieved_at']='2026-09-07T10:00:00+00:00'
    changed['transactions']=[{'transaction_id':'1','status':'complete','created':1000,
        'type':'free_agent','adds':{'5':7},'drops':{'4':7}}]
    changed['rosters'][0]['players']=['1','5']
    return cfg,changed


def frozen_files(cfg):
    return {p:p.read_bytes() for p in [league_dir(cfg)/'current.json',league_dir(cfg)/'sleeper_events.jsonl',cfg.paths['outputs']/'league_dashboard.html']}


def test_refresh_updates_ownership_free_agents_needs_and_change_summary(refresh_fixture):
    cfg,bundle=refresh_fixture
    result=refresh_league(cfg,fetch=lambda _:bundle)
    state,_=load_league(cfg)
    assert state.verified and state.roster('7')==['sleeper:1','sleeper:5']
    assert result['events_added']==1
    assert result['changes']==[{'manager':'A','added':['Player 5'],'dropped':['Player 4'],'reserve_changed':False}]
    html=(cfg.paths['outputs']/'league_dashboard.html').read_text(encoding='utf-8')
    data=json.loads(html.split('<script id="league-data" type="application/json">')[1].split('</script>')[0])
    assert 'sleeper:4' in data['free_agents'] and 'sleeper:5' not in data['free_agents']
    assert data['teams'][0]['positional_needs']
    assert data['last_refresh']['changes']==result['changes']
    assert "connect-src 'none'" in html
    prefix=(league_dir(cfg)/'sleeper_events.jsonl').read_bytes()
    second=refresh_league(cfg,fetch=lambda _:bundle)
    assert second['changes']==[] and second['events_added']==0
    assert (league_dir(cfg)/'sleeper_events.jsonl').read_bytes()==prefix


@pytest.mark.parametrize('failure',['network','reconcile','snapshot','commit'])
def test_failed_refresh_preserves_published_files(refresh_fixture,monkeypatch,failure):
    cfg,bundle=refresh_fixture;old=frozen_files(cfg)
    def fetch(_):
        if failure=='network':raise OSError('Network down')
        if failure=='reconcile':bundle['rosters'][0]['players']=['1','4']
        return bundle
    extra={}
    if failure=='snapshot':
        def broken(_):raise ValueError('Projection checksum failed')
        extra['make_snapshot']=broken
    if failure=='commit':
        import src.league.refresh_server as module
        original=module.atomic_bytes;failed=False
        def broken(path,data):
            nonlocal failed
            if Path(path).name=='league_dashboard.html' and not failed:
                failed=True;raise OSError('Publish failed')
            return original(path,data)
        monkeypatch.setattr(module,'atomic_bytes',broken)
    with pytest.raises((OSError,ValueError)):
        refresh_league(cfg,fetch=fetch,**extra)
    assert frozen_files(cfg)==old
    assert not (league_dir(cfg)/'refresh.lock').exists()


def test_refresh_lock_blocks_imports(refresh_fixture):
    cfg,bundle=refresh_fixture
    lock=league_dir(cfg)/'refresh.lock';lock.touch()
    with pytest.raises(ValueError,match='refresh'):
        import_bundle(cfg,bundle)
    with pytest.raises(ValueError,match='refresh'):
        refresh_league(cfg,fetch=lambda _:bundle)


def test_local_server_limits_paths_and_requires_same_origin_token(refresh_fixture):
    cfg,_=refresh_fixture
    started=threading.Event();release=threading.Event()
    def refresh(_):started.set();release.wait(5);return {'changes':[]}
    controller=RefreshController(cfg,refresh=refresh)
    server=make_server(cfg,0,controller)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    def request(method,path,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        connection.request(method,path,body=b'{}' if method=='POST' else None,headers=headers or {})
        response=connection.getresponse();body=response.read();status=response.status
        connection.close();return status,body
    try:
        code,body=request('GET','/');assert code==200 and b"connect-src 'self'" in body
        assert request('GET','/../config/league_instance.yaml')[0]==404
        assert request('GET','/',{'Host':'attacker.example'})[0]==403
        assert request('GET','/api/session',{'Sec-Fetch-Site':'cross-site'})[0]==403
        token=json.loads(request('GET','/api/session')[1])['token']
        assert request('GET','/api/refresh')[0]==404
        assert request('POST','/api/refresh')[0]==403
        headers={'Origin':origin,'X-Refresh-Token':token,'Content-Type':'application/json'}
        assert request('POST','/api/refresh',{**headers,'Origin':'null'})[0]==403
        assert request('POST','/api/refresh',{**headers,'Origin':'https://evil.example'})[0]==403
        code,body=request('POST','/api/refresh',headers);assert code==202
        assert started.wait(2)
        first=json.loads(body)['job_id']
        assert json.loads(request('POST','/api/refresh',headers)[1])['job_id']==first
        release.set()
        for _ in range(100):
            if controller.status()['status']=='succeeded':break
            time.sleep(.01)
        assert controller.status()['status']=='succeeded'
        assert request('POST','/api/refresh',headers)[0]==429
    finally:
        release.set();server.shutdown();server.server_close();thread.join(3)


def test_button_and_refresh_script_are_in_generated_dashboard(refresh_fixture):
    cfg,bundle=refresh_fixture;refresh_league(cfg,fetch=lambda _:bundle)
    html=(cfg.paths['outputs']/'league_dashboard.html').read_text(encoding='utf-8')
    assert 'id="refresh-sleeper"' in html and 'Refresh from Sleeper' in html
    assert 'X-Refresh-Token' in html and 'location.reload()' in html
    assert 'Previous trade suggestions have been cleared' in html


def test_refresh_keeps_saved_matchup_snapshots(refresh_fixture):
    from src.league.matchups import sync_matchups
    cfg,bundle=refresh_fixture
    state,_=load_league(cfg)
    rows=[{'roster_id':int(tid),'matchup_id':i//2+1,'starters':[]} for i,tid in enumerate(sorted(state.teams))]
    sync_matchups(cfg,1,fetch=lambda _:rows)
    refresh_league(cfg,fetch=lambda _:bundle)
    html=(cfg.paths['outputs']/'league_dashboard.html').read_text(encoding='utf-8')
    data=json.loads(html.split('<script id="league-data" type="application/json">')[1].split('</script>')[0])
    assert list(data['matchups'])==['1']
