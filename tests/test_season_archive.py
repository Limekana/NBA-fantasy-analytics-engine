import pandas as pd

from src.league.season_archive import (
    _matchup_frames,
    _reconcile_player_weeks,
    _repair_sleeper_score_discrepancies,
)


def test_sleeper_archive_keeps_player_scores_and_reconciles_team_total():
    rows = [
        {'roster_id': 7, 'matchup_id': 3, 'points': 16.5, 'custom_points': None,
         'players': ['10', '11'], 'starters': ['10'],
         'players_points': {'10': 16.5, '11': 4.0}},
        {'roster_id': 9, 'matchup_id': 3, 'points': 8.0, 'custom_points': None,
         'players': ['12'], 'starters': ['12'], 'players_points': {'12': 8.0}},
    ]
    rosters = [{'roster_id': 7, 'owner_id': 'a'}, {'roster_id': 9, 'owner_id': 'b'}]
    users = [{'user_id': 'a', 'display_name': 'Alpha'}, {'user_id': 'b', 'display_name': 'Beta'}]
    teams, players = _matchup_frames(1, rows, rosters, users, {'10': 'A', '11': 'B', '12': 'C'}, 'now')
    assert len(teams) == 2 and teams.points_match_starters.all()
    score = players.loc[players.sleeper_id.eq('10'), 'fantasy_points'].item()
    assert score == 16.5


def test_sleeper_archive_rejects_team_score_mismatch():
    rows = [
        {'roster_id': 7, 'matchup_id': 3, 'points': 20.0, 'custom_points': None,
         'players': ['10'], 'starters': ['10'], 'players_points': {'10': 16.5}},
        {'roster_id': 9, 'matchup_id': 3, 'points': 8.0, 'custom_points': None,
         'players': ['12'], 'starters': ['12'], 'players_points': {'12': 8.0}},
    ]
    rosters = [{'roster_id': 7, 'owner_id': 'a'}, {'roster_id': 9, 'owner_id': 'b'}]
    users = [{'user_id': 'a', 'display_name': 'Alpha'}, {'user_id': 'b', 'display_name': 'Beta'}]
    import pytest
    with pytest.raises(ValueError, match='team total does not match'):
        _matchup_frames(1, rows, rosters, users, {}, 'now')


def test_player_week_reconciliation_flags_without_overwriting_score():
    players = pd.DataFrame([{'week': 1, 'sleeper_id': '10', 'fantasy_points': 12.5}])
    logs = pd.DataFrame([{'player_id': 'nba-10', 'game_id': 'g1', 'game_date': '2026-10-20',
                          'fantasy_points': 12.5}])
    crosswalk = pd.DataFrame([{'sleeper_id': '10', 'nba_player_id': 'nba-10', 'match_status': 'unique_name'}])
    reconciled, _summary = _reconcile_player_weeks(players, logs, crosswalk, week_start_weekday=1)
    assert reconciled.iloc[0].score_check == 'matches_an_nba_game'
    assert reconciled.iloc[0].fantasy_points == 12.5


def test_sleeper_score_discrepancy_repairs_only_box_score_confirmed_stats():
    logs = pd.DataFrame([{
        'player_id': '1630245', 'game_id': '0022600120', 'team': 'MIN', 'opponent': 'DEN',
        'minutes': 25.89, 'points': 9.0, 'rebounds': 1.0, 'assists': 0.0,
        'steals': 0.0, 'blocks': 1.0, 'turnovers': 3.0, 'personal_fouls': 2.0,
        'free_throws_made': 0.0, 'three_pointers_made': 1.0,
    }])
    reconciliation = pd.DataFrame([{
        'week': 2, 'nba_player_id': '1630245', 'fantasy_points': 5.0,
        'score_check': 'score_differs_from_nba_games',
        'candidate_nba_game_scores': '[{"game_id":"0022600120","fantasy_points":3.0}]',
    }])
    official = pd.DataFrame([{
        'personId': 1630245, 'teamTricode': 'MIN', 'minutes': '25:54',
        'points': 9, 'reboundsTotal': 1, 'assists': 0, 'steals': 1,
        'blocks': 1, 'turnovers': 3, 'foulsPersonal': 2,
        'freeThrowsMade': 0, 'threePointersMade': 1,
        'fieldGoalsAttempted': 7, 'freeThrowsAttempted': 0,
        'threePointersAttempted': 3, 'reboundsOffensive': 0,
        'reboundsDefensive': 1,
    }])

    class BoxScore:
        def get_data_frames(self):
            return [official]

    repaired, corrections = _repair_sleeper_score_discrepancies(
        logs, reconciliation, source=object(),
        boxscore_factory=lambda **_kwargs: BoxScore(),
    )

    assert logs.iloc[0].steals == 0
    assert repaired.iloc[0].steals == 1
    assert corrections[0]['fields_updated']['steals'] == {'game_log': 0.0, 'box_score': 1.0}
