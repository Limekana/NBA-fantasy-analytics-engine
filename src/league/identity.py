"""Stable identity; names are lookup hints and never ownership keys."""
from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
import math

from src.adp import normalise_name


def clean_id(value) -> str:
    if value is None or str(value).strip().lower() in ('', 'nan', 'none', '<na>'):
        return ''
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def positions_of(value) -> tuple[str, ...]:
    if isinstance(value, (tuple, list)):
        return tuple(dict.fromkeys(str(p) for p in value if p))
    return tuple(dict.fromkeys(clean_id(value).replace(',', '|').split('|'))) if clean_id(value) else ()


@dataclass(frozen=True)
class PlayerIdentity:
    player_id: str
    name: str
    sleeper_id: str = ''
    nba_id: str = ''
    positions: tuple[str, ...] = ()
    team: str = ''
    status: str = ''
    injury_status: str = ''


class PlayerCatalog:
    def __init__(self, players, aliases=None):
        self.players = {}
        self.names = defaultdict(set)
        for p in players:
            if not p.player_id or p.player_id in self.players:
                raise ValueError(f'Duplicate/empty player ID: {p.player_id}')
            self.players[p.player_id] = p
            self.names[normalise_name(p.name)].add(p.player_id)
        for alias, pid in (aliases or {}).items():
            if pid not in self.players:
                raise ValueError(f'Alias references unknown player: {alias} -> {pid}')
            key = normalise_name(alias)
            if self.names[key] and pid not in self.names[key]:
                raise ValueError(f'Alias conflicts with another player: {alias}')
            self.names[key].add(pid)

    @classmethod
    def from_frame(cls, frame, aliases=None):
        players = []
        for r in frame.to_dict('records'):
            sid = clean_id(r.get('sleeper_id'))
            if not sid:
                raise ValueError('Player catalogue requires Sleeper IDs')
            players.append(PlayerIdentity(
                'sleeper:' + sid, str(r['player_name']), sid,
                clean_id(r.get('nba_id')), positions_of(r.get('positions') or r.get('position')),
                clean_id(r.get('team')), clean_id(r.get('status')), clean_id(r.get('injury_status')),
            ))
        return cls(players, aliases)

    def resolve(self, name_or_id):
        value = str(name_or_id).strip()
        if value in self.players:
            return self.players[value]
        if 'sleeper:' + value in self.players:
            return self.players['sleeper:' + value]
        candidates = self.names.get(normalise_name(value), set())
        if len(candidates) != 1:
            detail = ', '.join(sorted(candidates)) or 'no exact match'
            raise ValueError(f'Unresolved or ambiguous player {value!r}: {detail}; supply an ID')
        return self.players[next(iter(candidates))]
