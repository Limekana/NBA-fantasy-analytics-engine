# NBA Fantasy Analytics Engine

Quantitative draft and valuation system for a **10-team Sleeper Fantasy
Basketball league in Lock-In mode**, 2026-27 season.

Player value is computed from the league's actual scoring settings, game-level
historical data, projected roles, schedule, availability, bonus frequency and
Lock-In mechanics — not from generic fantasy rankings.

---

## Status

| Component | State |
|---|---|
| Docker image | Built and published by CI on tag |
| Backtesting | Working — projection + Lock-In strategy backtests |
| Rookie projections | 2026 class configured, 4 flagged for verification |
| Configuration system | Working, validated |
| Fantasy scoring engine | Working, **191 tests passing** |
| Bonus interaction rules | Working; one assumption **needs your confirmation** (below) |
| Lock-In simulator | Working — optimal-stopping model |
| Distributions, archetypes | Working |
| Projections, games-played model | Working |
| Draft board + tiers + VOR | Working |
| Monte Carlo draft simulator | Working |
| Live draft assistant | Working |
| Interactive HTML board | Working |
| nba_api adapter contracts | Verified against the installed package schema |
| **Real NBA data ingested** | **No — network blocked in the build environment** |

**The pipeline is complete and tested end to end. It has not yet been run on real
NBA data**, because every NBA data host was blocked by this environment's egress
policy (403 on `stats.nba.com`, `basketball-reference.com`, `api.sleeper.app`,
`support.sleeper.com`). Ingestion adapters are written and unit-tested; run them
from your own machine. See [`docs/data_sources.md`](docs/data_sources.md).

---

## Two things to do before the draft

### 1. Confirm the 50-point bonus rule (2 minutes)

Sleeper's docs could not be reached from the build environment, so **one scoring
rule is an unverified assumption**: whether a 50-point game also collects the 40+
bonus. The model currently assumes it does (7 bonus points, not 4).

```bash
python -m src.cli scoring-check
```

