"""Offline end-to-end fixtures and the duplicated browser-math contract."""
import json
import random
import shutil
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.config import load_config, config_with_overrides
from src.ingestion.sleeper_source import diff_scoring, SLEEPER_SCORING_KEYS
from src.league.storage import import_bundle, load_league, league_dir
from src.league.service import context, require_team, config_digest, forecast_paths
from src.league.trades import evaluate_two_team
from src.league.replacements import add_drop_candidates, replaceability
from src.pipeline import reconcile_players
from src.reporting.trade_dashboard import LINEUP_JS
from src.reporting.league_dashboard import render_league_dashboard
from src.trade import RosterPlayer, expand_slots


@pytest.fixture
def league_fixture(tmp_path):
    cfg = config_with_overrides({'league': {'league': {'teams': 2},
        'roster': {'PG': 1, 'SG': 0, 'G': 0, 'SF': 0, 'PF': 0, 'F': 0, 'C': 0, 'UTIL': 0, 'BN': 1, 'IR': 1},
        'draft': {'rounds': 2}}})
    cfg = replace(cfg, paths={**cfg.paths, 'repo': tmp_path, 'outputs': tmp_path/'outputs'})
    (tmp_path/'config').mkdir()
    (tmp_path/'config/league_instance.yaml').write_text(yaml.safe_dump({'league_id':'123',
        'my_team':'A','draft_order':['A','B'],'manager_aliases':{'A':['alpha'],'B':['beta']}}))
    scores = {**cfg.league.raw['scoring'], **cfg.league.raw['bonuses']}
    draft = {'draft_id':'d','type':'snake','status':'complete','slot_to_roster_id':{'1':7,'2':9}}
    bundle = {'league_id':'123','retrieved_at':'2026-09-06T12:00:00+00:00','transaction_rounds':[0,1],
        'league':{'league_id':'123','sport':'nba','total_rosters':2,'settings':{'leg':1},
                  'scoring_settings':{k:scores[v] for k,v in SLEEPER_SCORING_KEYS.items()}},
        'draft':draft,'users':[{'user_id':'u1','display_name':'alpha'},{'user_id':'u2','display_name':'beta'}],
        'rosters':[{'roster_id':7,'owner_id':'u1','players':['1','4'],'reserve':[]},
                   {'roster_id':9,'owner_id':'u2','players':['2','3'],'reserve':[]}],
        'picks':[{'pick_no':i,'draft_slot':slot,'player_id':str(i)} for i,slot in enumerate([1,2,2,1],1)],
        'players':{str(i):{'full_name':f'Player {i}','team':'BOS','status':'ACT','fantasy_positions':['PG']}
                   for i in range(1,6)}, 'transactions':[]}
    return cfg,bundle


def test_import_reimport_load_and_append_completed_api_event(league_fixture):
    cfg,bundle=league_fixture
    state,cat=import_bundle(cfg,bundle)
    assert state.verified and len(state.ownership)==4
    original=(league_dir(cfg)/'sleeper_events.jsonl').read_bytes()
    import_bundle(cfg,bundle)
    assert (league_dir(cfg)/'sleeper_events.jsonl').read_bytes()==original
    assert load_league(cfg)[0].verified
    bundle['transactions']=[{'transaction_id':'1','status':'complete','created':1000,
        'type':'free_agent','adds':{'5':7},'drops':{'4':7}}]
    bundle['rosters'][0]['players']=['1','5']
    state,_=import_bundle(cfg,bundle)
    assert state.last_sequence==5 and state.roster('7')==['sleeper:1','sleeper:5']
    assert (league_dir(cfg)/'sleeper_events.jsonl').read_bytes().startswith(original)


def test_rejected_import_does_not_publish_and_checksum_guards(league_fixture):
    cfg,bundle=league_fixture
    import_bundle(cfg,bundle)
    pointer=(league_dir(cfg)/'current.json').read_bytes()
    bad=deepcopy(bundle);bad['rosters'][0]['players']=['1','5']
    with pytest.raises(ValueError,match='reconcile'):import_bundle(cfg,bad)
    assert (league_dir(cfg)/'current.json').read_bytes()==pointer
    log=league_dir(cfg)/'sleeper_events.jsonl'
    log.write_text(log.read_text().replace('"round": 1','"round": 8'))
    with pytest.raises(ValueError,match='history changed'):load_league(cfg)


