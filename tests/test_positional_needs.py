"""Position rankings and targeted suggestions must agree across Python/browser."""
from dataclasses import replace
import json
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from src.config import load_config, config_with_overrides
from src.league.needs import league_needs, position_metrics, score_needs, trade_needs
from src.reporting.trade_dashboard import LINEUP_JS
from src.trade import RosterPlayer, expand_slots


ROOT = Path(__file__).resolve().parents[1]


def p(pid, pos, value, availability=1):
    return RosterPlayer(pid, pid, pos[0], value, availability, positions=tuple(pos))


def small_cfg():
    return config_with_overrides({'league': {'roster': {'PG': 1, 'SG':0, 'G':0, 'SF':0, 'PF':0, 'F':0, 'C':1, 'UTIL':0, 'BN':1, 'IR':0}}}).league


def run_js(body):
    if not shutil.which('node'): pytest.skip('Node required for browser parity')
    code = LINEUP_JS + '\n' + '\n'.join((ROOT/'src/reporting'/f).read_text(encoding='utf-8') for f in ['league_math.js','league_advisor_math.js','league_needs.js'])
    r = subprocess.run(['node','-'],input=code+'\n'+body,text=True,encoding='utf-8',capture_output=True,timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def payload(roster):
    return [dict(id=p.player_id,name=p.name,position=p.position,positions=list(p.positions),value=p.weekly_value,availability=p.availability,isPick=False,slots=[]) for p in roster]


def cfgjs(cfg):
    return dict(slots=expand_slots(cfg),elig=dict(cfg.position_eligibility),rosterLimit=cfg.roster_size,stress=.1,weeksRemaining=22)


def test_elite_pg_is_a_depth_need_not_a_production_need():
    cfg=small_cfg()
    rosters={'a':[p('a1',['PG'],40),p('a2',['C'],30),p('a3',['C'],28)],
             'b':[p('b1',['PG'],30),p('b2',['C'],30),p('b3',['PG'],25)],
             'c':[p('c1',['PG'],32),p('c2',['C'],30),p('c3',['PG'],26)]}
    data=league_needs(rosters,cfg,3)['a']
    pg=next(r for r in data['rows'] if r['position']=='PG')
    assert pg['production_rank']==1
    assert pg['production_gap']==0
    assert pg['cover_gap']>0
    assert not pg['bench_cover']
    after=[rosters['a'][0],rosters['a'][1],p('b3',['PG'],25)]
    diff={r['position']:r for r in trade_needs(rosters['a'],after,cfg,data['references'])}
    assert diff['PG']['improvement']>0
    assert diff['PG']['production_change']==0
    assert diff['C']['outage_loss_change']>0  # show surrendered cover even below peer median


def test_bench_player_does_not_consume_two_positions_and_empty_is_explicit():
    cfg=small_cfg()
    r=[p('dual',['PG','C'],30),p('pg',['PG'],29),p('bench',['C'],10)]
    metrics=position_metrics(r,cfg)
    assert metrics['PG']['eligible_count']==2
    assert metrics['C']['eligible_count']==2
    assert metrics['PG']['bench_cover']==[]
    assert metrics['C']['bench_cover']==['bench']
    assert 'SF' not in metrics  # no starting slot accepts an SF in this fixture


def test_incomplete_league_withholds_ranks_and_ties_share_rank():
    cfg=small_cfg()
    r={'a':[p('a',['PG'],20)],'b':[p('b',['PG'],20)]}
    assert league_needs(r,cfg,3)=={}
    data=league_needs(r,cfg,2)
    for t in data.values():
        row=next(r for r in t['rows'] if r['position']=='PG')
        assert row['production_rank']==1
        assert row['need_score']==0


def test_needs_are_invariant_to_input_order_and_references_frozen():
    cfg=load_config().league
    r={'a':[p('a',['PG'],40),p('b',['C'],25),p('c',['SF','PF'],30)],
       'b':[p('d',['PG'],35),p('e',['C'],28),p('f',['SF','PF'],27)]}
    n=league_needs(r,cfg,2)
    assert n==league_needs({k:list(reversed(v)) for k,v in reversed(list(r.items()))},cfg,2)
    saved=json.dumps(n,sort_keys=True)
    trade_needs(r['a'],r['b'],cfg,n['a']['references'])
    assert json.dumps(n,sort_keys=True)==saved


def test_random_position_metrics_and_trade_changes_python_browser_parity():
    cfg=load_config().league;rng=random.Random(871)
    rosters={str(t):[p(f'{t}:{i}',rng.sample(['PG','SG','SF','PF','C'],rng.randint(1,3)),rng.uniform(10,45),rng.uniform(.6,1)) for i in range(14)] for t in range(4)}
    needs=league_needs(rosters,cfg,4)
    out=run_js('const rosters='+json.dumps({t:payload(r) for t,r in rosters.items()})+';const needs='+json.dumps(needs)+';const cfg='+json.dumps(cfgjs(cfg))+';console.log(JSON.stringify(Object.fromEntries(Object.entries(rosters).map(([t,r])=>[t,scoreNeeds(positionMetrics(r,cfg),needs[t].references)]))));')
    for tid,rows in out.items():
        for actual,expected in zip(rows,needs[tid]['rows']):
            for k,v in expected.items():
                if isinstance(v,float): assert actual[k]==pytest.approx(v,abs=1e-8)
                else: assert actual[k]==v


def test_need_focused_search_respects_profit_and_reports_all_position_changes():
    cfg=small_cfg()
    mine=[p('a',['PG'],40),p('b',['C'],30),p('c',['C'],25)]
    other=[p('d',['C'],35),p('e',['PG'],28),p('f',['PG'],26)]
    third=[p('g',['PG'],30),p('h',['C'],28),p('i',['PG'],25)]
    data=league_needs({'a':mine,'b':other,'c':third},cfg,3)
    body='const mine='+json.dumps(payload(mine))+';const other='+json.dumps(payload(other))+';const cfg='+json.dumps(cfgjs(cfg))+';const needs='+json.dumps(data)+';'+'''
(async()=>{
const result=await findLeagueTrades(mine,[{id:'b',roster:other,needReferences:needs.b.references}],[],cfg,{packages:false,needFocus:['PG'],needReferences:needs.a.references});
for(const r of result.results){
 if(r.focus_gain<=0||r.total_need_gain<-1e-8)throw Error('Wrong need filter');
 if(r.result.team_a.delta_weekly<=0||r.result.team_b.delta_weekly<0.1)throw Error('Profit filter bypassed');
 if(!r.needs_a||!r.needs_b)throw Error('Missing explanations');
}
console.log(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result=run_js(body)
    # Availability=1 means swapping purely bench insurance has no expected gain;
    # the finder must not invent a recommendation just to satisfy a need.
    assert result['results']==[]


def test_need_focused_search_can_find_profitable_cover_swap():
    cfg=small_cfg()
    rosters={'a':[p('a',['PG'],40,.6),p('b',['C'],30,.95),p('c',['C'],20,.8)],
             'b':[p('d',['C'],35,.6),p('e',['PG'],28,.95),p('f',['PG'],26,.8)],
             'c':[p('g',['PG'],30,.8),p('h',['C'],28,.8),p('i',['PG'],25,.8)]}
    needs=league_needs(rosters,cfg,3)
    result=run_js('const r='+json.dumps({t:payload(r) for t,r in rosters.items()})+'; const cfg='+json.dumps(cfgjs(cfg))+';const n='+json.dumps(needs)+';'+'''
(async()=>{const out=await findLeagueTrades(r.a,[{id:'b',roster:r.b,needReferences:n.b.references}],[],cfg,{packages:false,needFocus:['PG'],needReferences:n.a.references});console.log(JSON.stringify(out));})();
''')
    assert result['results']
    for item in result['results']:
        assert item['focus_gain']>0
        assert item['total_need_gain']>=-1e-8
        assert item['result']['team_a']['delta_weekly']>0
        assert item['result']['team_b']['delta_weekly']>=.1
