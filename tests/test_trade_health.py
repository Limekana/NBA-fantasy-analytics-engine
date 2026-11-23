"""Joint absence, injury gating and interpretable positional trade changes."""
from dataclasses import replace
import json

import pytest

from src.config import config_with_overrides, load_config
from src.trade import roster_strength, _scale
from src.league.trades import evaluate_two_team
from src.league.needs import trade_needs
from test_positional_needs import p, payload, cfgjs, run_js
from test_surplus_trades import fixture
from test_league_integration import league_fixture


def one_slot():
    return config_with_overrides({'league': {'roster': {'PG':1,'SG':0,'G':0,'SF':0,'PF':0,'F':0,'C':0,'UTIL':0,'BN':2,'IR':0}}}).league


def test_backup_must_be_available_when_starter_is_out():
    cfg=one_slot()
    star=p('star',['PG'],40,.5)
    backup=p('backup',['PG'],20,.5)
    # 50% star scores 40; otherwise 50% backup scores 20; both out scores zero.
    assert roster_strength([star,backup],cfg).expected_weekly==pytest.approx(25)
    assert roster_strength([star,replace(backup,availability=0)],cfg).expected_weekly==pytest.approx(20)
    assert roster_strength([star,replace(backup,availability=1)],cfg).expected_weekly==pytest.approx(30)


def test_two_starters_cannot_share_the_same_backup_in_joint_absence():
    cfg=config_with_overrides({'league': {'roster': {'PG':1,'SG':1,'G':0,'SF':0,'PF':0,'F':0,'C':0,'UTIL':0,'BN':1,'IR':0}}}).league
    roster=[p('pg',['PG'],40,.5),p('sg',['SG'],40,.5),p('backup',['PG','SG'],30,1)]
    assert roster_strength(roster,cfg).expected_weekly==pytest.approx(.25*80+.5*70+.25*30)


def test_exact_small_expectation_against_independent_subset_oracle():
    from itertools import product
    from src.trade import optimal_lineup
    cfg=one_slot()
    roster=[p(str(i),pos,20+i,.3+.1*i) for i,pos in enumerate([['PG'],['SG'],['SF','PF'],['C'],['PG','SG']])]
    expected=0
    for present in product([False,True],repeat=len(roster)):
        weight=1
        for q,on in zip(roster,present):weight*=q.availability if on else 1-q.availability
        expected+=weight*optimal_lineup([q for q,on in zip(roster,present) if on],cfg).total
    assert roster_strength(roster,cfg).expected_weekly==pytest.approx(expected)


def test_large_joint_model_is_order_invariant_and_zero_backup_is_worthless():
    cfg=load_config().league
    roster=[p('joint:'+str(i),[['PG'],['SG'],['SF','PF'],['C']][i%4],20+i,.7) for i in range(14)]
    expected=roster_strength(roster,cfg).expected_weekly
    assert roster_strength(list(reversed(roster)),cfg).expected_weekly==expected
    assert roster_strength(roster+[p('out',['PG'],100,0)],cfg).expected_weekly==expected
    result=run_js('console.log(JSON.stringify(rosterStrength('+json.dumps(payload(roster))+','+json.dumps(cfgjs(cfg))+').expected));')
    assert result==pytest.approx(expected)


def test_no_integration_mode_jump_when_cut_crosses_eight_uncertain_players():
    cfg=load_config().league
    roster=[p('cross:'+str(i),['PG','C'],20+i,.8) for i in range(8)]
    before=roster_strength(roster,cfg).expected_weekly
    after=roster_strength(roster+[p('tiny',['PG'],.000001,.8)],cfg).expected_weekly
    assert before<=after<=before+.000001


@pytest.mark.parametrize('side',['a','b'])
def test_flagged_assets_never_enter_automatic_singles_or_packages(side):
    cfg,rs,refs=fixture()
    rs[side][-1]=replace(rs[side][-1],injury_status='Out')
    data={t:[{**q,'injury_status':p.injury_status} for q,p in zip(payload(r),r)] for t,r in rs.items()}
    result=run_js('const rs='+json.dumps(data)+';const cfg='+json.dumps(cfgjs(cfg))+';'+'''
    (async()=>{console.log(JSON.stringify(await findLeagueTrades(rs.a,[{id:'b',roster:rs.b}],[],cfg,{packages:true,robust:false,replacements:false})));})();
    ''')
    flagged=rs[side][-1].player_id
    assert flagged in result['excludedInjuries']
    assert all(flagged not in row['give']+row['get'] for row in result['results'])


def test_manual_injury_warning_and_unavailable_scenario_python_browser_parity():
    cfg,rs,_=fixture()
    rs['a'][-1]=replace(rs['a'][-1],injury_status='Out')
    a,b=rs['a'],rs['b']
    py=evaluate_two_team(a,b,[a[-1].player_id],[b[-1].player_id],cfg,include_replacements=False)
    assert py['classification']=='INJURY REVIEW REQUIRED'
    assert _scale(a[-1],.9).injury_status=='Out'
    data={t:[{**q,'injury_status':p.injury_status} for q,p in zip(payload(r),r)] for t,r in rs.items()}
    js=run_js('const rs='+json.dumps(data)+';console.log(JSON.stringify(evaluateLeagueTrade(rs.a,rs.b,[rs.a[2].id],[rs.b[2].id],[],'+json.dumps(cfgjs(cfg))+',false)));')
    assert js['classification']==py['classification']
    for side in ['team_a','team_b']:
        assert js[side]['injury_risks']==py[side]['injury_risks']
        for key in ['delta_weekly','health_delta','health_pessimistic']:
            assert js[side][key]==pytest.approx(py[side][key])
    assert py['team_b']['health_delta']<py['team_b']['delta_weekly']


