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
from typing import Sequence

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


def _true_age_multiplier(age: float) -> float:
    """The generator's GROUND-TRUTH aging curve.

    Kept intentionally distinct from config/model.yaml's age_curve so the
    projection backtest measures whether the model approximates a real effect
    rather than whether it can recite one it was handed.
    """
    age = float(age)
    if age < 23:
        return 1.045          # young players improve quickly
    if age < 26:
        return 1.020
    if age < 29:
        return 1.000          # peak
    if age < 32:
        return 0.975
    if age < 35:
        return 0.945
    return 0.900              # steep late decline


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


def generate_multi_season(
    players: pd.DataFrame,
    seasons: Sequence[str] = ("2023-24", "2024-25", "2025-26"),
    seed: int = 20262027,
    year_over_year_drift: float = 0.12,
):
    """Generate several seasons with players who genuinely change year to year.

    Backtesting is only meaningful if the target season is not a copy of the
    training seasons. Each season the underlying talent and minutes drift, so a
    projection has something real to get right or wrong - and a model that merely
    memorises last season cannot score perfectly.
    """
    rng = np.random.default_rng(seed + 7717)
    frames = []
    current = players.copy()
    start_years = {season: int(season.split("-")[0]) for season in seasons}

    for index, season in enumerate(sorted(seasons)):
        frames.append(
            generate_game_logs(
                current,
                season=season,
                season_start=f"{start_years[season]}-10-21",
                seed=seed + index * 101,
            )
        )
        # Drift into the next season. Two components:
        #
        #  (a) a REAL aging effect, so there is a genuine signal for a projection
        #      to capture. Without it, year-over-year change would be pure noise
        #      and NO method could beat a "same as last season" baseline - which
        #      would make the projection backtest structurally unable to
        #      discriminate between a good model and a broken one.
        #
        #  (b) idiosyncratic noise, so the signal is not trivially recoverable.
        #
        # The curve below is deliberately a DIFFERENT shape from the age curve in
        # config/model.yaml (steeper, with a later peak). The model must
        # approximate a real effect it does not know exactly - if the generator
        # used the model's own curve, the backtest would be marking its own
        # homework.
        nxt = current.copy()
        age_effect = np.array([_true_age_multiplier(a) for a in current["age"]], dtype=float)
        nxt["talent"] = np.clip(
            current["talent"] * age_effect * rng.normal(1.0, year_over_year_drift, len(current)),
            0.5, 2.4,
        )
        nxt["base_minutes"] = np.clip(
            current["base_minutes"] * age_effect + rng.normal(0.0, 2.5, len(current)), 8.0, 38.0
        )
        nxt["durability"] = np.clip(
            current["durability"] * (0.5 + 0.5 * age_effect) * rng.normal(1.0, 0.08, len(current)),
            0.4, 0.99,
        )
        nxt["age"] = current["age"] + 1
        current = nxt

    return pd.concat(frames, ignore_index=True)


def write_synthetic_season(
    output_dir,
    season: str = "2025-26",
    n_players: int = 180,
    seed: int = 20262027,
    seasons: Sequence[str] | None = None,
):
    """Write one or more synthetic seasons to disk in the raw-data layout."""
    from pathlib import Path

    players = generate_players(n_players=n_players, seed=seed)
    season_list = list(seasons) if seasons else [season]
    logs = generate_multi_season(players, season_list, seed=seed)

    written = []
    for name in season_list:
        directory = Path(output_dir) / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"SYNTHETIC_game_logs_{name}.csv"
        logs[logs["season"] == name].to_csv(path, index=False)
        written.append(path)

    # Deliberately NOT inside a season directory: everything under
    # data/raw/<season>/ is treated as game logs.
    meta_dir = Path(output_dir).parent / "external"
    meta_dir.mkdir(parents=True, exist_ok=True)
    players.to_csv(meta_dir / "SYNTHETIC_players.csv", index=False)
    return (written[-1] if written else None), players, logs
