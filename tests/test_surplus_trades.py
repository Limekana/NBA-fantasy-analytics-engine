"""Regression: fill a gap without selling the star or inventing a waiver profit."""
import json

from test_positional_needs import p, small_cfg, cfgjs, payload, run_js
from src.config import config_with_overrides
from src.league.needs import trade_needs, league_needs


def test_downgrading_a_star_is_not_credited_as_better_cover():
    cfg=small_cfg()
    before=[p('star',['PG'],50),p('backup',['PG'],20),p('center',['C'],30)]
    after=[p('lesser',['PG'],40),before[1],before[2]]
    refs={pos:{'qualities':[30,30],'median_quality':30,'median_outage_loss':20} for pos in ['PG','C']}
    change=trade_needs(before,after,cfg,refs)
    pg=next(r for r in change if r['position']=='PG')
    assert pg['improvement']==10
    assert pg['search_improvement']==0
    assert pg['leader_changed']
    output=run_js('console.log(JSON.stringify(tradeNeeds('+json.dumps(payload(before))+','+json.dumps(payload(after))+','+json.dumps(cfgjs(cfg))+','+json.dumps(refs)+')));')
    assert output==change


def fixture():
    cfg=config_with_overrides({'league': {'roster': {'PG':1,'SG':0,'G':0,'SF':1,'PF':0,'F':0,'C':0,'UTIL':0,'BN':1,'IR':0}}}).league
    rosters={'a':[p('star',['PG'],40,.6),p('wing-starter',['SF'],30,.8),p('extra-wing',['SF'],22,.8)],
             'b':[p('their-pg',['PG'],35,.95),p('their-wing',['SF'],18,.6),p('extra-pg',['PG'],26,.8)]}
    refs=league_needs(rosters,cfg,2)
    return cfg,rosters,refs


def test_surplus_wing_for_pg_preserves_leader_and_profit_is_standalone():
    cfg,rosters,refs=fixture()
    result=run_js('const cfg='+json.dumps(cfgjs(cfg))+';const rs='+json.dumps({t:payload(r) for t,r in rosters.items()})+';const n='+json.dumps(refs)+';'+'''
(async()=>{
 const report=await findLeagueTrades(rs.a,[{id:'b',roster:rs.b,needReferences:n.b.references}],[],cfg,{searchMode:'surplus',offerFrom:'wings',needFocus:['PG'],needReferences:n.a.references,replacements:false,packages:true});
 console.log(JSON.stringify(report));
})();
''')
    assert result['results']
    for trade in result['results']:
        assert 'star' not in trade['give']
        assert 'wing-starter' not in trade['give']
        assert trade['give']==['extra-wing']
        assert trade['depth_fit']['count_after']>trade['depth_fit']['count_before']
        assert trade['depth_fit']['backup_gain']>0
        for side in ['team_a','team_b']:
            assert not trade['result'][side]['pickups']
            assert trade['result'][side]['delta_weekly']>0


def test_surplus_guards_reject_fake_depth_and_wrong_surplus():
    cfg,rosters,refs=fixture()
    data={'mine':payload(rosters['a']),'other':payload(rosters['b'])}
    result=run_js('const d='+json.dumps(data)+';const cfg='+json.dumps(cfgjs(cfg))+';'+'''
const star=d.mine[0],wing=d.mine[1],extra=d.mine[2],guard=d.other[2];
const good=[star,wing,guard];
const swapStar=[guard,wing,extra];
const equalWings=d.other.concat({...extra,id:'another-wing'});
console.log(JSON.stringify([
 surplusDepthFit(d.mine,good,d.other,['extra-wing'],['extra-pg'],'PG',cfg),
 surplusDepthFit(d.mine,swapStar,d.other,['star'],['extra-pg'],'PG',cfg),
 surplusDepthFit(d.mine,good,equalWings,['extra-wing'],['extra-pg'],'PG',cfg),
 surplusDepthFit(d.mine,good,d.other,['extra-wing'],[],'PG',cfg)
]));
''')
    assert result[0]
    assert result[1:]==[None,None,None]


def test_no_waiver_mode_cannot_use_free_agent_to_rescue_package():
    cfg,rosters,refs=fixture()
    result=run_js('const cfg='+json.dumps(cfgjs(cfg))+';const rs='+json.dumps({t:payload(r) for t,r in rosters.items()})+';'+'''
(async()=>{
const free={id:'free',name:'free',position:'PG',positions:['PG'],value:100,availability:1,isPick:false,slots:[]};
const out=await findLeagueTrades(rs.a,[{id:'b',roster:rs.b}],[free],cfg,{replacements:false,packages:true});
console.log(JSON.stringify(out));
})();
''')
    for item in result['results']:
        assert not item['result']['team_a']['pickups']
        assert not item['result']['team_b']['pickups']
