import json
import shutil
import subprocess
from dataclasses import replace
import pandas as pd
import numpy as np
import pytest

from src.config import load_config
from src.trade import RosterPlayer, optimal_lineup, roster_strength, trim_roster, evaluate_trade, board_to_roster_players
from src.league.weekly import played_games_pmf, weekly_value
from src.league.profiles import profile, relative_profiles
from src.league.trades import evaluate_two_team, classify
from src.league.partners import partners
from src.league.replacements import add_drop_candidates
from src.schedule.calendar import regular_schedule, weekly_counts
from src.schedule.weeks import playoff_week_pmf
from src.reporting.trade_dashboard import LINEUP_JS


def test_week_availability_preserves_mean_and_distinguishes_missing_week():
    independent=played_games_pmf({4:1},.5,0)
    clustered=played_games_pmf({4:1},.5,1)
    assert independent[0]==pytest.approx(1/16)
    assert clustered[0]==.5
    assert sum(independent.values())==pytest.approx(1)
    assert sum(k*v for k,v in independent.items())==pytest.approx(2)
    assert sum(k*v for k,v in clustered.items())==pytest.approx(2)
    assert played_games_pmf({0:1},.6,.5)=={0:1}


def test_weekly_value_scores_a_final_game_dnp_as_zero():
    sample=[0.,10.,40.]
    # Certain play: optimal two-game stopping, W2 = E[max(x, mean)] = 24.44.
    assert weekly_value(sample,{2:1},1.,.5)==pytest.approx((220/9,1.))
    # Independent 50% appearances, final-game DNP = 0: 13.889 / P(>=1 game) 0.75.
    # Knowing the played-game count in advance would overstate it as 19.26.
    conditional,usable=weekly_value(sample,{2:1},.5,0.)
    assert usable==pytest.approx(.75) and conditional==pytest.approx(125/9/.75)

@pytest.mark.parametrize('pmf', [{3:float('nan')},{3:float('inf')},{-1:1},{3:-1},{}])
def test_invalid_schedule_mass_is_rejected(pmf):
    with pytest.raises(ValueError):played_games_pmf(pmf,.8)


def test_historical_calendar_has_no_offseason_and_playoffs_use_global_anchor():
    from src.schedule.calendar import latest_historical_schedule, counts_to_pmfs
    logs=pd.DataFrame({'season':['2024-25','2025-26','2025-26'],
        'game_date':['2024-10-21','2025-10-20','2025-11-03'],'team':['A','A','B']})
    latest=latest_historical_schedule(logs)
    counts=weekly_counts(latest)
    assert len(counts)==3
    assert list(counts.B)==[0,0,1]
    assert playoff_week_pmf(latest,'B',3)=={1:1}
    assert counts_to_pmfs(counts.iloc[:2])['B']=={0:1}
    assert counts_to_pmfs(counts.iloc[2:])['B']=={1:1}
    assert playoff_week_pmf(latest,'B',5)=={}


def test_replaceability_and_positional_market_gaps_are_roster_effects():
    from src.league.replacements import replaceability, positional_market_gaps
    cfg=load_config().league
    roster=[RosterPlayer(str(i),str(i),'PG',10,.8) for i in range(14)]
    pool=[RosterPlayer('c','Centre','C',20,.9)]
    values=replaceability(roster,pool,cfg)
    assert len(values)==14 and values['1']['replacement']=='c'
    gaps=positional_market_gaps(roster,pool,cfg)
    assert gaps['C']['delta_weekly']>0 and gaps['SF'] is None


def test_regular_schedule_filters_preseason_and_calendar_keeps_zero_weeks():
    schedule=pd.DataFrame({'game_id':['0012600001','0022600001','0022600002','0022600003'],
       'game_date':['2026-10-03','2026-10-20','2026-11-03','2026-11-03'],
       'home_team':['LON','A','A',None], 'away_team':['A','B','B',None]})
    cleaned,notes=regular_schedule(schedule)
    assert len(cleaned)==2 and len(notes)==2
    counts=weekly_counts(cleaned)
    assert list(counts.A)==[1,0,1]
    assert playoff_week_pmf(cleaned,'A',2)=={0:.5,1:.5}
    assert len(schedule)==4