def test_live_scoring_aliases_bonuses_and_unknown_keys():
    scores={'turnovers':-1,'three_pointers_made':.5,'points_50_plus':4}
    assert diff_scoring({'scoring_settings':{'to':-1,'tpm':.5,'bonus_pt_50p':4}},scores)==[]
    assert diff_scoring({'scoring_settings':{'tov':-1,'fgm3':.5,'bonus_pt_50p':4}},scores)==[]
    assert diff_scoring({'scoring_settings':{'unexpected':2}},scores)


def test_crosswalk_does_not_choose_between_equally_plausible_namesakes():
    base=pd.DataFrame([{'player_id':'1','player_name':'Chris Smith'}])
    external=pd.DataFrame([{'player_name':'Chris Smith','team':'BOS','age':25,'sleeper_id':'2'},
                           {'player_name':'Chris Smith','team':'LAL','age':26,'sleeper_id':'3'}])
    result=reconcile_players(base,external)
    assert pd.isna(result.iloc[0].sleeper_id) and len(result)==1


def test_missing_projections_block_only_affected_teams(league_fixture):
    import hashlib
    cfg,bundle=league_fixture
    import_bundle(cfg,bundle)
    values,meta=forecast_paths(cfg);values.parent.mkdir()
    pd.DataFrame([{'player_name':f'Player {i}','sleeper_id':str(i),'player_id':str(i),
        'conditional_lockin_value':20.,'weekly_availability':.9,'historical_games':40,'playoff_weeks':3,'team':'BOS'}
        for i in [1,4,5]]).to_csv(values,index=False)
    meta.write_text(json.dumps({'config_sha256':config_digest(cfg),'generated_at':'2026-09-06',
        'values_sha256':hashlib.sha256(values.read_bytes()).hexdigest()}))
    state,cat,players,records,metadata,rosters,issues,pool=context(cfg)
    assert set(rosters)=={'7'} and '9' in issues
    assert [p.player_id for p in pool]==['sleeper:5']
    with pytest.raises(ValueError,match='Missing projections'):require_team(state,rosters,issues,'B')
    with pytest.raises(ValueError,match='Rules/model'):context(replace(cfg,model={**cfg.model,'new':1}))


def test_nonactive_player_cannot_keep_a_stale_projection(league_fixture):
    import hashlib
    cfg,bundle=league_fixture
    bundle['players']['1']['team']=None
    bundle['players']['1']['status']='FA'
    import_bundle(cfg,bundle)
    values,meta=forecast_paths(cfg);values.parent.mkdir()
    pd.DataFrame([{'player_name':f'Player {i}','sleeper_id':str(i),'player_id':str(i),
        'conditional_lockin_value':20.,'weekly_availability':.9,'historical_games':40,
        'playoff_weeks':3,'team':'BOS'} for i in range(1,5)]).to_csv(values,index=False)
    meta.write_text(json.dumps({'config_sha256':config_digest(cfg),'generated_at':'2026-09-06',
        'values_sha256':hashlib.sha256(values.read_bytes()).hexdigest()}))
    _state,_catalogue,players,_records,_metadata,_rosters,issues,_pool=context(cfg)
    assert 'sleeper:1' not in players
    assert '7' in issues and 'Missing projections: Player 1' in issues['7'][0]
    assert 'Player 1 (Sleeper status FA, no active NBA team)' in issues['7'][0]


