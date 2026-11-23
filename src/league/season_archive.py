"""Immutable Sleeper and NBA observations for improving future-season models."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import numpy as np
import pandas as pd

from src.ingestion.base import validate_game_logs, write_raw
from src.ingestion.csv_source import CSVSource
from src.league.storage import archive_payload, cached_player_catalog, fetch_bundle, instance_config, league_dir
from src.schemas import BOX_SCORE_FIELDS


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.stem}-{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temp, path)


def _player_names(player_payload: dict) -> dict[str, str]:
    names = {}
    for sleeper_id, player in player_payload.items():
        name = player.get('full_name') or ' '.join(
            str(player.get(k) or '') for k in ('first_name', 'last_name')
        ).strip()
        if name:
            names[str(sleeper_id)] = str(name)
    return names


def _crosswalk(logs: pd.DataFrame, player_payload: dict) -> pd.DataFrame:
    from src.adp.loader import normalise_name

    nba_by_name: dict[str, set[str]] = {}
    for row in logs[['player_id', 'player_name']].drop_duplicates().to_dict('records'):
        key = normalise_name(row['player_name'])
        if key:
            nba_by_name.setdefault(key, set()).add(str(row['player_id']))
    sleeper_by_name: dict[str, set[str]] = {}
    names = _player_names(player_payload)
    for sleeper_id, name in names.items():
        key = normalise_name(name)
        if key:
            sleeper_by_name.setdefault(key, set()).add(sleeper_id)

    candidates = {}
    for sleeper_id, name in names.items():
        key = normalise_name(name)
        nba_ids = nba_by_name.get(key, set())
        sleeper_ids = sleeper_by_name.get(key, set())
        matched = len(nba_ids) == 1 and len(sleeper_ids) == 1
        candidates[sleeper_id] = {
            'sleeper_id': sleeper_id,
            'player_name': name,
            'birth_date': player_payload.get(sleeper_id, {}).get('birth_date'),
            'sleeper_team': player_payload.get(sleeper_id, {}).get('team'),
            'sleeper_positions': '|'.join(player_payload.get(sleeper_id, {}).get('fantasy_positions') or []),
            'sleeper_status': player_payload.get(sleeper_id, {}).get('status'),
            'sleeper_injury_status': player_payload.get(sleeper_id, {}).get('injury_status'),
            'nba_player_id': next(iter(nba_ids)) if matched else '',
            'match_status': 'unique_name' if matched else
                            'ambiguous_name' if nba_ids or len(sleeper_ids) > 1 else 'no_nba_game_log',
        }
    # Enforce one-to-one identity across the complete catalog, not just per name.
    nba_to_sleeper: dict[str, set[str]] = {}
    for sleeper_id, row in candidates.items():
        if row['match_status'] == 'unique_name':
            nba_to_sleeper.setdefault(row['nba_player_id'], set()).add(sleeper_id)
    for sleeper_id, row in candidates.items():
        if row['match_status'] == 'unique_name' and len(nba_to_sleeper[row['nba_player_id']]) > 1:
            row['nba_player_id'] = ''
            row['match_status'] = 'ambiguous_name'
    rows = list(candidates.values())
    return pd.DataFrame(rows)


def _matchup_frames(week: int, rows: list[dict], rosters: list[dict], users: list[dict],
                    names: dict[str, str], retrieved_at: str):
    from src.league.matchups import validate_matchups

    roster_ids = [str(r['roster_id']) for r in rosters]
    validate_matchups(rows, roster_ids)
    owners = {str(r['roster_id']): str(r.get('owner_id') or '') for r in rosters}
    users_by_id = {str(u.get('user_id')): u for u in users}
    team_rows, player_rows = [], []
    seen_rostered_players = set()
    for record in rows:
        roster_id = str(record['roster_id'])
        owner_id = owners[roster_id]
        user = users_by_id.get(owner_id, {})
        manager = (user.get('metadata') or {}).get('team_name') or user.get('display_name') or user.get('username') or owner_id
        players = [str(pid) for pid in (record.get('players') or [])]
        starters = [str(pid) for pid in (record.get('starters') or []) if str(pid) not in ('0', '')]
        points_map = record.get('players_points')
        if not isinstance(points_map, dict):
            raise ValueError(f'Sleeper week {week} omitted per-player points for roster {roster_id}')
        normalized_points = {str(pid): float(value) for pid, value in points_map.items()
                             if value is not None and np.isfinite(float(value))}
        missing = sorted(set(starters) - normalized_points.keys())
        if missing:
            raise ValueError(f'Sleeper week {week} omitted starter points for roster {roster_id}: {missing}')
        starter_total = sum(normalized_points[pid] for pid in starters)
        points = record.get('points')
        custom_points = record.get('custom_points')
        points_match = (custom_points is not None or points is None or
                        abs(float(points) - starter_total) <= 0.05)
        if not points_match:
            raise ValueError(f'Sleeper week {week} team total does not match starter points for roster {roster_id}')
        team_rows.append({
            'week': week, 'roster_id': roster_id, 'owner_id': owner_id, 'team_name': manager,
            'matchup_id': record.get('matchup_id'), 'points': points, 'custom_points': custom_points,
            'starter_points_sum': round(starter_total, 4), 'points_match_starters': points_match,
            'players': json.dumps(players), 'starters': json.dumps(starters),
            'bench': json.dumps([pid for pid in players if pid not in starters]),
            'retrieved_at': retrieved_at,
        })
        for sleeper_id in sorted(set(players) | normalized_points.keys()):
            if sleeper_id in seen_rostered_players:
                # A mid-week transaction can make an API roster reflect the latest
                # owner; keep the full team/week rows and flag this player collision.
                duplicate_owner = True
            else:
                duplicate_owner = False
                seen_rostered_players.add(sleeper_id)
            player_rows.append({
                'week': week, 'roster_id': roster_id, 'owner_id': owner_id, 'team_name': manager,
                'matchup_id': record.get('matchup_id'), 'sleeper_id': sleeper_id,
                'player_name': names.get(sleeper_id, ''),
                'fantasy_points': normalized_points.get(sleeper_id),
                'starter': sleeper_id in starters, 'duplicate_roster_observation': duplicate_owner,
                'retrieved_at': retrieved_at,
            })
    return pd.DataFrame(team_rows), pd.DataFrame(player_rows)


def _validate_logs(logs: pd.DataFrame, season: str, previous: pd.DataFrame) -> None:
    problems = validate_game_logs(logs)
    if problems:
        raise ValueError('; '.join(problems))
    if not logs.season.astype(str).eq(season).all():
        raise ValueError('NBA API returned game logs from an unexpected season')
    numeric = logs[['minutes', *BOX_SCORE_FIELDS]].apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(numeric.to_numpy()).all() or (numeric < 0).any().any():
        raise ValueError('NBA API returned missing, nonfinite, or negative box-score values')
    dates = pd.to_datetime(logs.game_date, errors='coerce', utc=True)
    if dates.isna().any() or (dates > pd.Timestamp.now(tz='UTC') + pd.Timedelta(days=1)).any():
        raise ValueError('NBA API returned invalid or future game dates')
    keys = lambda frame: set(zip(frame.player_id.astype(str), frame.game_id.astype(str).str.lstrip('0')))
    if not keys(previous) <= keys(logs):
        raise ValueError('NBA response omitted previously archived player-games; prior data remains current')


def _reconcile_player_weeks(player_weeks: pd.DataFrame, logs: pd.DataFrame,
                            crosswalk: pd.DataFrame, week_start_weekday: int):
    """Compare Sleeper weekly scores with available one-game NBA scores.

    Sleeper does not publish the locked game ID, so this records candidate scores
    and mismatches without guessing which game was selected.
    """
    unique = crosswalk[crosswalk.match_status.eq('unique_name')]
    sleeper_to_nba = dict(zip(unique.sleeper_id.astype(str), unique.nba_player_id.astype(str)))
    dates = pd.to_datetime(logs.game_date, errors='coerce')
    first_game = dates.min().normalize()
    week_offset = (first_game.dayofweek - (week_start_weekday - 1)) % 7
    first_week = first_game - pd.Timedelta(days=week_offset)
    log_copy = logs.copy()
    log_copy['_archive_date'] = dates.dt.normalize()
    output = []
    for row in player_weeks.to_dict('records'):
        score = row.get('fantasy_points')
        nba_id = sleeper_to_nba.get(str(row['sleeper_id']))
        week_start = first_week + pd.Timedelta(days=7 * (int(row['week']) - 1))
        week_end = week_start + pd.Timedelta(days=7)
        games = pd.DataFrame()
        if nba_id:
            games = log_copy[(log_copy.player_id.astype(str) == nba_id) &
                             (log_copy._archive_date >= week_start) &
                             (log_copy._archive_date < week_end)]
        candidates = ([{'game_id': str(game_id), 'game_date': game_date.date().isoformat(),
                        'fantasy_points': float(fantasy_points)}
                       for game_id, game_date, fantasy_points in
                       games[['game_id', '_archive_date', 'fantasy_points']].itertuples(index=False, name=None)]
                      if not games.empty else [])
        if not nba_id:
            status = 'no_unique_nba_crosswalk'
        elif pd.isna(score):
            status = 'sleeper_score_missing'
        elif not candidates:
            status = 'no_nba_game' if abs(float(score)) < 0.01 else 'score_without_nba_game'
        elif any(abs(float(score) - candidate['fantasy_points']) < 0.02 for candidate in candidates):
            status = 'matches_an_nba_game'
        elif abs(float(score)) < 0.01:
            status = 'zero_despite_nba_game'
        else:
            status = 'score_differs_from_nba_games'
        output.append({**row, 'nba_player_id': nba_id or '',
                       'week_start_assumption': week_start.date().isoformat(),
                       'candidate_nba_game_scores': json.dumps(candidates),
                       'score_check': status})
    result = pd.DataFrame(output)
    counts = result.score_check.value_counts().to_dict() if not result.empty else {}
    return result, {'week_start_weekday_iso': week_start_weekday,
                    'week_one_start_assumption': first_week.date().isoformat(),
                    'counts': {str(k): int(v) for k, v in counts.items()},
                    'note': 'Sleeper omits the selected NBA game ID; mismatches are flags for review, not corrected scores.'}


def _repair_duplicate_player_games(logs: pd.DataFrame, source) -> tuple[pd.DataFrame, list[dict]]:
    """Resolve source feed duplicate identities from the NBA's per-game box score."""
    duplicate_mask = logs.duplicated(['player_id', 'game_id'], keep=False)
    if not duplicate_mask.any():
        return logs.copy(), []
    from nba_api.stats.endpoints import boxscoretraditionalv3

    repaired = logs.copy()
    corrections = []
    duplicates = logs.loc[duplicate_mask, ['player_id', 'game_id']].drop_duplicates()
    for game_id in duplicates.game_id.astype(str).unique():
        box = boxscoretraditionalv3.BoxScoreTraditionalV3(
            game_id=game_id, timeout=getattr(source, 'timeout', 30)
        ).get_data_frames()[0]
        game_rows = logs[logs.game_id.astype(str).eq(game_id)]
        teams = sorted(set(game_rows.team.dropna().astype(str)))
        if len(teams) != 2:
            raise ValueError(f'Cannot validate duplicated NBA game {game_id}: expected two teams')
        for player_id in duplicates.loc[duplicates.game_id.astype(str).eq(game_id), 'player_id'].astype(str):
            source_rows = game_rows[game_rows.player_id.astype(str).eq(player_id)]
            official = box[box.personId.astype(str).eq(player_id)]
            if len(official) != 1:
                raise ValueError(f'NBA box score did not uniquely resolve player {player_id} in {game_id}')
            official_row = official.iloc[0]
            team = str(official_row.teamTricode)
            valid_existing = source_rows[source_rows.team.astype(str).eq(team)]
            if len(valid_existing) != 1 or team not in teams:
                raise ValueError(f'NBA game-log duplicate for {player_id}/{game_id} cannot be matched to the box score')
            row = valid_existing.iloc[0].to_dict()
            minute_text = str(official_row.minutes or '0:00')
            if ':' in minute_text:
                mins, seconds = minute_text.split(':', 1)
                minutes = float(mins or 0) + float(seconds or 0) / 60.0
            else:
                minutes = float(minute_text or 0)
            official_stats = {
                'minutes': minutes, 'points': official_row.points,
                'rebounds': official_row.reboundsTotal, 'assists': official_row.assists,
                'steals': official_row.steals, 'blocks': official_row.blocks,
                'turnovers': official_row.turnovers, 'personal_fouls': official_row.foulsPersonal,
                'free_throws_made': official_row.freeThrowsMade,
                'three_pointers_made': official_row.threePointersMade,
                'field_goals_attempted': official_row.fieldGoalsAttempted,
                'free_throws_attempted': official_row.freeThrowsAttempted,
                'three_pointers_attempted': official_row.threePointersAttempted,
                'offensive_rebounds': official_row.reboundsOffensive,
                'defensive_rebounds': official_row.reboundsDefensive,
                'team': team,
                'opponent': next(other for other in teams if other != team),
            }
            row.update({key: float(value) if key not in ('team', 'opponent') else value
                        for key, value in official_stats.items()})
            replacement = pd.DataFrame([row], columns=repaired.columns)
            keep = ~((repaired.player_id.astype(str) == player_id) &
                     (repaired.game_id.astype(str) == game_id))
            repaired = pd.concat([repaired.loc[keep], replacement], ignore_index=True)
            corrections.append({
                'repair_type': 'duplicate_player_game',
                'player_id': player_id, 'game_id': game_id,
                'source_rows': int(len(source_rows)), 'kept_team': team,
                'repair_source': 'NBA BoxScoreTraditionalV3',
                'source_url': f'https://stats.nba.com/stats/boxscoretraditionalv3?GameID={game_id}',
                'reason': 'PlayerGameLogs contained multiple rows for the same player/game; per-game box score resolved identity and stats.',
            })
    return repaired, corrections


