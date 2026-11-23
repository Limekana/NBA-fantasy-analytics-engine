# Season data archive

The archive keeps real Sleeper league outcomes and NBA player-game statistics
for later model review. The Sleeper API is read-only and has public endpoints
for league, roster, transaction, draft and weekly matchup data. See the
[Sleeper API documentation](https://docs.sleeper.com/).

## Capture or refresh a season

From the repository root:

```powershell
python -m src.cli league-archive
```

The default captures weeks 1 through the current NBA fantasy week. To backfill
or explicitly cap the range:

```powershell
python -m src.cli league-archive --through-week 22
```

It is safe to run again. Every run creates a timestamped, checksummed snapshot;
`current.json` points to the latest complete snapshot. If run during a live week,
the partial score is preserved as an observation. Run it again after that week
finishes to preserve the final totals. The command serializes with dashboard and
performance refreshes through `refresh.lock`.

## Stored data

For league `1374183914238648320`, the archive lives under
`data/league/1374183914238648320/seasons/<NBA season>/snapshots/<observation>/`:

- `sleeper/league.json`, `users.json`, `drafts.json`, `draft.json`, `picks.json`,
  `transactions.json`, and `rosters.json` preserve the league and all ten teams.
- `sleeper/matchups/week-NN.json` preserves each raw weekly response.
- `sleeper/team_week_results.csv` has one row per team and week, with matchup,
  starters, bench, Sleeper team total and the sum of starter scores.
- `sleeper/player_week_results.csv` has the Sleeper weekly fantasy score and
  starter status for every player on each archived team roster.
- `sleeper/player_week_score_reconciliation.csv` compares each Sleeper score
  with available NBA game scores in the configured fantasy week and records
  candidate game IDs. It flags mismatches; it never substitutes a guessed score.
- `sleeper/nba_state.json` and `sleeper_player_catalogue` record season state and
  the full player catalogue. The large player catalogue is stored once per day
  under the league's immutable `raw/` archive and referenced by path and hash.
- `nba/player_game_logs_api_response.csv` preserves the unmodified NBA API
  response. `nba/fantasy_player_game_logs.csv` adds fantasy points calculated
  with the league's configured scoring and bonus rules for every NBA player game.
- `nba/sleeper_nba_player_crosswalk.csv` carries birth dates and links only
  unique normalized names; ambiguous names stay unmatched rather than guessed.
- `archive_manifest.json` records paths, source URLs, row counts and SHA-256
  hashes. The season-level `current.json` validates the manifest hash.

The canonical unmodified NBA game-log snapshots also live under
`data/raw/<NBA season>/snapshots/`; that directory's `current.json` makes the
latest validated logs available to the existing model and backtest loaders.
The archive checks that old player-games are not removed by a later API response.
The original NBA response is kept unchanged. The canonical logs may include
duplicate-identity repairs and stat corrections supported by NBA's per-game
box score; each correction is listed in the manifest.

Sleeper's live matchup response includes per-player weekly point totals, team
totals and lineup IDs. It does not provide the NBA game ID or lock timestamp for
each weekly player score, so the archive records actual weekly outcomes without
claiming to reconstruct a manager's exact lock decision.

## Use the data next offseason

The default `backtest` command discovers available seasons from `data/raw/` and
uses the newest season with a substantial 20-game player sample as its target.
It avoids treating the ongoing season as a completed holdout. Run:

```powershell
python -m src.cli backtest --diagnose
```

After updating `config/league.yaml` to the next league season, the board builder,
league forecast and performance refresh select the latest three available
seasons before that target season. The current `blend` projection method uses
each player's latest available season's rates, with age adjustment and
shrinkage. If you switch to `model`, review and update `config/model.yaml` season weights deliberately;
the archive does not silently change those assumptions. Compare model and
baseline results before changing the production method or weights.

The NBA game logs are the training and player-game backtest evidence. During a
season build, the configured target season is excluded from its own training;
rates train on completed seasons through 2025-26, and the archived 2026-27
logs then update minutes and per-minute production through the validated
in-season blend (see docs/assumptions.md). Run `league-archive`, then
`league-build`, to add newer games. Once `config/league.yaml` advances to the
next season, discovery includes this season's real logs; `blend` uses each
player's newest available season, while `model` uses configured multi-season
weights. The Sleeper weekly scores and team results are preserved as real
league outcomes, but the current projection backtest does not train on those
weekly rows. They are not per-game fantasy scores and Sleeper does not include
the selected NBA game or lock timestamp. The current build also does not keep
an immutable pre-week forecast, so those outcomes cannot yet support a clean
historical test of the dashboard's weekly team forecasts.

These files are ignored by git under `data/raw/` and `data/league/`. The archive
command can backfill all Sleeper weeks and current NBA logs when run later, but
regular captures also preserve season-time roster and transaction observations.
Back up the data directory if you want to preserve it across machine changes or
repository replacements.