def test_health_sensitivity_catches_injured_cover_even_when_not_traded():
    cfg,rs,_=fixture()
    rs['b'][1]=replace(rs['b'][1],injury_status='Out')
    result=evaluate_two_team(rs['a'],rs['b'],['extra-wing'],['extra-pg'],cfg,include_replacements=False)
    assert result['team_b']['injury_risks']=={'their-wing':'Out'}
    assert not result['team_b']['traded_injury_risk']
    assert result['team_b']['health_delta']!=result['team_b']['delta_weekly']


def test_positional_labels_have_direct_depth_and_separate_whole_lineup_gain():
    cfg,rs,refs=fixture()
    before=rs['a'];after=before[:2]+[rs['b'][-1]]
    rows=trade_needs(before,after,cfg,refs['a']['references'])
    pg=next(r for r in rows if r['position']=='PG')
    assert (pg['eligible_before'],pg['eligible_after'])==(1,2)
    assert pg['leader_before']==pg['leader_after']=='star'
    assert pg['production_change']==0
    assert pg['lineup_gain_without_original_leader']>0
    assert pg['lineup_gain_without_original_leader']-pg['healthy_lineup_gain']==pytest.approx(-pg['outage_loss_change'])


def test_reviewed_full_absence_override_is_not_raised_to_two_percent():
    from src.projections.games_played import project_games_played
    result=project_games_played('out',30,[.9],load_config().model,{'availability_multiplier':0,'note':'Test assumption'})
    assert result.availability_probability==result.expected_games==result.games_ceiling==0
    assert 'Test assumption' in result.assumption_notes


@pytest.mark.parametrize('bad',[{'availability_multiplier':float('nan')},{'availability_multiplier':-1},{'games_missed':-2}])
def test_invalid_injury_assumptions_fail_closed(bad):
    from src.projections.games_played import project_games_played
    with pytest.raises(ValueError,match='Injury'):
        project_games_played('out',30,[.9],load_config().model,bad)


def test_identity_injury_flags_survive_both_horizon_payloads(league_fixture):
    import hashlib
    import pandas as pd
    from src.league.storage import import_bundle
    from src.league.service import snapshot, forecast_paths, config_digest
    cfg,bundle=league_fixture
    bundle['players']['1']['injury_status']='Out'
    import_bundle(cfg,bundle)
    values,meta=forecast_paths(cfg);values.parent.mkdir()
    pd.DataFrame([{'player_name':f'Player {i}','sleeper_id':str(i),'player_id':str(i),
        'conditional_lockin_value':20.,'weekly_availability':.9,
        'playoff_conditional_lockin_value':21.,'playoff_weekly_availability':.92,
        'playoff_used_real_schedule':True,'historical_games':40,'playoff_weeks':3,'team':'BOS'}
        for i in range(1,6)]).to_csv(values,index=False)
    meta.write_text(json.dumps({'config_sha256':config_digest(cfg),'generated_at':'2026-09-06',
        'values_sha256':hashlib.sha256(values.read_bytes()).hexdigest()}))
    data=snapshot(cfg)
    for horizon in ['players','playoff_players']:
        assert data[horizon]['sleeper:1']['injury_status']=='Out'
        assert data[horizon]['sleeper:1']['health_risk'] is True
        assert data[horizon]['sleeper:2']['injury_status']==''
    # Don't fabricate a season-long absence from a snapshot flag.
    assert data['players']['sleeper:1']['availability']==.9


def test_exact_large_roster_matches_closed_form():
    from src.trade import ExactAvailabilityConfig
    cfg=config_with_overrides({'league': {'roster': {'PG':1,'SG':0,'G':0,'SF':0,'PF':0,'F':0,'C':0,'UTIL':0,'BN':13,'IR':0}}}).league
    roster=[p('asset:'+str(i),['PG'],20,.3) for i in range(10)]
    expected=20*(1-.7**10)
    assert roster_strength(roster,ExactAvailabilityConfig(cfg)).expected_weekly==pytest.approx(expected)
    js=run_js('console.log(JSON.stringify(rosterStrength('+json.dumps(payload(roster))+','+json.dumps({**cfgjs(cfg),'exactAvailability':True})+').expected));')
    assert js==pytest.approx(expected)


def test_automatic_offers_must_pass_exact_recheck_not_only_screening():
    cfg,rs,_=fixture()
    data={t:payload(r) for t,r in rs.items()}
    result=run_js('const rs='+json.dumps(data)+';const cfg='+json.dumps(cfgjs(cfg))+';'+'''
    const original=evaluateLeagueTrade;
    evaluateLeagueTrade=(...args)=>{
      const result=original(...args);
      if(args[7]===true)result.team_b.delta_weekly=-1;
      return result;
    };
    (async()=>console.log(JSON.stringify(await findLeagueTrades(rs.a,[{id:'b',roster:rs.b}],[],cfg,{packages:false,replacements:false}))))();
    ''')
    assert result['exactChecked']>0
    assert result['results']==[]
