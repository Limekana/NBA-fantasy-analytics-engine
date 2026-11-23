"""Planned starters, honest insurance and week-specific trade scenarios."""
import itertools
import json
import random
from pathlib import Path

import pytest
from test_positional_needs import run_js

MODULE = (Path(__file__).resolve().parents[1] / 'src/reporting/league_trade_context.js').read_text(encoding='utf-8')
CFG = {'slots':['PG','UTIL'], 'elig':{'PG':['PG','UTIL'],'SF':['UTIL']}, 'rosterLimit':4, 'stress':.1, 'weeksRemaining':22, 'exactAvailability':True}

def player(pid, pos, value, availability=1):
    return {'id':pid,'name':pid,'positions':[pos],'value':value,'availability':availability}

def js(body):
    return run_js(MODULE + '\nconst cfg='+json.dumps(CFG)+';\n'+body)

def test_planned_starters_are_retained_with_bench_replacing_absences():
    players=[player('star','PG',40,.5),player('chosen','SF',10),player('bench','PG',30)]
    result=js('const ps='+json.dumps(players)+''';
const c={...cfg,plannedStarterIds:['star','chosen']};
console.log(JSON.stringify([optimalLineup(ps,c).total,jointExpectation(ps,c),jointExpectation(ps,cfg),jointExpectation(ps,c)]));''')
    assert result == [50,45,55,45]

@pytest.mark.parametrize('ids', [['star','star'], ['star'], ['missing','wing'], ['wing','star']])
def test_plan_rejects_duplicate_missing_or_ineligible(ids):
    assert js('try {validateStarterPlan('+json.dumps([player('star','PG',40),player('wing','SF',20)])+','+json.dumps(ids)+',cfg);console.log(false)}catch(e){console.log(true)}')

def test_general_lineup_gain_is_not_insurance():
    before=[player('star','PG',40),player('wing','SF',20),player('backup','PG',25)]
    after=[before[0],before[2],player('incoming','PG',22)]
    result=js('console.log(JSON.stringify(plannedTradeImpact('+json.dumps(before)+','+json.dumps(after)+",cfg,['star','wing'],'star',true))); ")
    assert result['healthy_gain'] == 5  # vacated starting slot selects existing backup
    assert result['leader_out_gain'] == 2
    assert result['insurance_gain'] == -3
    assert result['missing_starters'] == ['wing']

def test_untouched_plan_gets_no_healthy_bench_credit():
    before=[player('star','PG',40),player('wing','SF',20),player('bench','SF',10)]
    after=before[:2]+[player('guard','PG',25)]
    result=js('console.log(JSON.stringify(plannedTradeImpact('+json.dumps(before)+','+json.dumps(after)+",cfg,['star','wing'],'star',true))); ")
    assert result['healthy_gain'] == 0
    assert result['insurance_gain'] == 25
    assert result['gain'] == 0  # everybody always available: no invented bench profit

def test_week_uses_chronological_games_and_no_double_availability_discount():
    result=js('''const ps=[{id:'a',name:'a',positions:['PG'],value:99,availability:.1}];
const advisor={players:{a:{team:'BOS',sample:[10,30],play_probability:.5}},schedule:{games:[{date:'2026-10-23',home:'BOS',away:'DET'},{date:'2026-10-20',home:'DET',away:'BOS'}]}};
console.log(JSON.stringify(weekRosterScenario(ps,advisor,1,cfg,['a'])));''')
    assert result['available']
    assert result['total'] == 15
    assert result['bounds'] == {'start':'2026-10-19','end':'2026-10-25'}

def test_missing_games_are_not_silently_zero():
    result=js('console.log(JSON.stringify(weekRosterScenario('+json.dumps([player('a','PG',20)])+",{players:{},schedule:{games:[{date:'2026-10-20',home:'BOS',away:'DET'}]}},1,cfg))); ")
    assert not result['available']
    assert 'Missing' in result['reason']

def test_research_missing_stale_future_and_unsafe_sources():
    result=js('''const ps=['missing','stale','future','unsafe','good'].map(id=>({id,name:id}));
const good={reviewed_at:'2026-09-08',role_source:'https://www.nba.com/example',role_note:'Role uncertainty',market_source:'https://example.com/values',market_note:'Reviewed opinion'};
console.log(JSON.stringify(tradeResearchStatus(ps,{stale:{...good,reviewed_at:'2026-08-01'},future:{...good,reviewed_at:'2026-10-01'},unsafe:{...good,role_source:'javascript:alert(1)'},good},new Date('2026-09-09'))));''')
    assert [r['complete'] for r in result] == [False,False,False,False,True]
    assert result[3]['role_source'] is None

def test_planned_exact_expectation_matches_independent_enumeration():
    rng=random.Random(913)
    for _ in range(10):
        ps=[player('a','PG',rng.randint(10,40),.5),player('b','SF',rng.randint(1,20),.75),player('c','PG',rng.randint(20,40),.8),player('d','SF',rng.randint(1,35),.6)]
        expected=0
        for flags in itertools.product([False,True],repeat=4):
            active=[p for p,on in zip(ps,flags) if on]
            weight=1
            for p,on in zip(ps,flags):weight*=p['availability'] if on else 1-p['availability']
            choices=[]
            for n in range(3):
                for selected in itertools.combinations(active,n):
                    if n==2 and all(p['positions']==['SF'] for p in selected):continue
                    choices.append((sum(p['id'] in ['a','b'] for p in selected),sum(p['value'] for p in selected)))
            expected+=weight*max(choices)[1]
        actual=js('console.log(jointExpectation('+json.dumps(ps)+",{...cfg,plannedStarterIds:['a','b']}));")
        assert actual == pytest.approx(expected)
