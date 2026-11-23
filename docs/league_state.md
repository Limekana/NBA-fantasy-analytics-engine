# Post-draft league management

## Run it

### Refresh button (Windows)

Double-click `Open League Dashboard.cmd` in the repository root. It starts the
loopback-only service in a hidden window if needed, then opens
`http://127.0.0.1:8765/`. Click **Refresh from Sleeper** there. The original HTML
file remains usable offline; its button opens the refresh-enabled local desk.
The launcher must be run again after restarting your computer. It does not
install an automatic startup task or expose the service on your network.

The button detects the current league leg, reads completed transactions and
current rosters/metadata, verifies replay, and recalculates profiles, positional
needs, partner screens and the free-agent pool. Previous trade suggestions are
cleared when refreshing. A change summary shows additions, drops and IR changes.
It uses existing performance projections; it does not scrape new NBA game logs,
invent missing projections or infer medical clearance from a blank injury flag.
An NBA-team change or missing projection may require `league-build`; affected
analytics remain explicitly blocked rather than inventing a player value.

Refresh runs in staging first. Network, reconciliation, forecast-validation or
rendering errors preserve the old event log, observation pointer and dashboard.
Publication errors attempt to restore their saved bytes. Immutable source
archives may remain after a failed publish. Process interruption during file
publication is not a database transaction: inspect the saved event/observation
checksums if interrupted. A refresh lock prevents overlapping imports; do not
delete a leftover lock until confirming that no refresh is still running.

The service binds only `127.0.0.1`, serves only the dashboard and four fixed API
routes, rejects unexpected Host/Origin headers, and requires a per-process
token for refresh. There is no generic file access, shell-command endpoint or
CORS permission. Duplicate in-progress clicks share a job; completed attempts
have a 30-second cooldown. GET requests never initiate a sync. All upstream
Sleeper requests are read-only. The page polls only during a requested refresh.

**Refresh NBA performance** is a separate POST route (`/api/refresh-performance`)
with the same Host/Origin/token/empty-body checks. It shares the job controller,
cooldown and `refresh.lock` with Sleeper refresh. It fetches the configured training
seasons, not rosters or schedule updates, using bounded NBA request timeouts.
Empty/partial responses, missing or nonfinite scores, unexpected seasons, future
dates, synthetic data and omitted previously loaded player-games fail closed.

Downloads go to immutable `data/raw/<season>/snapshots/<uuid>.csv` plus manifests.
Each season's checksummed `current.json` selects its successful snapshot, overriding
the original legacy files without deleting them or concatenating duplicate downloads.
The complete forecast, advisor and HTML are built in staging. Raw pointers and
outputs publish together with attempted rollback on errors; failed immutable
archives may remain. This is not power-loss-safe database atomicity. Do not clear
a leftover lock without checking the service and saved checksums. CLI writers must
not run concurrently; guarded inputs/outputs are checked for changes before publishing.

The button preserves roster-refresh summaries and shows NBA download time separately
from the latest game date and forecast generation. Changing training-season weights,
rookie priors or the schedule still needs reviewed configuration/input changes;
it never fabricates a new projection source. `refresh_performance(download=False)`
is the internal safe rebuild path for code upgrades using already reviewed inputs;
it preserves the last download timestamp instead of claiming a fresh fetch.

For manual service use: `python -m src.cli league-serve --port 8765`.
Stop that foreground service with Ctrl+C. A launcher-started service runs until
its Python process exits or the computer restarts; its diagnostics are in
`outputs/refresh-service.log` and `outputs/refresh-service-errors.log`.

### Weekly forecast log

Run this before each fantasy week starts (Monday, before the first game):

```powershell
python -m src.cli league-weekly
python -m src.cli league-forecast-report
```

`league-weekly` refreshes Sleeper, archives new NBA games, rebuilds the forecast
and dashboard, then saves a write-once team forecast for the next unstarted week
to `seasons/<season>/forecasts/week-NN.json` with a checksum. It prices that
week's real game counts and treats players flagged Out as unavailable. A saved
week is never overwritten, so moves made after saving are not reflected. Saves
after the first game day's earliest tipoff are kept but marked late and left out
of the report. The report needs `league-archive` output from a later week, and
it assumes perfect lineups and locks, so real scores should run below forecasts.