def _repair_sleeper_score_discrepancies(logs: pd.DataFrame, reconciliation: pd.DataFrame,
                                        source, boxscore_factory=None) -> tuple[pd.DataFrame, list[dict]]:
    """Correct NBA stat-feed rows only when an official box score supports it.

    Sleeper's actual weekly player score provides a useful cross-check. When it
    matches none of the candidate game scores, inspect those games' official
    box scores and update only player-game rows whose box-score stats differ.
    The unmodified PlayerGameLogs response remains archived separately.
    """
    mismatches = reconciliation[reconciliation.score_check.eq('score_differs_from_nba_games')]
    if mismatches.empty:
        return logs.copy(), []
    if boxscore_factory is None:
        from nba_api.stats.endpoints import boxscoretraditionalv3
        boxscore_factory = boxscoretraditionalv3.BoxScoreTraditionalV3

    def id_key(value):
        text = str(value).strip()
        if text.endswith('.0'):
            text = text[:-2]
        return text.lstrip('0') or '0'

    def box_minutes(value):
        value = '0:00' if pd.isna(value) else str(value)
        if ':' not in value:
            return float(value or 0)
        mins, seconds = value.split(':', 1)
        return float(mins or 0) + float(seconds or 0) / 60.0

    repaired = logs.copy()
    corrections = []
    box_scores = {}
    for record in mismatches.to_dict('records'):
        player_id = id_key(record.get('nba_player_id', ''))
        try:
            candidates = json.loads(record.get('candidate_nba_game_scores') or '[]')
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError('Sleeper reconciliation has invalid candidate-game JSON') from exc
        for candidate in candidates:
            game_id = str(candidate.get('game_id', '')).strip()
            if not game_id:
                continue
            game_key = id_key(game_id)
            if game_id not in box_scores:
                box_scores[game_id] = boxscore_factory(
                    game_id=game_id, timeout=getattr(source, 'timeout', 30)
                ).get_data_frames()[0]
            box = box_scores[game_id]
            official = box[box.personId.astype(str).map(id_key).eq(player_id)]
            game_rows = repaired[
                repaired.player_id.astype(str).map(id_key).eq(player_id)
                & repaired.game_id.astype(str).map(id_key).eq(game_key)
            ]
            if len(official) != 1 or len(game_rows) != 1:
                continue

            official_row = official.iloc[0]
            existing = game_rows.iloc[0]
            team = str(official_row.teamTricode)
            official_stats = {
                'minutes': box_minutes(official_row.minutes),
                'points': official_row.points, 'rebounds': official_row.reboundsTotal,
                'assists': official_row.assists, 'steals': official_row.steals,
                'blocks': official_row.blocks, 'turnovers': official_row.turnovers,
                'personal_fouls': official_row.foulsPersonal,
                'free_throws_made': official_row.freeThrowsMade,
                'three_pointers_made': official_row.threePointersMade,
                'field_goals_attempted': official_row.fieldGoalsAttempted,
                'free_throws_attempted': official_row.freeThrowsAttempted,
                'three_pointers_attempted': official_row.threePointersAttempted,
                'offensive_rebounds': official_row.reboundsOffensive,
                'defensive_rebounds': official_row.reboundsDefensive,
            }
            changed = {}
            for field, actual in official_stats.items():
                if field not in repaired.columns:
                    continue
                previous = pd.to_numeric(pd.Series([existing[field]]), errors='coerce').iloc[0]
                actual = float(actual)
                tolerance = 0.05 if field == 'minutes' else 1e-6
                if pd.isna(previous) or abs(float(previous) - actual) > tolerance:
                    changed[field] = {'game_log': None if pd.isna(previous) else float(previous),
                                      'box_score': actual}
            if not changed:
                continue

            repair_index = game_rows.index[0]
            for field, values in changed.items():
                repaired.at[repair_index, field] = values['box_score']
            if 'team' in repaired.columns:
                repaired.at[repair_index, 'team'] = team
            if 'opponent' in repaired.columns:
                other_teams = set(box.teamTricode.astype(str)) - {team}
                if len(other_teams) == 1:
                    repaired.at[repair_index, 'opponent'] = next(iter(other_teams))
            corrections.append({
                'repair_type': 'sleeper_score_stat_discrepancy',
                'player_id': player_id, 'game_id': game_id,
                'sleeper_week': int(record['week']),
                'sleeper_score': float(record['fantasy_points']),
                'fields_updated': changed,
                'repair_source': 'NBA BoxScoreTraditionalV3',
                'source_url': f'https://stats.nba.com/stats/boxscoretraditionalv3?GameID={game_id}',
            })
    return repaired, corrections


