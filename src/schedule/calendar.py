"""Schedule validation and one calendar shared by every NBA team."""
from __future__ import annotations

import pandas as pd


def counts_to_pmfs(counts):
    if counts.empty:
        return {}
    return {str(team): {int(n): float(p) for n, p in counts[team].value_counts(normalize=True).items()}
            for team in counts}


def regular_schedule(schedule):
    """Exclude identifiable NBA preseason/cup-final games and unresolved fixtures.

    NBA IDs are 10 digits: 002 regular season, 001 preseason, 006 cup final.
    Generic user schedules without those IDs are accepted with explicit dates.
    Raw input remains untouched. Return a cleaned copy and diagnostics.
    """
    frame = schedule.copy()
    notes = []
    if 'game_id' in frame:
        ids = frame.game_id.astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(10)
        nba = ids.str.match(r'^00[12456]\d{7}$')
        remove = nba & ~ids.str.startswith('002')
        if remove.any():
            notes.append(f'Excluded {int(remove.sum())} non-regular NBA fixtures')
        # A full NBA regular season is 1,230 numbered games. Games still lacking a
        # date or teams (e.g. NBA Cup knockout and fill-in games) can be absent
        # outright, which silently lowers some teams' weekly game counts.
        regular = ids[nba & ids.str.startswith('002')]
        if len(regular) > 1000:
            numbers = set(regular.str[5:].astype(int))
            missing = len(set(range(1, 1231)) - numbers)
            if missing:
                notes.append(f'{missing} of 1230 regular-season NBA games are not in the schedule file '
                             '(dates/teams TBD, e.g. NBA Cup knockout games); affected teams\' weekly game '
                             'counts are too low; schedule incomplete')
        frame = frame[~remove].copy()
        frame = frame.drop_duplicates('game_id')
    frame['game_date'] = pd.to_datetime(frame['game_date'], errors='coerce', utc=True).dt.tz_localize(None).dt.normalize()
    valid = frame.game_date.notna()
    cols = ['home_team', 'away_team'] if 'home_team' in frame else ['team']
    for c in cols:
        valid &= frame[c].notna() & frame[c].astype(str).str.strip().ne('')
    if (~valid).any():
        notes.append(f'Excluded {int((~valid).sum())} unresolved/date-less fixtures; schedule incomplete')
    return frame[valid].copy(), notes


def weekly_counts(schedule, week_start_weekday=1, start=None, end=None):
    from src.schedule.weeks import fantasy_week_start
    if not 1 <= week_start_weekday <= 7:
        raise ValueError('Weekday must be ISO 1..7')
    if schedule.empty:
        return pd.DataFrame()
    frame = schedule.copy()
    if {'home_team', 'away_team'} <= set(frame):
        frame = pd.concat([frame[['game_date', c]].rename(columns={c: 'team'})
                           for c in ('home_team', 'away_team')], ignore_index=True)
    frame['week'] = fantasy_week_start(frame.game_date, week_start_weekday)
    first = pd.Timestamp(start) if start is not None else frame.week.min()
    last = pd.Timestamp(end) if end is not None else frame.week.max()
    first, last = [fantasy_week_start(pd.Series([x]), week_start_weekday).iloc[0] for x in (first, last)]
    weeks = pd.date_range(first, last, freq='7D')
    counts = frame.groupby(['week', 'team']).size().unstack(fill_value=0)
    return counts.reindex(weeks, fill_value=0).fillna(0).astype(int)


def latest_historical_schedule(logs):
    """A single historical season; never create offseason zero-game weeks."""
    latest = logs[logs.season == max(logs.season)] if 'season' in logs and not logs.empty else logs
    return latest[['game_date', 'team']].drop_duplicates()
