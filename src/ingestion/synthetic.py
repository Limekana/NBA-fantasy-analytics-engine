"""Synthetic game log generator.

>>> THIS IS NOT REAL DATA. IT IS NOT A PROJECTION. DO NOT DRAFT FROM IT. <<<

Its only purposes are (a) letting the full pipeline run end-to-end in CI and on a
machine with no NBA API access, and (b) giving the tests a realistic-shaped
dataset with known ground truth.

Everything it produces is stamped ``is_synthetic=True``, that flag is propagated
through every downstream table, and the reporting layer refuses to render a
draft board without a prominent warning banner when it is set.

The generator is *shaped* like real basketball - correlated stats, minute-driven
production, skewed scoring, realistic double-double and 40-point rates - so that
the model's behaviour under it is informative about the model, not about the
generator's artefacts. It is still fiction.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TEAMS = [
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
]

POSITIONS = ["PG", "SG", "SF", "PF", "C"]


@dataclass(frozen=True)
class Archetype:
    """Per-36-minute production profile for a player type."""

    name: str
    points: float
    rebounds: float
    assists: float
    steals: float
    blocks: float
    turnovers: float
    personal_fouls: float
    ft_rate: float          # FTM per point
    three_rate: float       # 3PM per point
    volatility: float       # multiplicative game-to-game noise


ARCHETYPES: dict[str, Archetype] = {
    "PG": Archetype("floor_general", 21.0, 4.2, 8.5, 1.5, 0.3, 3.0, 2.0, 0.16, 0.11, 0.30),
    "SG": Archetype("scorer",        22.5, 4.0, 3.6, 1.2, 0.4, 2.3, 2.2, 0.17, 0.13, 0.32),
    "SF": Archetype("wing",          20.0, 6.0, 3.8, 1.2, 0.6, 2.2, 2.3, 0.16, 0.11, 0.30),
    "PF": Archetype("big_forward",   18.5, 8.5, 3.0, 1.0, 1.1, 2.0, 2.6, 0.15, 0.07, 0.28),
    "C":  Archetype("center",        17.5, 11.0, 2.8, 0.8, 1.8, 2.1, 2.9, 0.16, 0.03, 0.28),
}


def generate_players(n_players: int = 180, seed: int = 20262027) -> pd.DataFrame:
    """Create a synthetic player pool with a realistic talent distribution."""
    rng = np.random.default_rng(seed)
    rows = []
    for index in range(n_players):
        position = POSITIONS[index % len(POSITIONS)]
        # Talent is lognormal-ish: a few stars, a long tail of role players.
        talent = float(np.clip(rng.lognormal(mean=0.0, sigma=0.34), 0.55, 2.3))
        minutes = float(np.clip(rng.normal(26.0 + 6.0 * (talent - 1.0), 5.0), 10.0, 37.5))
        rows.append(
            {
                "player_id": f"SYN{index:04d}",
                "player_name": f"Synthetic Player {index:03d}",
                "team": TEAMS[index % len(TEAMS)],
                "position": position,
                "positions": position,
                "age": int(rng.integers(19, 38)),
                "talent": talent,
                "base_minutes": minutes,
                "durability": float(np.clip(rng.beta(6, 1.6), 0.45, 0.99)),
                # Versatility creates genuine stat-stuffers. Without it every
                # category is an independent draw, no player ever posts 10/10/10,
                # and the triple-double archetype the model is meant to discover
                # simply does not exist in the data.
                "versatility": float(np.clip(rng.beta(1.7, 5.0), 0.0, 1.0)),
                "volatility_mult": float(np.clip(rng.normal(1.0, 0.22), 0.6, 1.7)),
                "is_synthetic": True,
            }
        )
    return pd.DataFrame(rows)


def generate_game_logs(
    players: pd.DataFrame,
    season: str = "2025-26",
    n_team_games: int = 82,
    season_start: str = "2025-10-21",
    seed: int = 20262027,
) -> pd.DataFrame:
    """Generate a season of game logs on a realistic ~3.4-games-per-week cadence."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(season_start)

    # Game dates per team: spread n_team_games over ~25 weeks, avoiding
    # back-to-back-to-backs, so that reconstructed fantasy weeks look real.
    team_dates: dict[str, list[pd.Timestamp]] = {}
    for offset, team in enumerate(TEAMS):
        dates, cursor = [], start + pd.Timedelta(days=int(offset % 3))
        while len(dates) < n_team_games:
            dates.append(cursor)
            cursor = cursor + pd.Timedelta(days=int(rng.choice([1, 2, 2, 3], p=[0.28, 0.42, 0.20, 0.10])))
        team_dates[team] = dates

    records = []
    for player in players.itertuples(index=False):
        archetype = ARCHETYPES[player.position]
        dates = team_dates[player.team]
        n_played = int(round(len(dates) * player.durability))
        # Missed games cluster (injuries are streaks, not coin flips).
        played_indices = _sample_played_games(len(dates), n_played, rng)

        for game_index in played_indices:
            minutes = float(np.clip(rng.normal(player.base_minutes, 4.2), 0.0, 44.0))
            if minutes < 5:
                continue
            scale = minutes / 36.0
            noise = archetype.volatility * player.volatility_mult

            # Talent is applied with an exponent < 1: linear scaling produced
            # 44-PPG players. Real NBA tops out near 34 PPG.
            talent_effect = player.talent ** 0.7
            # A versatile player's weak categories are pulled toward a balanced
            # line, which is what produces correlated big games and triple-doubles.
            balanced = (archetype.points / 2.2 + archetype.rebounds + archetype.assists) / 3.0
            reb_base = archetype.rebounds + player.versatility * (balanced - archetype.rebounds)
            ast_base = archetype.assists + player.versatility * (balanced - archetype.assists)

            points = _draw(rng, archetype.points * scale * talent_effect, noise, skew=1.15)
            rebounds = _draw(rng, reb_base * scale * (0.7 + 0.3 * player.talent), noise * 0.85)
            assists = _draw(rng, ast_base * scale * talent_effect, noise)
            steals = _draw(rng, archetype.steals * scale, noise * 1.1)
            blocks = _draw(rng, archetype.blocks * scale, noise * 1.2)
            turnovers = _draw(rng, archetype.turnovers * scale * (0.7 + 0.3 * talent_effect), noise * 0.7)
            fouls = _draw(rng, archetype.personal_fouls * scale, noise * 0.5)

            ftm = _draw(rng, points * archetype.ft_rate, noise * 0.9)
            fg3m = _draw(rng, points * archetype.three_rate, noise)

            opponent = TEAMS[(TEAMS.index(player.team) + 1 + game_index) % len(TEAMS)]
            records.append(
                {
                    "player_id": player.player_id,
                    "player_name": player.player_name,
                    "season": season,
                    "game_id": f"{season}_{player.team}_{game_index:03d}",
                    "game_date": dates[game_index],
                    "team": player.team,
                    "opponent": opponent,
                    "home": bool(game_index % 2),
                    "started": bool(minutes >= 24),
                    "minutes": round(minutes, 1),
                    "points": points,
                    "rebounds": rebounds,
                    "assists": assists,
                    "steals": steals,
                    "blocks": blocks,
                    "turnovers": turnovers,
                    "personal_fouls": min(fouls, 6),
                    "free_throws_made": ftm,
                    "three_pointers_made": min(fg3m, max(0, points // 3)),
                    "is_synthetic": True,
                }
            )

    return pd.DataFrame(records)


def _sample_played_games(n_games: int, n_played: int, rng: np.random.Generator) -> list[int]:
    """Choose which games a player appears in, with injuries as contiguous blocks."""
    if n_played >= n_games:
        return list(range(n_games))
    missed = n_games - n_played
    out_indices: set[int] = set()
    while len(out_indices) < missed:
        # Mostly short absences, occasionally a long one.
        length = int(rng.choice([1, 2, 3, 6, 12], p=[0.42, 0.24, 0.16, 0.12, 0.06]))
        start = int(rng.integers(0, n_games))
        out_indices.update(range(start, min(start + length, n_games)))
    return [i for i in range(n_games) if i not in out_indices]


def _draw(rng: np.random.Generator, mean: float, noise: float, skew: float = 1.0) -> int:
    """Draw a non-negative integer stat with right skew.

    Real box score stats are right-skewed - the gamma shape reproduces the fat
    upper tail that drives 40-point games and makes Lock-In valuable.
    """
    if mean <= 0.01:
        return 0
    shape = max(0.6, (1.0 / max(noise, 0.05) ** 2) / skew)
    scale = mean / shape
    return int(round(float(rng.gamma(shape, scale))))


def write_synthetic_season(
    output_dir, season: str = "2025-26", n_players: int = 180, seed: int = 20262027
):
    """Write a synthetic season to disk in the raw-data layout."""
    from pathlib import Path

    directory = Path(output_dir) / season
    directory.mkdir(parents=True, exist_ok=True)
    players = generate_players(n_players=n_players, seed=seed)
    logs = generate_game_logs(players, season=season, seed=seed)
    path = directory / f"SYNTHETIC_game_logs_{season}.csv"
    logs.to_csv(path, index=False)
    players.to_csv(directory / f"SYNTHETIC_players_{season}.csv", index=False)
    return path, players, logs
