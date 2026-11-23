"""Editable, per-field rookie blends; missing stats are never silently zero."""
import math
from collections.abc import Mapping

from src.projections.baseline import RATE_STATS


def resolve_rookie(entry):
    if not isinstance(entry, Mapping) or not entry.get('projection_sources'):
        return entry
    rows = entry['projection_sources']
    ids = [r['source_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate projection provider in rookie blend')
    if any(r['season'] != entry['projection_season'] or r['units'] != 'per_game' for r in rows):
        raise ValueError('Rookie sources must share the target season and per-game units')
    values, counts = {}, {}
    for key in [*RATE_STATS, 'minutes', 'expected_games']:
        field = [float(r['values'][key]) for r in rows if key in r['values']]
        if field:
            if any(not math.isfinite(v) or v < 0 for v in field):
                raise ValueError('Invalid projection number: ' + key)
            values[key], counts[key] = sum(field) / len(field), len(field)
        elif key in entry.get('fallbacks', {}):
            values[key], counts[key] = float(entry['fallbacks'][key]), 0
        else:
            raise ValueError('Missing rookie projection field: ' + key)
    if not 0 < values['minutes'] <= 48 or not 0 <= values['expected_games'] <= 82:
        raise ValueError('Invalid rookie minutes/games')
    if any(not math.isfinite(v) or v < 0 for v in values.values()):
        raise ValueError('Invalid rookie fallback')
    notes = '; '.join(f'{k}: {n} source(s)' if n else f'{k}: explicit fallback {values[k]:g}'
                      for k, n in counts.items())
    return {**entry, 'minutes': values['minutes'], 'expected_games': values['expected_games'],
            'per36': {k: values[k] * 36 / values['minutes'] for k in RATE_STATS},
            'note': entry.get('note', '') + '; Equal-weight per-field blend. ' + notes}
