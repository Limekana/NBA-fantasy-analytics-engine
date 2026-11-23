import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.config import config_with_overrides
from src.valuation import rescale_distribution
from src.league.lock_advisor import continuation, matchup_advice
from src.schedule.context import fit_game_context
from test_league_tools import ELIGIBLE, js
from test_positional_needs import p, cfgjs, payload, run_js
from src.league.needs import league_needs


@pytest.mark.parametrize('sample,target', [([-5,5,15],10), ([-5,5],4), ([-10,-2],-3), ([2,4],-2), ([2,4],0)])
def test_rescale_preserves_target_mean_order_and_variance(sample,target):
    original=np.array(sample,dtype=float)
    actual=rescale_distribution(original,target)
    assert actual.mean()==pytest.approx(target)
    assert np.all(np.diff(actual)>0)
    assert original.tolist()==sample
    if target>0 and np.mean(sample)>0:
        assert actual[0]<0


def test_negative_final_game_has_no_free_zero_option():
    assert continuation([-10,-2],1)==-6
    assert continuation([-10,-2],2)==-4
    assert continuation([-10,-2],1,.5)==-3
    assert js('console.log(JSON.stringify(weeklyScoreMoments([-10,-2],1,.5)));')==dict(mean=-3,variance=17)


def test_sequence_order_and_head_to_head_python_js_parity():
    sample=[-4,5,30]
    for factors in ([.9,1.1],[1.1,.9],[1,1]):
        expected=matchup_advice(sample,12,2,-10,5,.8,**ELIGIBLE,factors=factors)
        actual=js('console.log(JSON.stringify(matchupLockAdvice('+json.dumps(sample)+',12,2,-10,5,.8,'+json.dumps({**ELIGIBLE,'factors':factors})+')));')
        assert actual['action']==expected['action']
        assert actual['threshold']==pytest.approx(expected['threshold'])
        assert actual['wait_win_equivalent']==pytest.approx(expected['wait_win_equivalent'],abs=2e-7)
    assert continuation(sample,2,1,[.9,1.1])!=continuation(sample,2,1,[1.1,.9])


@pytest.mark.parametrize('factors', [[1], [1, float('nan')], [.4,1], [1,1.6]])
def test_sequence_rejects_invalid_factors(factors):
    with pytest.raises(ValueError):continuation([1],2,factors=factors)


def fit_fixture():
    cfg=config_with_overrides({'league':{'roster':{'PG':1,'SG':0,'G':0,'SF':1,'PF':0,'F':0,'C':0,'UTIL':0,'BN':1,'IR':0}}}).league
    rosters={'a':[p('star',['PG'],60,.6),p('wing-leader',['SF'],30),p('extra-wing',['SF'],29)],
             'b':[p('their-pg',['PG'],35,.95),p('their-wing',['SF'],29),p('extra-pg',['PG'],28)]}
    refs=league_needs(rosters,cfg,2)
    return cfg,rosters,refs


def fit_search(**options):
    cfg,rs,refs=fit_fixture()
    opts={'searchMode':'fit','offerFrom':'wings','needFocus':['PG'],'needReferences':refs['a']['references'],
          'replacements':False,'packages':True,'robust':False,'minGain':.25,**options}
    return run_js('const cfg='+json.dumps(cfgjs(cfg))+';const rs='+json.dumps({t:payload(r) for t,r in rs.items()})+';'+
        '(async()=>{console.log(JSON.stringify(await findLeagueTrades(rs.a,[{id:"b",roster:rs.b}],[],cfg,'+json.dumps(opts)+')));})();')


def test_fit_overpay_can_improve_my_team_without_claiming_mutual_gain():
    result=fit_search()
    trade=next(x for x in result['results'] if x['give']==['wing-leader'] and x['get']==['extra-pg'])
    assert trade['fit_price']['overpay']==pytest.approx(30/28-1)
    assert trade['result']['team_a']['delta_weekly']>0
    assert -1<trade['result']['team_b']['delta_weekly']<0
    assert trade['depth_fit']['backup_gain']>0
    assert not trade['result']['team_a']['pickups']
    assert all('star' not in x['give'] for x in result['results'])


def test_default_remains_strict_and_fit_limits_are_enforced():
    for opts in ({'searchMode':'surplus'}, {'maxOverpay':0}, {'maxOtherLoss':0}, {'robust':True}):
        assert not fit_search(**opts)['results']


def test_centre_eligible_big_is_not_offered_as_a_surplus_wing():
    cfg,rs,_=fit_fixture()
    rs['a'][1]=p('big',['C','PF'],30)
    rs['a'][2]=p('wing',['SF'],29)
    after=[rs['a'][0],rs['a'][2],rs['b'][2]]
    code='console.log(JSON.stringify(surplusDepthFit('+','.join(json.dumps(x) for x in [payload(rs['a']),payload(after),payload(rs['b']),['big'],['extra-pg'],'PG',cfgjs(cfg),'wings',True])+')));'
    assert run_js(code) is None


def test_game_context_insufficient_history_is_neutral():
    assert not fit_game_context(pd.DataFrame())['enabled']