def test_board_identity_and_full_positions_propagate():
    board=pd.DataFrame([{'player_name':'X','player_id':'123','sleeper_id':'456',
                        'positions':'PG|SF','position':'PG','lockin_value':20,
                        'conditional_lockin_value':18,'weekly_availability':.9}])
    p=board_to_roster_players(board)['x']
    assert p.player_id=='sleeper:456' and p.positions==('PG','SF')
    assert p.weekly_value==18 and p.availability==.9
    cfg=load_config().league
    roster=[RosterPlayer(str(i),str(i),'PG',30) for i in range(4)]+[p]
    assert optimal_lineup(roster,cfg).total==138
    with pytest.raises(ValueError,match='Duplicate'): optimal_lineup([p,p],cfg)


def test_cut_keeps_unique_centre_even_with_lower_raw_projection():
    cfg=load_config().league
    roster=[RosterPlayer(str(i),str(i),'PG',30,.8) for i in range(14)]
    centre=RosterPlayer('c','Centre','C',20,.8)
    kept,cuts=trim_roster(roster+[centre],cfg)
    assert centre in kept and len(cuts)==1 and cuts[0]!=centre


def test_bench_expected_marginal_and_relative_quantities():
    cfg=load_config().league
    players=[RosterPlayer(str(i),str(i),'C',30,.5) for i in range(3)]
    bench=RosterPlayer('b','Bench','C',20,1)
    p=profile(players+[bench],cfg)
    assert p['healthy_marginal']['b']==0
    assert p['expected_marginal']['b']>0
    assert p['bench_insurance']>0
    result=relative_profiles({'a':p,'b':profile(players,cfg)})
    assert result['a']['league_relative']['bench_insurance']['percentile']==100


def test_two_sided_ownership_symmetry_and_replacements():
    cfg=load_config().league
    a=[RosterPlayer(f'a{i}',f'A{i}','PG',30,.8) for i in range(14)]
    b=[RosterPlayer(f'b{i}',f'B{i}','C',25,.8) for i in range(14)]
    pool=[RosterPlayer('fa','FA','SF',20,.9)]
    r=evaluate_two_team(a,b,['a12','a13'],['b13'],cfg,free_agents=pool,stress=0)
    reverse=evaluate_two_team(b,a,['b13'],['a12','a13'],cfg,free_agents=pool,stress=0)
    assert r['team_a']['delta_weekly']==pytest.approx(reverse['team_b']['delta_weekly'])
    assert r['team_a']['pickups'] and r['team_b']['drops']
    with pytest.raises(ValueError,match='ownership'): evaluate_two_team(a,a,['a1'],['a2'],cfg)
    with pytest.raises(ValueError): evaluate_two_team(a,b,['missing'],[],cfg)


def test_classification_requires_robust_benefit_on_both_sides():
    pos={'pessimistic':1,'optimistic':2}
    neg={'pessimistic':-2,'optimistic':-1}
    assert classify(pos,pos)=='WIN-WIN'
    assert classify(pos,neg)=='FAVOURS TEAM A'
    assert classify(neg,pos)=='FAVOURS TEAM B'
    assert classify(neg,neg)=='LOSE-LOSE'
    assert classify(pos,{'pessimistic':-.1,'optimistic':1})=='TOO CLOSE TO CALL'


def test_partner_scores_are_measurable_and_symmetric():
    cfg=load_config().league
    a=[RosterPlayer(f'a{i}',f'A{i}','PG',30,.8) for i in range(6)]
    b=[RosterPlayer(f'b{i}',f'B{i}','C',30,.8) for i in range(6)]
    r=partners('a',{'a':a,'b':b},cfg)[0]
    rr=partners('b',{'a':a,'b':b},cfg)[0]
    assert r['compatibility_fp_week']>0
    assert r['compatibility_fp_week']==rr['compatibility_fp_week']


