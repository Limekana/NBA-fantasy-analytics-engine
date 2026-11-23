import json
from dataclasses import replace
import pandas as pd
import pytest

from src.config import config_with_overrides
from src.league.identity import PlayerCatalog, PlayerIdentity
from src.league.models import Team, Transfer, LeagueEvent
from src.league.state import replay, apply_event, reconcile
from src.league.events import append_events, read_events
from src.league.sleeper_import import draft_events, transaction_events, teams_from_sleeper


@pytest.fixture
def small():
    cfg = config_with_overrides({'league': {'league': {'teams': 2},
        'roster': {'PG': 1, 'SG': 0, 'G': 0, 'SF': 0, 'PF': 0, 'F': 0, 'C': 0, 'UTIL': 0, 'BN': 1, 'IR': 1},
        'draft': {'rounds': 2}}}).league
    catalog = PlayerCatalog([PlayerIdentity('sleeper:'+str(i), n) for i,n in enumerate(
        ['Davion Mitchell', 'Donovan Mitchell', 'Ajay Mitchell', 'Someone Else', 'New Player'],1)])
    teams = [Team('7', 'A', 1), Team('9', 'B', 2)]
    events = [LeagueEvent('d'+str(i), i, 'draft', (Transfer('sleeper:'+str(i), None, t),))
              for i,t in enumerate(['7','9','9','7'],1)]
    return cfg,catalog,teams,events


def test_snake_reconstruction_uses_roster_mapping(small):
    cfg,cat,teams,_=small
    draft={'draft_id':'d','type':'snake','status':'complete'}
    picks=[{'pick_no':i,'draft_slot':s,'player_id':str(i)} for i,s in enumerate([1,2,2,1],1)]
    state=replay('l',teams,draft_events(picks,draft,teams,cat,2),cat,cfg)
    assert state.roster('7')==['sleeper:1','sleeper:4']
    assert state.roster('9')==['sleeper:2','sleeper:3']
    with pytest.raises(ValueError,match='Incomplete'):
        draft_events(picks[:-1],draft,teams,cat,2)


def test_davion_donovan_and_collision_resolution(small):
    _,cat,_,_=small
    assert cat.resolve('Davion Mitchell').player_id=='sleeper:1'
    assert cat.resolve('Donovan Mitchell').player_id=='sleeper:2'
    with pytest.raises(ValueError): cat.resolve('Mitchell')
    colliding=PlayerCatalog([PlayerIdentity('a','Chris Smith'),PlayerIdentity('b','Chris Smith')])
    with pytest.raises(ValueError,match='ambiguous'): colliding.resolve('Chris Smith')
    assert colliding.resolve('a').player_id=='a'


def test_add_drop_is_atomic_and_ownership_is_exclusive(small):
    cfg,cat,teams,events=small
    state=replay('l',teams,events,cat,cfg)
    event=LeagueEvent('swap',5,'add_drop',(Transfer('sleeper:1','7',None),Transfer('sleeper:5',None,'7')))
    after=apply_event(state,event,cat,cfg)
    assert state.roster('7')==['sleeper:1','sleeper:4']
    assert after.roster('7')==['sleeper:4','sleeper:5']
    assert after.free_agents(cat)==['sleeper:1']
    invalid=replace(event,transfers=(Transfer('sleeper:1','7',None),Transfer('sleeper:2',None,'7')))
    with pytest.raises(ValueError,match='Ownership'): apply_event(state,invalid,cat,cfg)
    assert 'sleeper:1' in state.ownership


def test_ordered_add_drop_and_duplicate_log_rejected(small):
    cfg,cat,teams,events=small
    events += [LeagueEvent('drop',5,'drop',(Transfer('sleeper:1','7',None),)),
               LeagueEvent('add',6,'add',(Transfer('sleeper:1',None,'9'),))]
    state=replay('l',teams,list(reversed(events)),cat,cfg)
    assert state.ownership['sleeper:1']=='9'
    with pytest.raises(ValueError,match='Duplicate'): replay('l',teams,events+events[:1],cat,cfg)
    with pytest.raises(ValueError,match='increasing'): apply_event(state,events[0],cat,cfg)


def test_reserve_reconciliation_and_capacity(small):
    cfg,cat,teams,events=small
    events += [LeagueEvent('add',5,'add',(Transfer('sleeper:5',None,'7'),))]
    state=replay('l',teams,events,cat,cfg)
    assert not state.reserve_known
    rosters=[{'roster_id':7,'players':['1','4','5'],'reserve':['5']},
             {'roster_id':9,'players':['2','3'],'reserve':[]}]
    after=reconcile(state,rosters,cat,cfg)
    assert after.verified and after.roster('7',True)==['sleeper:1','sleeper:4']
    rosters[0]['reserve']=[]
    with pytest.raises(ValueError,match='capacity'): reconcile(state,rosters,cat,cfg)
    rosters[0]['players']=['1','4']
    with pytest.raises(ValueError,match='does not reconcile'): reconcile(state,rosters,cat,cfg)


def test_append_only_idempotent_conflict_and_failed_validation(tmp_path,small):
    cfg,cat,teams,events=small
    path=tmp_path/'events.jsonl'
    assert append_events(path,events)==4
    original=path.read_bytes()
    assert append_events(path,events)==0
    with pytest.raises(ValueError,match='Conflicting'): append_events(path,[replace(events[0],source='different')])
    with pytest.raises(ValueError,match='Ownership'):
        append_events(path,[LeagueEvent('bad',5,'add',(Transfer('sleeper:1',None,'9'),))],
                      lambda all_events: replay('l',teams,all_events,cat,cfg))
    assert path.read_bytes()==original
    assert len(read_events(path))==4


def test_completed_transactions_sorted_deduped_and_trade_atomic():
    rows=[{'transaction_id':'2','status':'complete','status_updated':2000,'type':'trade',
           'adds':{'1':9,'2':7},'drops':{'1':7,'2':9}},
          {'transaction_id':'1','status':'complete','created':1000,'type':'free_agent','adds':{'3':9},'drops':None},
          {'transaction_id':'3','status':'pending','type':'free_agent'}]
    events=transaction_events(rows+rows[:1],4)
    assert [e.source_id for e in events]==['1','2']
    assert events[1].transfers==(Transfer('sleeper:1','7','9'),Transfer('sleeper:2','9','7'))
    assert events[0].occurred_at.startswith('1970-01-01T00:00:01')


def test_team_aliases_checked_against_draft_order():
    users=[{'user_id':'u','display_name':'lukamagic6'}]
    rosters=[{'owner_id':'u','roster_id':9}]
    draft={'slot_to_roster_id':{'1':9}}
    teams=teams_from_sleeper(users,rosters,draft,{'Mymmeli':['lukamagic6']},['Mymmeli'])
    assert teams[0].manager=='Mymmeli' and teams[0].team_id=='9'
    with pytest.raises(ValueError,match='disagrees'):
        teams_from_sleeper(users,rosters,draft,{'Mymmeli':['lukamagic6']},['Emil'])
