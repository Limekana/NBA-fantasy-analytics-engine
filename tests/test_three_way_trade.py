"""Manual three-team trades must route assets once and value every side."""
import json

import pytest

from test_positional_needs import run_js


CFG = {'slots': ['PG', 'UTIL'], 'elig': {'PG': ['PG', 'UTIL'], 'SF': ['UTIL']},
       'rosterLimit': 2, 'stress': .1, 'weeksRemaining': 20}
ROSTERS = [
    [{'id': 'a1', 'name': 'A guard', 'positions': ['PG'], 'value': 40, 'availability': 1},
     {'id': 'a2', 'name': 'A wing', 'positions': ['SF'], 'value': 10, 'availability': 1}],
    [{'id': 'b1', 'name': 'B guard', 'positions': ['PG'], 'value': 30, 'availability': 1},
     {'id': 'b2', 'name': 'B wing', 'positions': ['SF'], 'value': 20, 'availability': 1}],
    [{'id': 'c1', 'name': 'C guard', 'positions': ['PG'], 'value': 25, 'availability': 1},
     {'id': 'c2', 'name': 'C wing', 'positions': ['SF'], 'value': 15, 'availability': 1}],
]


def evaluate(routes, rosters=ROSTERS):
    return run_js('console.log(JSON.stringify(evaluateLeagueThreeWay('
                  + json.dumps(rosters) + ',' + json.dumps(routes) + ',' + json.dumps(CFG) + ')));')


def test_three_team_cycle_reports_all_sides_and_no_claims():
    sides = evaluate([{'from': 0, 'to': 1, 'playerId': 'a2'},
                      {'from': 1, 'to': 2, 'playerId': 'b2'},
                      {'from': 2, 'to': 0, 'playerId': 'c2'}])
    assert [round(side['delta_weekly'], 4) for side in sides] == [5, -10, 5]
    assert [side['sent'] for side in sides] == [['a2'], ['b2'], ['c2']]
    assert [side['received'] for side in sides] == [['c2'], ['a2'], ['b2']]
    assert all(not side['drops'] and not side['pickups'] for side in sides)
    assert sides[1]['pessimistic'] < sides[1]['delta_weekly']


def test_extra_incoming_player_creates_visible_roster_cut():
    sides = evaluate([{'from': 0, 'to': 1, 'playerId': 'a2'},
                      {'from': 1, 'to': 0, 'playerId': 'b1'},
                      {'from': 1, 'to': 2, 'playerId': 'b2'},
                      {'from': 2, 'to': 0, 'playerId': 'c2'}])
    assert sides[0]['received'] == ['b1', 'c2']
    assert sides[0]['drops'] == ['c2']
    assert len(sides[0]['after_roster']) == CFG['rosterLimit']


@pytest.mark.parametrize('routes', [
    [],
    [{'from': 0, 'to': 1, 'playerId': 'a2'}, {'from': 1, 'to': 2, 'playerId': 'b2'},
     {'from': 1, 'to': 0, 'playerId': 'b1'}],  # team C receives but sends nothing
    [{'from': 0, 'to': 1, 'playerId': 'a2'}, {'from': 1, 'to': 2, 'playerId': 'b2'},
     {'from': 2, 'to': 0, 'playerId': 'a2'}],  # wrong owner / duplicate
    [{'from': 0, 'to': 0, 'playerId': 'a2'}, {'from': 1, 'to': 2, 'playerId': 'b2'},
     {'from': 2, 'to': 0, 'playerId': 'c2'}],  # self route
])
def test_invalid_routing_is_rejected(routes):
    body = 'try {evaluateLeagueThreeWay(' + json.dumps(ROSTERS) + ',' + json.dumps(routes) + ',' + json.dumps(CFG) + ');console.log(false)}catch(e){console.log(true)}'
    assert run_js(body)


def test_duplicate_ownership_is_rejected():
    rosters = json.loads(json.dumps(ROSTERS))
    rosters[2][0]['id'] = 'a1'
    body = 'try {evaluateLeagueThreeWay(' + json.dumps(rosters) + ',[], ' + json.dumps(CFG) + ');console.log(false)}catch(e){console.log(true)}'
    assert run_js(body)
