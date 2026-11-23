"""Source blend, actual stopping decisions, head-to-head policy and browser search."""
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from src.config import load_config
from src.league.lock_advisor import continuation, advise, matchup_advice
from src.league.matchups import validate_matchups
from src.projections.consensus import resolve_rookie
from src.projections.rookies import build_rookie_projections, rookie_availability
from src.reporting.trade_dashboard import LINEUP_JS
from src.scoring import ScoringEngine


ELIGIBLE = dict(completed=True, eligible_starter=True, latest_game=True, deadline_open=True)
ROOT = Path(__file__).resolve().parents[1]


def js(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for browser-math parity')
    source = LINEUP_JS + '\n' + (ROOT/'src/reporting/league_math.js').read_text(encoding='utf-8')
    source += '\n' + (ROOT/'src/reporting/league_advisor_math.js').read_text(encoding='utf-8')
    result = subprocess.run([node, '-e', source + '\n' + code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_yaxel_blend_is_per_field_and_preserves_provenance():
    cfg = load_config()
    entry = cfg.assumptions['rookies']['Yaxel Lendeborg']
    resolved = resolve_rookie(entry)
    stats = {k: v * resolved['minutes'] / 36 for k, v in resolved['per36'].items()}
    assert resolved['minutes'] == 25.75
    assert stats == pytest.approx(dict(points=9.935, rebounds=5.895, assists=2.325,
        steals=.955, blocks=.88, three_pointers_made=.99, turnovers=1.625,
        free_throws_made=1.4516, personal_fouls=2.5))
    assert rookie_availability(entry) == 73/82
    assert 'personal_fouls: explicit fallback 2.5' in resolved['note']
    assert 'free_throws_made: 1 source(s)' in resolved['note']
    assert 'per36' not in entry  # no mutation
    projections, profiles = build_rookie_projections({'rookies': {'Yaxel Lendeborg': entry}}, cfg.model, ScoringEngine(cfg.scoring))
    assert projections[0].projected_stats == pytest.approx(stats)
    assert len(profiles[0].fp_sample) == 400


@pytest.mark.parametrize('bad', ['duplicate', 'season', 'units', 'missing', 'nan'])
def test_blend_rejects_invalid_sources(bad):
    entry = deepcopy(load_config().assumptions['rookies']['Yaxel Lendeborg'])
    if bad == 'duplicate': entry['projection_sources'].append(entry['projection_sources'][0])
    if bad == 'season': entry['projection_sources'][0]['season'] = '2025-26'
    if bad == 'units': entry['projection_sources'][0]['units'] = 'season_total'
    if bad == 'missing': entry['fallbacks'] = {}
    if bad == 'nan': entry['projection_sources'][0]['values']['points'] = float('nan')
    with pytest.raises(ValueError): resolve_rookie(entry)


def test_stopping_is_not_the_expected_maximum_and_dnp_does_not_rewind():
    assert continuation([0, 20], 1) == 10
    assert continuation([0, 20], 2) == 15
    assert continuation([0, 20], 3) == 17.5
    assert continuation([20], 1, .5) == 10
    assert continuation([20], 2, .5) == 15
    assert advise([20], 15, 1, .5, **ELIGIBLE)['action'] == 'LOCK'
    assert advise([20], 15, 1, 1, **ELIGIBLE)['action'] == 'WAIT'
    assert advise([20], -5, 0, **ELIGIBLE)['action'] == 'KEEP FINAL GAME'


@pytest.mark.parametrize('missing', list(ELIGIBLE))
def test_never_recommends_lock_without_confirmed_eligibility(missing):
    state = {**ELIGIBLE, missing: False}
    assert advise([20], 90, 2, **state)['action'] == 'VERIFY ELIGIBILITY'
    assert matchup_advice([20], 90, 2, -10, 10, **state)['action'] == 'VERIFY ELIGIBILITY'
    assert advise([20], 90, 2, **ELIGIBLE, already_locked=True)['action'] == 'ALREADY LOCKED'


@pytest.mark.parametrize('sample,n,p', [([], 1, 1), ([math.nan], 1, 1), ([1], -1, 1), ([1], 1.5, 1), ([1], 9, 1), ([1], 2, -1)])
def test_bad_stopping_inputs_rejected(sample, n, p):
    with pytest.raises(ValueError): continuation(sample, n, p)


def test_head_to_head_changes_the_decision_when_protecting_a_win():
    sample = [0, 60]
    # 25 is below the point threshold (30), but guarantees a win over a 20 gap.
    assert advise(sample, 25, 1, **ELIGIBLE)['action'] == 'WAIT'
    safe = matchup_advice(sample, 25, 1, -20, 0, **ELIGIBLE)
    assert safe['action'] == 'LOCK'
    assert safe['lock_win_equivalent'] == 1
    assert safe['wait_win_equivalent'] == .5
    # 35 is above the point threshold, but cannot close a 40-point gap.
    assert advise(sample, 35, 1, **ELIGIBLE)['action'] == 'LOCK'
    assert matchup_advice(sample, 35, 1, -40, 0, **ELIGIBLE)['action'] == 'WAIT'
    tie = matchup_advice([20], 20, 1, -20, 0, **ELIGIBLE)
    assert tie['lock_win_equivalent'] == .5


def test_head_to_head_python_browser_parity():
    cases = [(score, n, p, margin, sd) for score in (-2, 15, 55) for n in (0, 1, 3)
             for p, margin, sd in [(1, -20, 0), (.7, -40, 15), (.3, 20, 8)]]
    output = js('const cases='+json.dumps(cases)+'; console.log(JSON.stringify(cases.map(([score,n,p,m,s])=>matchupLockAdvice([0,10,30,60],score,n,m,s,p,'+json.dumps(ELIGIBLE)+'))));')
    for (score,n,p,margin,sd), result in zip(cases, output):
        expected = matchup_advice([0,10,30,60], score, n, margin, sd, p, **ELIGIBLE)
        assert result['action'] == expected['action']
        for key in ('threshold','lock_win_equivalent','wait_win_equivalent'):
            assert result[key] == pytest.approx(expected[key], abs=2e-7) if expected[key] is not None else result[key] is None


def test_weekly_moments_have_correct_terminal_dnp_and_variance():
    output = js('console.log(JSON.stringify([weeklyScoreMoments([0,20],2),weeklyScoreMoments([20],2,.5),weeklyScoreMoments([20],0)]));')
    assert output == [dict(mean=15, variance=75), dict(mean=15, variance=75), dict(mean=0, variance=0)]


def test_matchup_pairs_and_no_guessing_with_invalid_rosters():
    assert validate_matchups([{'roster_id': 9, 'matchup_id': 3}, {'roster_id':10,'matchup_id':3}], ['9','10']) == {'3':['9','10']}
    for payload in ([], [{'roster_id':9,'matchup_id':3}], [{'roster_id':9,'matchup_id':3}]*2):
        with pytest.raises(ValueError): validate_matchups(payload, ['9','10'])


def test_automatic_search_returns_owned_mutually_improving_trades_and_manual_parity():
    out = js('''
const cfg={slots:['PG','C'],elig:{PG:['PG'],C:['C']},rosterLimit:3,stress:0.1,weeksRemaining:22};
const player=(id,pos,value)=>({id,name:id,position:pos,positions:[pos],value,availability:1,isPick:false,slots:[]});
const mine=[player('a','PG',30),player('b','PG',28),player('c','C',10)];
const other=[player('d','C',30),player('e','C',28),player('f','PG',10)];
(async()=>{
const report=await findLeagueTrades(mine,[{id:'B',roster:other}],[],cfg,{packages:true,robust:true});
for(const item of report.results){
 const exact=evaluateLeagueTrade(mine,other,item.give,item.get,[],cfg,true);
 if(JSON.stringify(exact)!==JSON.stringify(item.result))throw Error('Manual result mismatch');
 if(item.result.team_a.delta_weekly<=0||item.result.team_b.delta_weekly<0)throw Error('Harmful trade');
 if(item.give.some(id=>!mine.some(p=>p.id===id))||item.get.some(id=>!other.some(p=>p.id===id)))throw Error('Wrong owner');
}
const cancelled=await findLeagueTrades(mine,[{id:'B',roster:other}],[],cfg,{},()=>{},()=>true);
console.log(JSON.stringify({report,cancelled}));
})().catch(e=>{console.error(e);process.exitCode=1;});
''')
    assert out['report']['singles'] == 9
    assert out['report']['results']
    assert out['cancelled']['cancelled']
