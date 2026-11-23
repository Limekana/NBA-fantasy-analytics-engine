"""Small, serializable league facts. Analytics are derived elsewhere."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class Team:
    team_id: str
    manager: str
    draft_slot: int
    owner_id: str = ''
    display_name: str = ''


@dataclass(frozen=True)
class Transfer:
    player_id: str
    from_team: str | None
    to_team: str | None


@dataclass(frozen=True)
class LeagueEvent:
    event_id: str
    sequence: int
    kind: str
    transfers: tuple[Transfer, ...] = ()
    occurred_at: str | None = None
    recorded_at: str | None = None
    source: str = 'manual'
    source_id: str = ''
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value['transfers'] = tuple(Transfer(**x) for x in value.get('transfers', []))
        return cls(**value)


@dataclass
class LeagueState:
    league_id: str
    teams: dict[str, Team]
    ownership: dict[str, str] = field(default_factory=dict)
    reserve: dict[str, set[str]] = field(default_factory=dict)
    reserve_known: bool = False
    last_sequence: int = 0
    verified: bool = False
    notes: list[str] = field(default_factory=list)
    observed_at: str | None = None

    def roster(self, team_id, active_only=False):
        return sorted(pid for pid, owner in self.ownership.items() if owner == team_id
                      and (not active_only or pid not in self.reserve.get(team_id, set())))

    def team(self, name_or_id):
        value = str(name_or_id).casefold()
        found = [t for t in self.teams.values() if value in
                 {t.team_id.casefold(), t.manager.casefold(), t.display_name.casefold()}]
        if len(found) != 1:
            raise ValueError(f'Unknown or ambiguous team: {name_or_id}')
        return found[0]

    def free_agents(self, catalogue):
        return sorted(set(catalogue.players) - set(self.ownership))