Compare the line marked `50 pts exactly  <-- KEY TEST` against your league's
Sleeper scoring. If it disagrees, set
`bonus_rules.points_thresholds_stack: false` in `config/league.yaml` and re-run.
Both branches are already tested. Details:
[`docs/lock_in_mechanics.md`](docs/lock_in_mechanics.md#3-40--50-point-stacking--unverified-action-required).

### 2. Verify the league settings themselves

The handoff calls the settings provisional. Every valuation is downstream of
them. Once the league exists, put its id in `config/league.yaml`
(`meta.sleeper_league_id`) and run:

```bash
python -m src.cli verify-league
```

This diffs Sleeper's live scoring settings against the YAML.

---

## Cutting a release

The container is built and published by CI, not by hand.

```bash
./scripts/release.sh v0.1.0
```

Or, entirely from a phone: github.com → **Actions** → **Release** → *Run
workflow* → type the tag. CI runs the tests, builds for amd64 and arm64,
publishes to `ghcr.io`, and attaches an offline tarball to the release.

## Run it with Docker (recommended for draft day)

```bash
docker pull ghcr.io/limekana/nba-fantasy-analytics-engine:latest

mkdir -p config data outputs
docker create --name tmp ghcr.io/limekana/nba-fantasy-analytics-engine:latest \
  && docker cp tmp:/app/config ./ && docker rm tmp

alias nba='docker run --rm -v "$PWD/config:/app/config" -v "$PWD/data:/app/data" \
  -v "$PWD/outputs:/app/outputs" ghcr.io/limekana/nba-fantasy-analytics-engine:latest'

nba check-config
nba data-help
```

`config/` is bind-mounted, so editing `config/assumptions.yaml` when news breaks
takes effect on the next command — no rebuild. Everything except `ingest` and
`verify-league` runs fully offline, which is what you want at a draft table.

Or with compose: `docker compose run --rm engine build-board`.

## Quick start (local Python)

```bash
pip install -r requirements.txt

python -m src.cli check-config     # validate config, show resolved scoring rules
python -m src.cli scoring-check    # reference stat lines to check against Sleeper
python -m src.cli data-help        # where to get data, and the exact CSV format
python -m src.cli demo             # full pipeline on synthetic data, no network
python -m src.cli backtest         # validate the model against naive baselines
pytest -q                          # 260 tests
```

`demo` writes `outputs/SYNTHETIC_draft_board.{csv,html}`. **The synthetic board
is fictional** — generated players, not real ones — and is labelled as such in
every output. It exists to prove the pipeline works, not to draft from.

### With real data

```bash
pip install nba_api
python -m src.cli ingest --season 2025-26 --source nba_api
python -m src.cli ingest --season 2024-25 --source nba_api
python -m src.cli ingest --season 2023-24 --source nba_api

python -m src.cli build-board --players data/external/players.csv \
                              --schedule data/external/schedule_2026-27.csv
```

No API access? Drop any CSV into `data/raw/<season>/` — column names are mapped
automatically from Basketball Reference / Kaggle / nba_api spellings.

### On draft day

```bash
# who is likely to reach each of your picks
python -m src.cli availability --slot 4

# on the clock at pick 17
python -m src.cli draft --pick 17 --slot 4 --drafted drafted.txt --roster PG,C
```

The assistant always shows its arithmetic — never a bare "pick this player".

---

## Is the 37% rule right for Lock-In? No — and the code shows why

The 37% (secretary) rule is genuinely optimal, but for a different problem:

| | Secretary problem | Lock-In week |
|---|---|---|
| Objective | maximise P(picking the single best) | maximise **expected points** |
| Payoff | all-or-nothing; 2nd best scores zero | 2nd-best game scores nearly as much |
| Distribution | unknown; only relative ranks | **known** — the player's own game log |
| n | large | 2–4 |

The third row is decisive: we are not guessing in the dark. Knowing a player's
scoring distribution lets us compute the exact value of continuing, which the 37%
rule throws away. The small `n` makes it worse — with 3.4 games a week,
`floor(n/e) = 1`, and in a 2-game week `floor(2/e) = 0`, so it locks Monday
unconditionally.

Replayed over real chronological weeks (`python -m src.cli backtest`):

```
strategy       fp_per_week   share of available edge
optimal_iid         19.55                     100%
percentile          19.40                      95%
secretary (37%)     18.05                      49%
threshold           17.18                      17%
last_game           16.63                       0%
```

The 37% rule beats doing nothing but captures under half the available edge.
`optimal_iid` — lock when the game in hand beats `W[k] = E[max(F, W[k-1])]` —
is what the model uses.

## What makes this a Lock-In model, not a fantasy ranking

In Lock-In **only one game per player counts each week**. You lock a completed
performance before that player's next game; if you never lock, Sleeper takes
their **final game of the week**. That single rule changes the arithmetic of
player value completely:

| Ordinary fantasy | Lock-In |
|---|---|
| Value ≈ FP/game × games played | Value ≈ Σ over weeks of E[best *chosen* game] |
| Consistency is prized | **Variance is an asset** |
| An extra game adds points | An extra game adds an **option** |
| Schedule is a tiebreak | Games-per-week is a first-order value driver |

Two players averaging an identical 35.0 FP/game differ by **~5.8 FP per week**
purely because one is volatile — the week keeps the good draw and discards the
bad ones. That result is a test
(`test_volatility_is_an_asset_in_lock_in`), not a claim.

The model reports the whole decision spectrum rather than one number:

| Column | Meaning |
|---|---|
| `lockin_auto` | You never lock. Sleeper takes the last game. **Lower bound.** |
| `lockin_value` | Optimal stopping with no knowledge of the future. **The realistic estimate.** |
| `lockin_perfect` | Clairvoyance. **Explicitly an upper bound**, never a projection. |
| `lock_in_advantage` | `lockin_value` − raw FP/game. The Lock-In edge. |

`lockin_value` uses backward induction over the player's own empirical
distribution: with `k` games left, continuing is worth `W[k] = E[max(F, W[k-1])]`,
so you lock when the game in hand beats that. Early in the week you should pass
on a good game; by Saturday the same score is a lock.

---

## Design decisions worth knowing

**Bonuses are non-linear, so the average stat line is not scored.** Projecting a
per-game line and pushing it through the scoring engine silently prices bonuses
near zero — a player averaging 26 points never crosses 40 on their *average*
line but does in real games. The linear terms are computed from the projected
line; expected bonus points are projected separately from historical bonus rates.
See `src/valuation.py`.

**Tiers are cut at gaps, not every N players.** A tier should mean "these players
are interchangeable"; the useful information is where that stops being true.
Cuts fall where the value drop exceeds ~1 SD of the local gap distribution.

**Archetypes are discovered, not defined.** k-means over standardised production
and distribution shape, labelled afterward by whichever feature each cluster is
most extreme on. No archetype is assumed valuable.

**Availability reduces usable weeks, not a season point total.** Missing one game
of a four-game week costs almost nothing in Lock-In. Missing the week costs
everything.

**Facts and assumptions are kept apart.** `src/distributions/` produces measured
facts; `src/projections/` produces assumptions; every player carries an
`assumption_notes` column explaining what was applied to them.

---

## Project layout

```
config/
  league.yaml        league settings + scoring + bonus interaction rules
  model.yaml         every tunable model parameter
  assumptions.yaml   per-player injury / role / rookie overrides (edit as news breaks)
  sources.yaml       data source registry + ADP provenance
src/
  config.py          config loading and validation
  schemas.py         canonical dataframe schemas
  scoring/           deterministic scoring engine
  ingestion/         nba_api, Sleeper, CSV, synthetic adapters
  distributions/     player profiles, percentiles, bonus rates, archetypes
  projections/       per-36 blend, age curve, games-played, rookie priors
  lockin/            Lock-In strategies and simulator
  schedule/          fantasy weeks, games-per-week
  adp/               multi-source ADP, name matching, value gaps
  draft/             board, tiers, VOR, Monte Carlo simulator, live assistant
  reporting/         interactive HTML board
  valuation.py       combines everything into a player valuation
  backtest.py        projection + Lock-In backtests, weight tuning
  pipeline.py        the one reproducible path from raw data to board
tests/               260 tests
docs/
  lock_in_mechanics.md   what was verified vs assumed, with sources
  lockin_strategy.md     how to play Lock-In, measured (incl. the 37% rule)
  data_sources.md        how to get real data in
  assumptions.md         full assumption register + known gaps
  schemas.md             data contracts
outputs/               draft_board.csv / .html, player_values.csv
```

---

## Engineering rules (from the handoff)

1. **Everything configurable.** No hardcoded scoring values — enforced by tests.
2. **Raw data immutable.** `data/raw/` is written once, never edited.
3. **Every transformation reproducible.** `src/pipeline.py` is the only path to `outputs/`.
4. **Test scoring before projections.** A perfect projection with wrong scoring is worthless.
5. **No ML before a baseline.** Transparent weighted model; no neural networks.
6. **Track timestamps.** Every ingest writes a provenance manifest.
7. **Separate facts from assumptions.** Enforced by module boundaries and surfaced per player.

---

## Honest limitations

- **No real data has been through this yet.** Ingestion is untested against live APIs.
- **The projection model has not yet beaten a naive baseline.** Backtesting is now
  implemented, and on synthetic data the model ranks players about as well as
  "last season's FP/game" and no better. That is the honest current state — and
  it is what handoff Rule 5 asks you to confront rather than paper over. Re-run
  `backtest --tune` on real data before trusting model rank over the baseline.
- **Rookie projections are pure prior.** Four of the eight configured 2026
  rookies have landing spots from single or unconfirmed sources and are flagged
  `VERIFY` in the pipeline warnings.
- **No ADP is loaded**, so market-inefficiency analysis is unavailable until you
  add sources — the board currently ranks on model value alone.
- **The real 2026-27 schedule is not loaded**, so Lock-In values use historical
  weekly cadence. This is the highest-leverage missing input.

Full detail in [`docs/assumptions.md`](docs/assumptions.md).