def history(effect=True):
    rows=[]
    for d in range(100):
        for player in range(40):
            opponent='A' if (d+player)%2 else 'B'
            fp=30*(1+(.15 if opponent=='A' else -.15)) if effect else 30
            rows.append(dict(player_id=str(player),team='T',opponent=opponent,home=bool(d%2),
                             game_date=pd.Timestamp('2025-01-01')+pd.Timedelta(days=d*2),minutes=30,
                             fantasy_points=fp,season='2024-25'))
    return pd.DataFrame(rows)


def test_context_holdout_accepts_signal_and_rejects_unnecessary_adjustment():
    model=fit_game_context(history())
    assert model['enabled']
    assert model['adjusted_mse']<model['baseline_mse']
    assert model['opponents']['A']>model['opponents']['B']
    assert not fit_game_context(history(False))['enabled']


def test_context_cap_missing_opponent_and_back_to_back():
    actual=js('''
const game={date:'2026-10-02',home:'T',away:'A'},all=[{date:'2026-10-01',home:'T',away:'B'},game];
const model={enabled:true,opponents:{A:.5},home:.2,back_to_back:-.03};
console.log(JSON.stringify([gameContextFactor(game,'T',all,model),gameContextFactor(game,'T',all,model,false),gameContextFactor({...game,away:'Z'},'T',all,model)]));
''')
    assert actual[0]['factor']==1.1 and actual[0]['backToBack']
    assert actual[1]['factor']==actual[2]['factor']==1


def test_missed_season_after_debut_counts_as_zero_availability():
    from src.pipeline import _availability_history
    logs = pd.DataFrame({'player_id': ['vet'] * 3 + ['rookie'] * 2,
                         'season': ['2023-24', '2023-24', '2025-26', '2025-26', '2025-26']})
    history = _availability_history(logs, ['2023-24', '2024-25', '2025-26'], 82)
    # Most recent first; the injury-lost 2024-25 season is a zero, not skipped.
    assert history['vet'] == [1 / 82, 0.0, 2 / 82]
    # Seasons before a player's debut are not missed seasons.
    assert history['rookie'] == [2 / 82]


def test_context_opponent_effects_do_not_absorb_the_average_residual():
    # Short games are less efficient, so the unweighted mean residual is negative;
    # Lock-In samples already carry the projected mean, so opponents must average ~0.
    frame=history()
    short=(frame.index//40)%2==0  # every other day, within each player
    frame.loc[short,'minutes']=15
    frame.loc[short,'fantasy_points']*=.4
    model=fit_game_context(frame)
    assert model['enabled'] and model['opponents']['A']>model['opponents']['B']
    assert abs(np.mean(list(model['opponents'].values())))<.005


def test_in_season_blend_moves_minutes_fast_and_rates_slowly():
    from src.projections.baseline import PlayerProjection, RATE_STATS
    from src.projections.in_season import blend_in_season
    prior={stat:0. for stat in RATE_STATS};prior['points']=20.  # 20 pts in 30 min
    proj=lambda pid:PlayerProjection(pid,pid,'BOS','PG',30.,dict(prior),27.,.25)
    # Four games at 36 minutes and 36 points: minutes weight 4/(4+4)=.5,
    # rate weight 144/(144+360)=2/7 on 1.0 pts/min against the prior .667.
    games=pd.DataFrame([{'player_id':'a','season':'2026-27','game_date':'2026-10-2'+str(i),'minutes':36.,
                         **{stat:0. for stat in RATE_STATS},'points':36.} for i in range(4)])
    blended={p.player_id:p for p in blend_in_season([proj('a'),proj('b')],games,{'in_season':{'minutes_games_k':4,'rate_games_k':10}})}
    assert blended['a'].projected_minutes==pytest.approx(33.)
    rate=(5/7)*(20/30)+(2/7)*1.
    assert blended['a'].projected_stats['points']==pytest.approx(rate*33.)
    assert 'In-season update: 4 games' in blended['a'].assumption_notes
    assert blended['b']==proj('b')
    # Players without a preseason projection are not invented from a few games.
    assert set(blended)=={'a','b'}


def test_in_season_blend_matches_rookie_priors_by_unique_name():
    from src.projections.baseline import PlayerProjection, RATE_STATS
    from src.projections.in_season import blend_in_season
    stats={stat:0. for stat in RATE_STATS};stats['points']=10.
    rookie=PlayerProjection('rookie:caleb-wilson','Caleb Wilson','CHI','PF',26.,dict(stats),19.,.55)
    row=lambda pid,name:{'player_id':pid,'player_name':name,'season':'2026-27','game_date':'2026-10-21','minutes':34.,
                         **{stat:0. for stat in RATE_STATS},'points':20.}
    games=pd.DataFrame([row('1642000','Caleb Wilson')]*4+[row('1','Jaylin Williams'),row('2','Jaylin Williams')])
    blended=blend_in_season([rookie],games,{'in_season':{'minutes_games_k':4,'rate_games_k':10}})[0]
    assert blended.projected_minutes==pytest.approx(.5*26+.5*34)
    assert 'In-season update: 4 games' in blended.assumption_notes
    # A name shared by two NBA players is never matched.
    other=PlayerProjection('rookie:jaylin','Jaylin Williams','OKC','C',20.,dict(stats),23.,.55)
    assert blend_in_season([other],games,{'in_season':{}})[0]==other