def archive_season(cfg, through_week: int | None = None, nba_source=None) -> dict:
    """Serialize the archive with dashboard/performance refreshes."""
    lock = league_dir(cfg) / 'refresh.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError('Another league or performance refresh is running; retry after it finishes') from exc
    os.close(fd)
    try:
        return _archive_season_unlocked(cfg, through_week, nba_source)
    finally:
        lock.unlink(missing_ok=True)


def _archive_season_unlocked(cfg, through_week: int | None, nba_source=None) -> dict:
    """Snapshot all available league weeks and season-to-date NBA player games.

    Sleeper is read-only. Snapshots are append-only; a repeated run creates a new
    observation, which is useful while the current fantasy week is still live.
    """
    from src.ingestion.nba_api_source import NBAApiSource
    from src.scoring import ScoringEngine
    from src.league.storage import load_league
    from src.ingestion.sleeper_source import _get_json

    state, _ = load_league(cfg)
    league_id = str(state.league_id)
    expected_season = str(cfg.league.raw['meta']['season'])
    if not re.fullmatch(r'\d{4}-\d{2}', expected_season):
        raise ValueError('Configured season must use NBA format YYYY-YY')

    league_root = league_dir(cfg)
    state_payload = _get_json('https://api.sleeper.app/v1/state/nba')
    nba_year = str(state_payload.get('season') or '')
    api_season = f'{nba_year}-{str((int(nba_year) + 1) % 100).zfill(2)}' if nba_year.isdigit() else ''
    if api_season != expected_season:
        raise ValueError(f'Sleeper NBA state is {api_season or "unknown"}, configured league season is {expected_season}')
    current_week = state_payload.get('week')
    if through_week is None:
        if not isinstance(current_week, int) or current_week < 1:
            raise ValueError('Sleeper has not published a current NBA fantasy week')
        through_week = current_week
    if not isinstance(through_week, int) or not 1 <= through_week <= 52:
        raise ValueError('Archive week must be 1..52')
    state_archive = archive_payload(league_root, f'season-{expected_season}-sleeper-state', state_payload,
                                    'https://api.sleeper.app/v1/state/nba')

    # The full player catalogue is cached for 24 hours; endpoint bundle history
    # and transaction/roster observations are refreshed on every archive run.
    bundle = fetch_bundle(cfg, source=None, archive_composite=False)
    if str(bundle.get('league_id')) != league_id or str(bundle['league'].get('season')) != nba_year:
        raise ValueError('Sleeper league snapshot does not match the configured league and season')
    if str(bundle['league'].get('sport')) != 'nba':
        raise ValueError('Expected an NBA Sleeper league')
    rosters = bundle['rosters']
    users = bundle['users']
    matchup_rows, raw_matchups, all_team_rows, all_player_rows = [], {}, [], []
    names = _player_names(bundle['players'])
    for week in range(1, through_week + 1):
        url = f'https://api.sleeper.app/v1/league/{league_id}/matchups/{week}'
        payload = _get_json(url)
        if not isinstance(payload, list) or not payload:
            raise ValueError(f'Sleeper has no matchup rows for week {week}; archive stopped without publishing')
        retrieved_at = datetime.now(timezone.utc).isoformat()
        teams, players = _matchup_frames(week, payload, rosters, users, names, retrieved_at)
        raw_matchups[str(week)] = payload
        all_team_rows.append(teams)
        all_player_rows.append(players)
        matchup_rows.append({'week': week, 'rows': len(payload)})

    nba_source = nba_source or NBAApiSource(timeout=30)
    source_logs = nba_source.fetch_game_logs(expected_season)
    logs, corrections = _repair_duplicate_player_games(source_logs, nba_source)
    previous = pd.DataFrame(columns=['player_id', 'game_id'])
    try:
        previous = CSVSource(cfg.paths['raw']).fetch_game_logs(expected_season)
    except FileNotFoundError:
        pass
    _validate_logs(logs, expected_season, previous)
    fantasy_logs = ScoringEngine(cfg.scoring).score_dataframe(logs)
    crosswalk = _crosswalk(logs, bundle['players'])
    team_weeks = pd.concat(all_team_rows, ignore_index=True)
    player_weeks = pd.concat(all_player_rows, ignore_index=True)
    reconciliation, reconciliation_summary = _reconcile_player_weeks(
        player_weeks, fantasy_logs, crosswalk, int(cfg.league.calendar.get('week_start_weekday', 1))
    )
    logs, score_corrections = _repair_sleeper_score_discrepancies(
        logs, reconciliation, nba_source
    )
    if score_corrections:
        corrections.extend(score_corrections)
        _validate_logs(logs, expected_season, previous)
        fantasy_logs = ScoringEngine(cfg.scoring).score_dataframe(logs)
        reconciliation, reconciliation_summary = _reconcile_player_weeks(
            player_weeks, fantasy_logs, crosswalk, int(cfg.league.calendar.get('week_start_weekday', 1))
        )

    now = datetime.now(timezone.utc)
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    observation_id = f'{stamp}_{uuid.uuid4().hex[:8]}'
    season_root = league_root / 'seasons' / expected_season
    season_root.mkdir(parents=True, exist_ok=True)
    snapshot_path = season_root / 'snapshots' / observation_id
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    stage = snapshot_path.with_name(f'.{observation_id}.tmp')
    stage.mkdir()
    try:
        datasets = {}
        sleeper_dir = stage / 'sleeper'
        matchup_dir = sleeper_dir / 'matchups'
        matchup_dir.mkdir(parents=True)
        for name, value in {
            'league': bundle['league'], 'users': users, 'drafts': bundle['drafts'],
            'draft': bundle['draft'], 'picks': bundle['picks'], 'transactions': bundle['transactions'],
            'rosters': rosters,
        }.items():
            path = sleeper_dir / f'{name}.json'
            path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            datasets[f'sleeper_{name}'] = {'path': path.relative_to(stage).as_posix(), 'sha256': _sha(path)}
        catalog_pointer = league_root / 'players-catalog-current.json'
        # fetch_bundle guarantees a valid daily cache pointer exists.
        catalog_ref = json.loads(catalog_pointer.read_text(encoding='utf-8'))
        catalog_file = (league_root / catalog_ref['path']).resolve()
        if not catalog_file.is_relative_to(league_root.resolve()) or _sha(catalog_file) != catalog_ref['sha256']:
            raise ValueError('Cached Sleeper player-catalogue pointer failed validation')
        datasets['sleeper_player_catalogue'] = {
            'path': str(catalog_file.relative_to(cfg.paths['repo'])).replace('\\', '/'),
            'sha256': catalog_ref['sha256'], 'retrieved_at': catalog_ref['retrieved_at'],
        }
        state_path = sleeper_dir / 'nba_state.json'
        state_path.write_text(json.dumps(state_payload, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        datasets['sleeper_nba_state'] = {'path': state_path.relative_to(stage).as_posix(),
            'sha256': _sha(state_path), 'source_url': 'https://api.sleeper.app/v1/state/nba',
            'raw_archive': str(state_archive.relative_to(cfg.paths['repo'])).replace('\\', '/')}
        for week, rows in raw_matchups.items():
            url = f'https://api.sleeper.app/v1/league/{league_id}/matchups/{week}'
            path = matchup_dir / f'week-{int(week):02d}.json'
            path.write_text(json.dumps(rows, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            datasets[f'sleeper_matchups_week_{week}'] = {'path': path.relative_to(stage).as_posix(), 'sha256': _sha(path),
                'source_url': url}
            archive_payload(league_root, f'season-{expected_season}-matchups-{week}', rows, url)

        nba_dir = stage / 'nba'
        nba_dir.mkdir()
        source_path = nba_dir / 'player_game_logs_api_response.csv'
        source_logs.to_csv(source_path, index=False)
        datasets['nba_player_game_logs_api_response'] = {
            'path': source_path.relative_to(stage).as_posix(), 'sha256': _sha(source_path),
            'rows': len(source_logs), 'note': 'Unmodified NBA PlayerGameLogs response, including source anomalies.'}
        datasets['nba_box_score_repairs'] = {
            'rows': len(corrections), 'repairs': corrections,
            'note': 'Corrections supported by NBA BoxScoreTraditionalV3; unmodified game-log response is preserved separately.'}
        fantasy_path = nba_dir / 'fantasy_player_game_logs.csv'
        fantasy_logs.to_csv(fantasy_path, index=False)
        datasets['fantasy_player_game_logs'] = {'path': fantasy_path.relative_to(stage).as_posix(),
            'sha256': _sha(fantasy_path), 'rows': len(fantasy_logs),
            'scoring_config_sha256': hashlib.sha256(json.dumps(cfg.league.raw.get('scoring', {}), sort_keys=True).encode()).hexdigest()}
        crosswalk_path = nba_dir / 'sleeper_nba_player_crosswalk.csv'
        crosswalk.to_csv(crosswalk_path, index=False)
        datasets['sleeper_nba_player_crosswalk'] = {'path': crosswalk_path.relative_to(stage).as_posix(),
            'sha256': _sha(crosswalk_path), 'matched': int(crosswalk.match_status.eq('unique_name').sum()),
            'catalogue_players': len(crosswalk)}
        team_path = sleeper_dir / 'team_week_results.csv'
        team_weeks.to_csv(team_path, index=False)
        datasets['team_week_results'] = {'path': team_path.relative_to(stage).as_posix(),
            'sha256': _sha(team_path), 'rows': len(team_weeks)}
        player_path = sleeper_dir / 'player_week_results.csv'
        player_weeks.to_csv(player_path, index=False)
        datasets['player_week_results'] = {'path': player_path.relative_to(stage).as_posix(),
            'sha256': _sha(player_path), 'rows': len(player_weeks),
            'meaning': 'Sleeper per-player fantasy points for the fantasy week; not per-game scores'}
        reconciliation_path = sleeper_dir / 'player_week_score_reconciliation.csv'
        reconciliation.to_csv(reconciliation_path, index=False)
        datasets['player_week_score_reconciliation'] = {
            'path': reconciliation_path.relative_to(stage).as_posix(),
            'sha256': _sha(reconciliation_path), 'rows': len(reconciliation),
            'summary': reconciliation_summary,
        }

        raw_root = cfg.paths['raw'] / expected_season
        raw_id = uuid.uuid4().hex
        relative_raw = Path('snapshots') / f'{raw_id}.csv'
        raw_path = raw_root / relative_raw
        write_raw(logs, raw_path, getattr(nba_source, 'name', 'nba_api'), 'player_game_logs', expected_season,
                  notes='Immutable season archive snapshot; full NBA player-game box scores.')
        raw_pointer = {'file': relative_raw.as_posix(), 'sha256': _sha(raw_path)}
        staged_current = raw_root / 'current.json'
        datasets['nba_player_game_logs'] = {
            'path': str(raw_path.relative_to(cfg.paths['repo'])).replace('\\', '/'),
            'sha256': raw_pointer['sha256'], 'rows': len(logs),
            'history_through': str(pd.to_datetime(logs.game_date).max().date()),
            'manifest': str(raw_path.with_suffix('.csv.manifest.json').relative_to(cfg.paths['repo'])).replace('\\', '/'),
        }

        manifest = {
            'schema_version': 1, 'league_id': league_id, 'nba_season': expected_season,
            'sleeper_season': nba_year, 'observation_id': observation_id,
            'observed_at': now.isoformat(), 'current_sleeper_week': current_week,
            'matchup_weeks_archived': matchup_rows,
            'nba_api_rows': len(logs), 'nba_history_through': datasets['nba_player_game_logs']['history_through'],
            'nba_source_rows': len(source_logs), 'nba_box_score_corrections': corrections,
            'player_week_score_reconciliation': reconciliation_summary,
            'sleeper_score_note': 'Weekly Sleeper player totals and team totals are retained. The API does not provide the game ID or lock timestamp for each player score.',
            'datasets': datasets,
        }
        (stage / 'archive_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(stage, snapshot_path)
        _atomic_json(season_root / 'current.json', {
            'observation_id': observation_id, 'path': f'snapshots/{observation_id}',
            'manifest_sha256': _sha(snapshot_path / 'archive_manifest.json'),
            'observed_at': now.isoformat(),
        })
        _atomic_json(staged_current, raw_pointer)
    except Exception:
        if stage.exists():
            import shutil
            shutil.rmtree(stage)
        raise

    return {
        'league_id': league_id, 'season': expected_season, 'snapshot': str(snapshot_path),
        'through_week': through_week, 'teams_per_week': len(rosters),
        'team_week_rows': len(team_weeks), 'player_week_rows': len(player_weeks),
        'nba_player_game_rows': len(logs), 'nba_history_through': datasets['nba_player_game_logs']['history_through'],
        'unique_crosswalks': int(crosswalk.match_status.eq('unique_name').sum()),
        'crosswalk_players': len(crosswalk),
    }