### Command-line workflow

From the repository root, using the Python environment with `requirements.txt` installed:

```powershell
python -m src.cli league-sync --through-round 1
python -m src.cli league-archive
python -m src.cli league-build
python -m src.cli league-dashboard
Start-Process outputs/league_dashboard.html

python -m src.cli league-rosters
python -m src.cli team-profile --team Emil
python -m src.cli free-agents --team Emil --top 10
python -m src.cli trade-partners --team Emil
python -m src.cli league-trade --team Emil --opponent Mymmeli --give "Zach LaVine" --get "Ryan Rollins"
python -m src.cli league-trade --team Emil --opponent Mymmeli --give "Zach LaVine" --get "Ryan Rollins" --horizon playoffs
```

The sample trades demonstrate syntax, not recommended proposals. `--stress 10` means ±10% projection stress. `--weeks-left` changes only the window multiplier, not the underlying schedule PMF. The regular horizon uses the configured full regular-season average; the playoff horizon uses its own schedule. `--no-replacements` isolates the trade without hypothetical free-agent claims.

`league-sync` now detects the current league leg automatically. An explicit
`--through-round` remains supported and cannot stop before the current leg. The
command reads every round from 0 through that leg, rejecting truncated coverage.
It performs only public GET requests. No command submits a trade, waiver or
lineup to Sleeper.

`league-build` requires real game logs, `data/external/players.csv`, and the season's schedule CSV. Current reconciled metadata supplies team and eligibility before valuation; source CSVs remain unchanged. It writes new `outputs/league_player_values.csv` and `league_forecast.json`, leaving the old draft board alone. The forecast records input hashes, model/config hash, warnings and generation time. Configuration changes or edited forecast bytes require a rebuild. Refreshing team assignments may also require a rebuild. Dashboard files are offline snapshots, not live applications.

## Current verified instance

League `1374183914238648320`: ten teams, 14 active spots plus 1 IR. The imported 140-pick draft and ten completed transactions replay exactly to all ten Sleeper rosters. Pick 139 is Zach LaVine; pick 140 is Darius Acuff. API roster IDs differ from draft slots: Emil is roster 9, draft slot 2; Mymmeli is roster 6, draft slot 1.

IR at the imported snapshot: Eppu—Brandon Miller; Santtu—Mark Williams; Elppu—Peyton Watson; Mymmeli—Kyrie Irving; Olkku—Shaedon Sharpe; Tomppa—Damian Lillard. Emil, Luki, Antti and Mikki have no IR occupant. Source timestamps and immutable payload manifests are stored locally; these are observations, not permanent assertions about future rosters.

September 7 update: Yaxel Lendeborg (`sleeper:4864`) now has a provisional rookie blend in `config/assumptions.yaml`. Eppu's profile, trades, partner screening and full-ten-team percentiles are available. The input is explicitly a prior, not NBA history; historical-game counts for all rookie priors now correctly show zero. See [decision tools](decision_tools.md) for sources and assumptions. A missing player is never assigned zero value or synthetic history.

## Storage and schema

Files under `data/league/<league_id>/` are local and gitignored:

- `raw/<dataset>-<unique_id>.json`: original endpoint responses or derived observations, each with a SHA-256 provenance manifest.
- `sleeper_events.jsonl`: normalized API draft and completed transaction events; stable IDs make repeated imports idempotent.
- `manual_events.jsonl`: separate provisional user evidence; never silently merged with or relabeled as API history.
- `current.json`: atomically published pointer to the latest validated observation.
- `seasons/<season>/snapshots/<observation>/`: checksummed Sleeper weekly/team/player outcomes and NBA per-game logs for future evaluation. Re-run `league-archive` weekly or at season end to refresh scores and backfill the weeks available from Sleeper. See [season archive](season_archive.md).

Canonical player IDs are `sleeper:<id>`. `PlayerIdentity` contains display name, Sleeper/NBA crosswalk fields, eligible positions, team, status and injury-status metadata; this is not a second projection table. NBA IDs are retained on the valuation board; cross-source names are matched only when unambiguous or uniquely disambiguated by current team/age metadata. Equal-quality namesakes remain unmatched. Davion Mitchell and Donovan Mitchell use distinct IDs.

