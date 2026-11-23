"""Manual, read-only Lock-In decisions; never infer live eligibility from dates."""
import math


def continuation(sample, games, play_probability=1.0, factors=None):
    """Independent future appearances; last team-game DNP is zero, not a rewind.

    A non-final DNP gives no lockable score and simply advances to the next game.
    The appearance probability is a user scenario, not an injury forecast.
    """
    if not sample or any(not math.isfinite(x) for x in sample):
        raise ValueError('A finite scoring distribution is required')
    if not isinstance(games, int) or not 0 <= games <= 8:
        raise ValueError('Remaining games must be an integer from 0 to 8')
    if not math.isfinite(play_probability) or not 0 <= play_probability <= 1:
        raise ValueError('Appearance probability must be between zero and one')
    factors = [1.] * games if factors is None else factors
    if len(factors) != games or any(not math.isfinite(f) or not .5 <= f <= 1.5 for f in factors):
        raise ValueError('One finite context factor (0.5–1.5) is required per future game')
    value = 0.0
    for k, factor in enumerate(reversed(factors), 1):
        played = sum(x*factor if k == 1 else max(x*factor, value) for x in sample) / len(sample)
        value = play_probability * played + (1 - play_probability) * value
    return value


def advise(sample, score, games_remaining, play_probability=1.0, *, completed=False,
           eligible_starter=False, latest_game=False, deadline_open=False, already_locked=False, factors=None):
    if not math.isfinite(score):
        raise ValueError('Enter a finite completed-game score')
    threshold = continuation(sample, games_remaining, play_probability, factors)
    if already_locked:
        return {'action': 'ALREADY LOCKED', 'threshold': threshold}
    if not all((completed, eligible_starter, latest_game, deadline_open)):
        return {'action': 'VERIFY ELIGIBILITY', 'threshold': threshold}
    return {'action': 'KEEP FINAL GAME' if games_remaining == 0 else
            ('LOCK' if score >= threshold else 'WAIT'), 'threshold': threshold,
            'lock_advantage': None if games_remaining == 0 else score - threshold}


def schedule_payload(schedule):
    from src.schedule.calendar import regular_schedule
    frame, notes = regular_schedule(schedule)
    games = []
    if {'home_team', 'away_team'} <= set(frame):
        for row in frame.sort_values('game_date').to_dict('records'):
            games.append({'date': row['game_date'].strftime('%Y-%m-%d'),
                          'home': row['home_team'], 'away': row['away_team']})
    return {'games': games, 'notes': notes, 'tipoffs_verified': False}


def matchup_advice(sample, score, games_remaining, margin, uncertainty, play_probability=1.0, **state):
    """Maximize expected win-equivalent outcome (ties half) in a static matchup.

    Margin excludes the selected player's candidate score and all future scores.
    It includes the expected net outcome of all OTHER players. Their residual
    difference is Normal(0, uncertainty), independent of this player. Neither
    this distribution nor the resulting probabilities are calibrated win odds.
    """
    base = advise(sample, score, games_remaining, play_probability, **state)
    if not math.isfinite(margin) or not math.isfinite(uncertainty) or uncertainty < 0:
        raise ValueError('Matchup margin must be finite and uncertainty nonnegative')
    def utility(x):
        d = margin + x
        return (1 if d > 0 else 0 if d < 0 else .5) if uncertainty == 0 else .5 * (1 + math.erf(d / uncertainty / math.sqrt(2)))
    wait = utility(0)
    factors = state.get('factors')
    factors = [1.] * games_remaining if factors is None else factors
    for k, factor in enumerate(reversed(factors), 1):
        played = sum(utility(x*factor) if k == 1 else max(utility(x*factor), wait) for x in sample) / len(sample)
        wait = play_probability * played + (1 - play_probability) * wait
    lock = utility(score)
    if base['action'] in ('LOCK', 'WAIT'):
        base['action'] = 'LOCK' if lock >= wait else 'WAIT'
    return {**base, 'lock_win_equivalent': lock,
            'wait_win_equivalent': None if games_remaining == 0 else wait,
            'objective': 'head_to_head'}