@pytest.mark.skipif(shutil.which('node') is None,reason='Node required for browser parity')
def test_two_sided_and_add_drop_javascript_parity(tmp_path):
    cfg=load_config().league; rng=random.Random(723);cases=[]
    def player(i):
        positions=tuple(rng.sample(['PG','SG','SF','PF','C'],rng.choice([1,2])))
        return RosterPlayer(str(i),f'P{i}',positions[0],rng.uniform(5,50),rng.uniform(.3,1),positions=positions)
    def encode(p):return {'id':p.player_id,'name':p.name,'position':p.position,'positions':p.positions,
                         'value':p.weekly_value,'availability':p.availability}
    for i in range(16):
        players=[player(i) for i in range(34)];a,b,pool=players[:14],players[14:28],players[28:]
        ga=[p.player_id for p in a[:1+i%3]];gb=[p.player_id for p in b[:1+(i+1)%3]]
        expected=evaluate_two_team(a,b,ga,gb,cfg,weeks=12,stress=.15,free_agents=pool,include_replacements=i%2==0)
        cases.append({'a':list(map(encode,a)),'b':list(map(encode,b)),'pool':list(map(encode,pool)),
            'ga':ga,'gb':gb,'replacements':i%2==0,'expected':expected,
            'moves':add_drop_candidates(a,pool,cfg)})
    js_cfg={'slots':expand_slots(cfg),'slotNames':list(cfg.starting_slots),'elig':dict(cfg.position_eligibility),
            'rosterLimit':14,'stress':.15,'weeksRemaining':12}
    math=(Path(__file__).parents[1]/'src/reporting/league_math.js').read_text()
    script=tmp_path/'parity.js'
    script.write_text(LINEUP_JS+math+'\nconst cases='+json.dumps(cases)+';const cfg='+json.dumps(js_cfg)+''';
console.log(JSON.stringify(cases.map(c=>({trade:evaluateLeagueTrade(c.a,c.b,c.ga,c.gb,c.pool,cfg,c.replacements),moves:leagueAddDrops(c.a,c.pool,cfg)}))));''')
    actual=json.loads(subprocess.check_output([shutil.which('node'),str(script)],text=True))
    for case,result in zip(cases,actual):
        assert result['trade']['classification']==case['expected']['classification']
        for side in ['team_a','team_b']:
            for key in ['before','after','delta_weekly','delta_starters','delta_depth','delta_season','pessimistic','optimistic']:
                assert result['trade'][side][key]==pytest.approx(case['expected'][side][key],abs=1e-6)
            for key in ['drops','pickups']:assert result['trade'][side][key]==case['expected'][side][key]
        assert len(result['moves'])==len(case['moves'])
        for a,b in zip(result['moves'],case['moves']):
            assert a['add']==b['add'] and a['drop']==b['drop']
            assert a['delta_weekly']==pytest.approx(b['delta_weekly'],abs=1e-6)


def test_dashboard_embeds_only_offline_scripts_and_escapes_payload(tmp_path):
    data={'name':'</script><script>alert(1)</script>'}
    output=render_league_dashboard(data,tmp_path/'dashboard.html')
    html=output.read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in html
    assert 'connect-src \'none\'' in html and 'src="http' not in html
    assert '__PAYLOAD__' not in html and '__MATH__' not in html and '__UI__' not in html
    encoded=html.split('<script id="league-data" type="application/json">')[1].split('</script>')[0]
    assert json.loads(encoded)==data


def test_calendar_horizons_flow_through_pipeline(monkeypatch):
    from src.pipeline import run_pipeline
    from src.ingestion.synthetic import generate_players, generate_game_logs
    import src.pipeline as pipeline
    cfg=config_with_overrides({'league':{'calendar':{'regular_season_weeks':2},'playoffs':{'start_week':3}}})
    players=generate_players(10,seed=3)
    logs=generate_game_logs(players,'2025-26',seed=4)
    monkeypatch.setattr(pipeline,'load_seasons',lambda *a,**kw:logs)
    captured=[]; original=pipeline.build_valuations
    def spy(profiles, projections, games, pmfs, *args):
        captured.append(pmfs)
        return original(profiles,projections,games,pmfs,*args)
    monkeypatch.setattr(pipeline,'build_valuations',spy)
    team=players.iloc[0].team
    schedule=pd.DataFrame({'game_date':['2026-10-20','2026-10-27','2026-11-03','2026-11-04'],
                           'team':[team]*4})
    result=run_pipeline(cfg,['2025-26'],players=players,schedule=schedule,allow_synthetic=True)
    assert captured[0][team]=={1:1} and captured[1][team]=={2:1}
    assert (result.board.playoff_weeks==1).all()
    assert 'playoff_conditional_lockin_value' in result.board