`Team`: `team_id`, `manager`, `draft_slot`, `owner_id`, `display_name`.

`LeagueEvent`: `event_id`, integer `sequence`, `kind`, `occurred_at`, `recorded_at`, `source`, `source_id`, `metadata`, and atomic `transfers` containing `player_id`, nullable `from_team`, nullable `to_team`. Null owner means free agency. Draft metadata preserves pick/round/slot. Completed API transactions sort by completion/creation time then transaction ID; missing chronology is rejected. Manual events preserve supplied order without inventing dates.

`LeagueState` is replay-derived: teams, ownership index, reserve sets, last sequence, verification status, observation timestamp and notes. Free agents are catalogue IDs minus ownership; the recommendation pool further requires active (`ACT`) metadata, an NBA-team assignment, positions, and a valid projection. IR players remain owned and are excluded from active-roster evaluations and the free-agent market. Their eventual return timing is not forecast.

Events validate all transfers against the same before-state, then apply together. Unknown players/teams, duplicate ownership, repeated sequence/ID, impossible transfer directions, missing outgoing ownership, and invalid known reserve/capacity states fail explicitly. API replay may omit historical IR movements, so capacity is reconciled at the final complete roster/reserve observation rather than invented for each historical event. A process lock and atomic batch replacement preserve the old event prefix. Changed old API history is rejected for review. Observation and event-prefix hashes detect tampering; a failed import never advances the current pointer.

## Offline import and later events