@pytest.mark.skipif(shutil.which('node') is None,reason='Node required for duplication guard')
def test_entire_trade_javascript_equivalence(tmp_path):
    import random
    rng=random.Random(81)
    cfg=load_config().league
    cases=[]
    for _ in range(20):
        ps=[RosterPlayer(str(i),str(i),rng.choice(['PG','C','SF']),round(rng.uniform(1,50),2),
                        round(rng.uniform(.5,1),2),positions=('PG','SF') if i%4==0 else ()) for i in range(17)]
        roster,get=ps[:14],ps[14:]
        result=evaluate_trade(roster,['0'],get,cfg,22)
        def payload(p):
            return dict(id=p.player_id,name=p.name,position=p.position,value=p.weekly_value,
                        availability=p.availability,positions=p.positions)
        cases.append({'roster':[payload(p) for p in roster], 'get':[payload(p) for p in get],
                      'expected':[result.delta_weekly,result.delta_starters,result.delta_depth,
                                  result.pessimistic_weekly,result.optimistic_weekly], 'drops':list(result.dropped)})
    from src.trade import expand_slots, eligibility_map
    js_cfg={'slots':expand_slots(cfg),'slotNames':list(cfg.starting_slots),
            'elig':{k:list(v) for k,v in eligibility_map(cfg).items()},'rosterLimit':14,'stress':.1,'weeksRemaining':22}
    script=tmp_path/'test.js'
    script.write_text(LINEUP_JS+'\nconst data='+json.dumps({'cases':cases,'cfg':js_cfg})+''';
console.log(JSON.stringify(data.cases.map(c=>{let r=evaluateTrade(c.roster,['0'],c.get,data.cfg);
return {values:[r.deltaWeekly,r.deltaStarters,r.deltaDepth,r.pessimistic,r.optimistic],drops:r.dropped}})));
''',encoding='utf-8')
    output=json.loads(subprocess.check_output([shutil.which('node'),str(script)],text=True))
    for case,actual in zip(cases,output):
        assert actual['values']==pytest.approx(case['expected'],abs=1e-6)
        assert actual['drops']==case['drops']


def test_replacement_claims_do_not_credit_a_free_standalone_move():
    from src.config import load_config
    from src.league.trades import evaluate_two_team
    from src.trade import RosterPlayer
    cfg=load_config().league;everywhere=('PG','SG','SF','PF','C')
    def team(prefix):
        return ([RosterPlayer(f'{prefix}s{i}',f'{prefix}s{i}','PG',30.,1.,positions=everywhere) for i in range(9)]+
                [RosterPlayer(f'{prefix}b{i}',f'{prefix}b{i}','PG',5.,1.,positions=everywhere) for i in range(5)])
    a,b=team('a'),team('b');pool=[RosterPlayer('fa','FA','PG',40.,1.,positions=everywhere)]
    # Two bench players for one equal bench player: the only "gain" is the opened slot's claim.
    result=evaluate_two_team(a,b,['ab0','ab1'],['bb0'],cfg,weeks=10,stress=.1,free_agents=pool,include_replacements=True)
    side=result['team_a']
    assert side['pickups']==['fa'] and side['baseline_free_moves'][0]['add']=='fa'
    assert abs(side['delta_weekly'])<1e-9 and abs(side['pessimistic'])<1e-9
    plain=evaluate_two_team(a,b,['ab0','ab1'],['bb0'],cfg,weeks=10,stress=.1,free_agents=pool,include_replacements=False)
    assert abs(plain['team_a']['delta_weekly'])<1e-9


def test_schedule_reports_regular_season_games_missing_outright():
    from src.schedule.calendar import regular_schedule
    numbers=[n for n in range(1,1231) if not 1205<=n<=1228]
    schedule=pd.DataFrame({'game_id':[f'00226{n:05d}' for n in numbers],'game_date':'2026-11-01',
                           'home_team':'BOS','away_team':'NYK'})
    _frame,notes=regular_schedule(schedule)
    assert any(note.startswith('24 of 1230 regular-season NBA games are not in the schedule file') and
               'schedule incomplete' in note for note in notes)
