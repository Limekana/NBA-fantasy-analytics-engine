# Data sources

Handoff §5: *prioritise reliable and legally accessible public data.*

> ## Read this first
>
> The sandbox this repository was built in **blocks outbound HTTPS** to every NBA
> data host (HTTP 403 on CONNECT from the egress proxy):
>
> - `stats.nba.com`
> - `www.basketball-reference.com`
> - `api.sleeper.app`
> - `support.sleeper.com`
>
> **No real NBA data has been ingested.** The ingestion adapters are written
> against documented endpoint contracts and unit-tested against fixtures, but
> have never executed against the live APIs. Run them from your own machine.
>
> Everything that does not need network access — the scoring engine, the Lock-In
> simulator, projections, the draft board, the live assistant — is fully working
> and tested, and the `demo` command proves the pipeline end-to-end on synthetic
> data.

---

## Getting real data in

### Option A — `nba_api` (recommended)

```bash
pip install nba_api
python -m src.cli ingest --season 2025-26 --source nba_api
python -m src.cli ingest --season 2024-25 --source nba_api
python -m src.cli ingest --season 2023-24 --source nba_api
```

Writes to `data/raw/<season>/game_logs_<season>.csv` plus a
`.manifest.json` recording the source, retrieval timestamp and row count
(Engineering Rule 6).

**Sanity check the first run:** a full NBA season is roughly **26,000**
player-game rows. If you get a few hundred, the endpoint returned a partial
response — do not build a board on it.

`stats.nba.com` rate-limits aggressively and rejects requests without browser-like
headers; `nba_api` handles both. The adapter sleeps 0.75s between calls
(`config/sources.yaml` → `rate_limit_seconds`).

**Fields provided:** everything the scoring engine needs (PTS, REB, AST, STL,
BLK, TOV, PF, FTM, FG3M) plus FGA/FTA/FG3A/OREB/DREB for role work.
**Not provided:** a starter flag. `PlayerGameLogs` omits it; getting it would
mean ~1,300 extra `boxscoretraditionalv2` calls per season. Minutes are a better
role proxy and are already present, so the adapter sets `started` to null.

### Option B — any CSV (no API access needed)

The pipeline reads any CSV or Parquet dropped into `data/raw/<season>/`. Column
names are mapped case-insensitively through a synonym table
(`src/ingestion/csv_source.py`), so exports from Basketball Reference, a Kaggle
dump, or a manual scrape all load without editing the file — which Engineering
Rule 2 forbids anyway.

Recognised spellings include:

| Canonical | Also accepted |
|---|---|
| `points` | `PTS` |
| `rebounds` | `REB`, `TRB` |
| `assists` | `AST` |
| `turnovers` | `TOV`, `TO` |
| `personal_fouls` | `PF`, `fouls` |
| `free_throws_made` | `FTM`, `FT` |
| `three_pointers_made` | `FG3M`, `3P`, `TPM` |
| `minutes` | `MIN`, `MP` — accepts `34:12` as well as `34.2` |
| `game_date` | `DATE`, `GAME_DATE` |
| `opponent` | `OPP`, or derived from `MATCHUP` |

Then:

```bash
python -m src.cli build-board --seasons 2025-26 2024-25 2023-24
```

### Player metadata (age and position)

Ages drive the age curve and the availability baseline; positions drive scarcity.
Game logs alone carry neither. Either:

```bash
# from Sleeper (needs network) - also gives injury status
python -c "from src.ingestion.sleeper_source import SleeperSource; \
SleeperSource().fetch_players().to_csv('data/external/players.csv', index=False)"
```

or supply your own CSV with `player_name`, `team`, `position`, `age` and pass
`--players data/external/players.csv`. Without ages the model defaults everyone
to 27 and says so in `assumption_notes`.

### Schedule

**This is the highest-leverage missing input.** Lock-In value depends directly on
games per fantasy week, and without the real schedule the model falls back to
historical cadence (or a flat PMF), which cannot distinguish a player on a
4-game-week-heavy schedule from one who is not.

```bash
python -c "from src.ingestion.nba_api_source import NBAScheduleSource; \
NBAScheduleSource().fetch_schedule('2026-27').to_csv('data/external/schedule_2026-27.csv', index=False)"

python -m src.cli build-board --schedule data/external/schedule_2026-27.csv
```

The board's `used_real_schedule` column records, per player, whether real
schedule data was used.

### ADP

Handoff §14: **never treat one source as truth**, and keep ADP separate from
expert rankings — ADP is a *market price*, a ranking is an *opinion*. Blending
them destroys the only signal that finds an inefficiency.

Drop CSVs into `data/external/adp/` with at least `player_name` and `adp`, then
register each one in `config/sources.yaml`:

```yaml
adp:
  sources:
    - name: "source_a"
      file: "data/external/adp/source_a_2026-08-28.csv"
      retrieved_at: "2026-08-28T12:00:00Z"
      league_format: "10-team"
      scoring_format: "sleeper_points_lockin"
```

Multiple sources are combined by **median** (resistant to one outlier), and the
spread between them is reported as `adp_spread` — a wide spread means the
player's draft-day cost is genuinely uncertain.

**A caution specific to this league:** almost all published NBA ADP is for
9-category or standard points leagues, *not* Sleeper Lock-In with these bonuses.
That mismatch is not noise, it is the edge — but it also means ADP tells you what
the field will do, not what is correct. Record `scoring_format` honestly.

---

## Licensing

- **stats.nba.com** — public endpoints, no key. Personal/non-commercial use.
  Do not redistribute bulk data.
- **Sleeper API** — public, unauthenticated, no key required.
- **Basketball Reference** — check their terms before scraping; they ask for
  rate limiting and prohibit bulk redistribution. Manual CSV export is fine.

`data/raw/` is gitignored: raw data stays local and immutable (Rule 2).
