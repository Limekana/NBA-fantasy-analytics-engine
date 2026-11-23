"""Conservative, held-out-tested game-context adjustment, not a causal model.

Predict relative fantasy production per minute using opponent, home venue and
team back-to-back status. Minutes/return dates are deliberately not forecast here.
Training uses only earlier dates to estimate player baselines during validation.
"""
import numpy as np
import pandas as pd


def fit_game_context(scored):
    result = {'enabled': False, 'method': 'Ridge on relative FP/min residuals; chronological 80/20 check',
              'cap': .10, 'ridge': 200., 'opponents': {}, 'home': 0., 'back_to_back': 0.,
              'note': 'Neutral adjustments: insufficient usable history.'}
    required = {'player_id', 'team', 'opponent', 'home', 'game_date', 'minutes', 'fantasy_points', 'season'}
    if not required <= set(scored):
        return result
    frame = scored.copy()
    frame['game_date'] = pd.to_datetime(frame.game_date, errors='coerce')
    frame = frame.dropna(subset=['game_date', 'minutes', 'fantasy_points', 'opponent', 'home'])
    frame = frame[frame.season == sorted(frame.season.astype(str).unique())[-1]] if len(frame) else frame
    calendar = frame[['team', 'game_date']].drop_duplicates().sort_values('game_date')
    calendar['b2b'] = calendar.groupby('team').game_date.diff().dt.days.eq(1).astype(float)
    frame = frame.merge(calendar, on=['team', 'game_date'], validate='many_to_one')
    frame = frame[frame.minutes >= 10].sort_values('game_date')
    if len(frame) < 1000:
        return result
    teams = sorted(frame.opponent.unique())
    split = sorted(frame.game_date.unique())[int(frame.game_date.nunique()*.8)]
    train, test = frame[frame.game_date < split], frame[frame.game_date >= split]

    def baseline(rows):
        grouped = rows.groupby('player_id').agg(fp=('fantasy_points', 'sum'), mins=('minutes', 'sum'), n=('minutes', 'size'))
        return (grouped.fp / grouped.mins)[(grouped.n >= 15) & (grouped.fp / grouped.mins > .2)]

    def design(rows, rates):
        expected = rows.player_id.map(rates)*rows.minutes
        good = expected.notna() & (expected > 0)
        rows, expected = rows[good], expected[good]
        x = np.column_stack([rows.opponent.eq(t).astype(float) for t in teams] +
                            [rows.home.astype(float)-.5, rows.b2b.astype(float)])
        y = (rows.fantasy_points/expected-1).clip(-1, 2).to_numpy()
        return x, y

    x, y = design(train, baseline(train))
    xt, yt = design(test, baseline(train))
    if len(y) < 1000 or len(yt) < 250:
        return result
    solve = lambda a, b: np.linalg.solve(a.T@a + np.eye(a.shape[1])*result['ridge'], a.T@b)
    # Lock-In samples are already rescaled to the projected mean, so the opponent
    # dummies must not absorb the average residual (it was -1.2% for 2025-26).
    # Centring keeps only relative opponent/venue/rest effects.
    weights = solve(x, y - y.mean())
    baseline_error = float(np.mean(yt**2))
    adjusted_error = float(np.mean((yt-np.clip(xt@weights, -.10, .10))**2))
    result.update(validation_rows=len(yt), train_rows=len(y), baseline_mse=baseline_error,
                  adjusted_mse=adjusted_error, validation_start=str(pd.Timestamp(split).date()),
                  history_through=str(frame.game_date.max().date()), season=str(frame.season.iloc[0]))
    # Small apparent improvements do not justify adding adjustments.
    if adjusted_error >= baseline_error*.998:
        result['note'] = 'Neutral adjustments: opponent/venue/rest model did not improve held-out error by 0.2%.'
        return result
    x, y = design(frame, baseline(frame))
    weights = solve(x, y - y.mean())
    counts = frame.opponent.value_counts()
    result.update(enabled=True, opponents={t:float(weights[i]) for i,t in enumerate(teams) if counts[t]>=100},
                  home=float(weights[-2]), back_to_back=float(weights[-1]),
                  note='Experimental context adjustment passed one historical holdout; capped at ±10%. Not calibrated future accuracy. No explicit pace, travel or injury forecast.')
    return result