If network access is unavailable, export a bundle with these keys: `league_id`, `retrieved_at`, `league`, `users`, `draft`, `picks`, `players`, `transactions`, `transaction_rounds`, `rosters`. Values are raw responses from the corresponding [public Sleeper endpoints](https://docs.sleeper.com/). `transactions` combines rounds 0 through the current leg; `transaction_rounds` records that coverage. Then:

```powershell
python -m src.cli league-import --file sleeper_bundle.json
```

`fetch_bundle` in `src/league/storage.py` creates that exact shape and archives it during a normal sync. Imports verify the league, season, team count, scoring weights (including bonus and legacy/current key aliases), roster slots, IR capacity, game mode and playoff start. Full roster reconciliation is required. Commissioned or non-player-asset transactions are not silently skipped: unsupported draft-pick/waiver-budget transfers stop import for explicit support. Existing draft-pick valuation in the legacy evaluator remains available; the new post-draft evaluator is player-only.

When no current observation exists, `league-init-manual` reconstructs the evidence in `config/league_instance.yaml`. It cannot claim API verification and does not guess IR. `league-append --file event.json` appends a schema-valid event to a provisional manual log; use `league-sync` for API-backed histories. Example (replace IDs, sequence, and event ID with actual evidence):

```json
{"event_id":"manual:next-change","sequence":151,"kind":"add_drop","source":"manual","transfers":[{"player_id":"sleeper:OLD","from_team":"manual:2","to_team":null},{"player_id":"sleeper:NEW","from_team":null,"to_team":"manual:2"}]}
```

Never edit old events to force reconciliation. Preserve the failed raw snapshots and investigate the exact mismatch. A interrupted-write lock needs an operator to confirm no writer is active before removing that specific lock; the tool does not clear locks blindly.

## Mathematics and assumptions

Let `n` be scheduled games in a fantasy week and `p` projected game availability. Played-game count `K` is a mixture: with weight `1-c`, `K ~ Binomial(n,p)`; with weight `c`, the player plays all `n` games with probability `p`, otherwise none. `c = model.weekly_availability.correlation`, default 0.5, is an explicit uncalibrated assumption. Both components preserve `E[K]=n*p`.

Usable-week probability is `q=P(K>0)`. Conditional weekly value `v` runs the existing Lock-In model on `K | K>0` and the existing projected scoring distribution. Thus missed games reduce available lock opportunities, and the trade model charges whole-week absence once using `q`; it does not mistakenly treat games/82 as a weekly outage probability. Player season value is `regular_weeks*q*v`. The legacy healthy-schedule `lockin_value` remains available separately; league analytics require the new conditional fields.

Roster expected weekly value now averages legal lineups over joint independent weekly availability, including backups. This league uses 512 fixed antithetic scenarios keyed by player ID, shared before/after trades; small gains are approximate. Zero-availability assets contribute zero, and simultaneous absences compete for actual remaining backups. Injury labels alone do not establish recovery timing: base projections remain provisional and require reviewed assumptions for known availability changes. Automatic offers exclude flagged assets and must survive an additional all-flagged-players-unavailable scenario. Manual offers show injury warnings and sensitivity results; a blank injury field is not verified health.

- Expected marginal value: decrease in expected roster value when each player is removed, including bench players.
- Bench insurance: expected value of full roster minus expected value of its healthy starters alone.
- Positional redundancy: lowest/highest expected removal costs among players eligible at each primary position, and the least-cost removable asset. Categories overlap and are not additive.
- Positional market gap: best legal add/drop gain from available players at that position. This measures an actionable weakness relative to the actual pool, not a generic basketball category.
- Replaceability: baseline minus expected value after removing a player and adding the best available free agent. Negative means an improvement is available under claim assumptions.
- Concentration: HHI of healthy starter value shares. Higher means more concentrated; it is not an arbitrary risk score.
- League percentiles: share of the other nine teams below the value plus half of ties; z-score uses their population mean/SD. Undefined SD is reported as null. All ten profiles must be complete. High absence cost/concentration is not labeled better.
- Trade compatibility: minimum of the best positive relocation gain in each direction, where relocation gain is destination's addition gain minus source's removal loss. Hypothetical additions are before cuts; compatibility is only a screening measure.
- Package cuts maximize remaining expected roster value, not raw player value. Single cuts are exhaustively evaluated; multiple cuts and multiple replacements are sequential greedy choices, not globally optimal packages. Eight-decimal tie rounding then stable ID order is shared with JavaScript.
- Replacement claims fill only slots newly opened by the trade, not pre-existing vacancies. Opponent cuts enter the hypothetical post-waiver pool; no free agent is awarded twice. Claim success, waiver priority and timing are assumptions, not guaranteed acquisitions.
- Two-sided verdicts require robust benefit/loss under ±projection stress; if either side crosses zero, verdict is TOO CLOSE TO CALL. Stress holds selected cuts/pickups fixed and is not a confidence interval. Weekly, starter, depth and window deltas are shown for both rosters, never replaced by raw sums.

## Calendar and playoff separation

Regular NBA fixtures only: preseason and Cup-final identifiers are excluded, unresolved teams/dates are excluded with explicit counts, and raw files are not edited. Both team appearances share the same normalized date/calendar. Input dates are date labels, not tipoff timestamps requiring venue-time conversion. Legitimate zero-game weeks remain within the season. Historical fallback uses the most recent loaded season, not a multi-year calendar containing offseasons.

`calendar.week_start_weekday` sets ISO weekday; optional `calendar.week_one_start` anchors week 1. Without it the first regular fixture anchors week 1, explicitly labeled an assumption. Regular valuation uses the first `regular_season_weeks`. Playoffs use `playoffs.start_week` through optional `playoffs.end_week`, otherwise the final loaded week. Overlap is rejected. Empty playoff coverage is unavailable, not silently regular-season value.

At this snapshot, there are 22 regular weeks and 3 loaded playoff weeks; 67 non-regular fixtures and 6 unresolved fixtures were excluded. Exact fantasy calendar boundaries and unfinished NBA Cup scheduling still need review. Playoff values remain a separate output, never a hidden weight inside the regular verdict. Roster optimization operates on horizon-average player values; it is not a week-by-week matchup/streaming simulator.

## Verification

`python -m pytest` runs the original regression suite plus league replay/import, identity, weekly availability, profiles, replacements, calendar, two-sided trades, positional needs and decision-tool tests. Node is required for Python/JavaScript parity checks. Those tests execute the actual embedded functions on randomized multi-position package trades, including cuts, pickups and stress, and compare points/head-to-head Lock-In decisions and positional metrics. Browser smoke checks cover all nine views, automatic proposals, search, add/drop, two horizons, incomplete-roster blocking, narrow layout, script errors and absence of network requests.

No external UI framework, database or cloud dependency is required. The local-only
Python refresh service serves the generated dashboard. Generated output and raw
snapshots are gitignored. The original repository and pre-existing demo CSV changes remain untouched.
