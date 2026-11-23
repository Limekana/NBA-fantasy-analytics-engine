"""Power ranks use the exact lineup model; ties and missing data stay honest."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from src.reporting.trade_dashboard import LINEUP_JS
from src.trade import roster_strength, ExactAvailabilityConfig
from test_positional_needs import p, payload, cfgjs, small_cfg

ROOT=Path(__file__).resolve().parents[1]


def run_rankings(teams,players,cfg,mode='baseline'):
    if not shutil.which('node'):pytest.skip('Node required')
    script=LINEUP_JS+'\n'+(ROOT/'src/reporting/league_math.js').read_text(encoding='utf-8')+'\n'+(ROOT/'src/reporting/league_rankings.js').read_text(encoding='utf-8')
    script+='\nconsole.log(JSON.stringify(leaguePowerRankings('+','.join(map(json.dumps,[teams,players,cfgjs(cfg),mode]))+')));'
    result=subprocess.run(['node','-'],input=script,text=True,encoding='utf-8',capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def fixture():
    cfg=small_cfg()
    rosters={'a':[p('star',['PG'],40,.5),p('backup',['PG'],30,.5),p('center',['C'],20)],
             'b':[p('pg',['PG'],30),p('c',['C'],25)]}
    players={q['id']:q for r in rosters.values() for q in payload(r)}
    teams=[{'team_id':tid,'manager':tid,'roster':[p.player_id for p in r],'reserve':['ir'] if tid=='a' else []} for tid,r in rosters.items()]
    return cfg,rosters,players,teams


def test_rank_order_and_depth_match_exact_python_model():
    cfg,rosters,players,teams=fixture()
    result=run_rankings(teams,players,cfg)
    assert result['complete']
    assert [r['team_id'] for r in result['rows']]==['b','a']
    for row in result['rows']:
        s=roster_strength(rosters[row['team_id']],ExactAvailabilityConfig(cfg))
        assert row['expected']==pytest.approx(s.expected_weekly)
        assert row['healthy']==pytest.approx(s.healthy_weekly)
        starters=[p for p in rosters[row['team_id']] if p.player_id in s.lineup.starter_ids]
        assert row['bench']==pytest.approx(s.expected_weekly-roster_strength(starters,ExactAvailabilityConfig(cfg)).expected_weekly)
    assert result['rows'][0]['gap']==0
    assert result['rows'][1]['gap']==pytest.approx(7.5)


def test_injury_stress_can_change_order_without_inventing_return_dates():
    cfg,_,players,teams=fixture()
    players['pg']['injury_status']='Out'
    baseline=run_rankings(teams,players,cfg)
    stressed=run_rankings(teams,players,cfg,'injury')
    assert baseline['rows'][0]['team_id']=='b'
    assert stressed['rows'][0]['team_id']=='a'
    assert stressed['rows'][1]['score']==25
    assert stressed['rows'][1]['injury_drop']==30
    assert players['pg']['availability']==1


def test_ties_share_competition_rank_at_display_precision():
    cfg,_,players,_=fixture()
    teams=[{'team_id':str(i),'manager':str(i),'roster':[str(i)]} for i in range(3)]
    players={str(i):payload([p(str(i),['PG'],v)])[0] for i,v in enumerate([30.001,30.002,20])}
    result=run_rankings(list(reversed(teams)),players,cfg)
    assert [r['rank'] for r in result['rows']]==[1,1,3]
    assert [r['team_id'] for r in result['rows']]==['0','1','2']


def test_missing_projection_withholds_entire_league_rank_not_zero_fill():
    cfg,_,players,teams=fixture()
    del players['pg']
    result=run_rankings(teams,players,cfg)
    assert result=={'rows':[],'missing':['b'],'complete':False}


def test_reserve_does_not_require_projection_or_enter_rank():
    cfg,_,players,teams=fixture()
    result=run_rankings(teams,players,cfg)
    assert result['complete']
    assert next(r for r in result['rows'] if r['team_id']=='a')['reserve']==['ir']


def test_new_roster_snapshot_recalculates_ranks_without_saved_search_state():
    cfg,_,players,teams=fixture()
    before=run_rankings(teams,players,cfg)
    players['new']=payload([p('new',['PG'],60)])[0]
    teams[0]['roster']=['new','center']
    after=run_rankings(teams,players,cfg)
    assert before['rows'][0]['team_id']=='b'
    assert after['rows'][0]['team_id']=='a'
    assert after['rows'][0]['expected']==80
