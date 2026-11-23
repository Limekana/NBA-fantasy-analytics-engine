# Project status and session handoff

Last updated: **2026-10-04**. Read this first after session compaction, then inspect
the relevant code and current data before making changes. This is a maintained
checkpoint, not a replacement for source data or a claim of permanently current rosters.

## Current state

### October 4 manual three-team trade checker (latest)

- Added **Moves → Check a 3-team trade** to the offline and loopback dashboard.
  Choose two other managers and route any number of active players to either
  other team. Every participant must send and receive at least one player.
  Ownership, duplicate routing and three distinct teams are validated.
- Simultaneously evaluates all three resulting rosters with the same exact joint
  availability, injury and stress math as the existing two-team evaluator.
  Displays each team's before/after gain, stress outcome, sends, receives,
  automatic roster-limit cuts, Week 1 scenario and playoff window. No assumed
  free-agent claim, picks, Sleeper trade submission or automatic three-team
  proposal search was added. Existing two-team checker still works.
- Targeted regression: **40 passed** (including seven new routing/cut tests).
  Headless Edge check on the offline file completed a three-team cycle and opened
  the original two-team evaluator with no page errors. No submitted transaction.
- Sleeper ownership refresh completed **2026-10-04T11:19:02Z**. The only change
  since the previous snapshot was Luki adding Day'Ron Sharpe and Jeremiah Fears
  and dropping Joe Ingles and Quentin Grimes. Rebuilt the forecast at
  **2026-10-04T11:21:26Z** from the existing 2023–26 historical game logs and
  current NBA team identity. Kawhi Leonard and Brandon Ingram had changed NBA
  teams after the September forecast, so their prior rows were correctly excluded;
  the rebuild restored both without assigning invented zero values. The refreshed
  dashboard reports **no missing team projections**. A loopback browser scenario
  with Antti and both existing trade views passed after the rebuild.
- Local dashboard was started on **http://127.0.0.1:8766/** and verified to serve
  the new checker. The file copy at `outputs/league_dashboard.html` is also ready.
  Obsidian session logging remains unavailable at the supplied vault path, so
  this is the local continuation record. All changes remain uncommitted.

### September 28 live roster check (latest)

- The old `http://127.0.0.1:8765/` address was occupied by LimeSearch (HTTP 200,
  wrong app). The NBA dashboard was started on **http://127.0.0.1:8766/** and
  verified by `/api/session`, browser title and Free agents view. The launcher now
  reuses a running NBA service or selects a free port in 8765–8775 and prints the
  actual URL; its CMD wrapper no longer prints a hardcoded address. Offline HTML
  refresh instructions no longer send the user to the wrong port.
- Sleeper ownership refresh succeeded on **2026-09-28T16:13:49Z**. No NBA
  performance refresh was run; player projections are still built from the older
  historical data. New availability is a roster snapshot, not a promise a player
  will remain unclaimed.
- Recent drops now in the eligible free-agent pool: Anthony Black, Collin
  Gillespie, Jeremiah Fears, Julian Strawther, Mark Williams (Out), Ajay Mitchell
  (DTD). The model excludes flagged players from automatic add/drop suggestions.
- For Emil, the dashboard ranks Black for Daniel Gafford at **+1.24 FP/week**,
  Gillespie for Zach LaVine at **+0.43**, and Fears for Gafford at **+0.17**.
  These are dated modeled lineup changes, not validated role or injury forecasts.
  Black adds a fourth PG-eligible player but reduces C eligibility from four to
  three; the reported gain should be weighed against Gafford's center cover.
  NBA.com's September 22 Orlando preview expects Black to lead the bench unit;
  Phoenix's preview names Gillespie a breakout candidate. Sacramento expects
  LaVine to return with improved health, so the small Gillespie swap estimate
  does not settle the LaVine decision. These outlooks were reviewed for this
  session only and were not silently injected into saved projections.
  Browser verification reached Moves → Free agents → Find best add/drop moves and
  matched these rows. No claim or drop was submitted.
- A visible Edge window was requested for the correct local URL. Kapture had no
  connected tabs, and its new-tab attempt failed due to extension connection; the
  direct Edge launch succeeded. The old in-app browser tab may still show LimeSearch.

### Unified desktop workspace (September 9, latest)

- Consolidated ten navigation destinations into four: My Team, League, Moves,
  Lock-In. My Team includes priorities, roster, and expandable detailed analysis.
  League combines rankings, rosters, and depth comparisons. Moves contains trade
  ideas, manual offer checking, free agents, and expandable partner screening.
- Explicit user preference: desktop-first; no mobile-specific controls needed.
  Removed this pass's mobile-specific styling and used compact desktop controls.
  Existing basic responsive CSS remains; no new dependencies or mobile navigation.
- Team context survives navigation. Data dates remain visible in the collapsed
  refresh panel title. Both refresh actions remain inside it; refresh locks both
  primary and Moves navigation while updating ownership.
- Simplified roster columns and surfaced recorded injury flags. Detailed raw
  availability/removal-cost numbers remain under Team analysis. Team headline
  projections are explicitly labeled optimized, not the user's saved starter plan.
- Design-critique and frontend-design guidance informed hierarchy and progressive
  disclosure, preserving the existing dark palette and keyboard focus styling.
- Full regression run: 479 passed in 135.52s. Browser assertions passed all four
  destinations, merged league sections, horizon changes, team context, free agents,
  manual evaluator entry and Lock-In. Source payload/valuations not refreshed.
- Obsidian remains unavailable at the previously supplied path; this is the local
  session record. Changes are uncommitted. No Sleeper mutations or external writes.

### Simplification pass (September 9, latest)

- Trade finder now starts with two visible selectors: goal and opponent. Goals are
  Improve my team, Find PG depth, and Explore bigger PG deals. Detailed controls
  and starter editing are retained inside collapsed Advanced settings.
- Default uses positional-fit mode, a 0.25 FP/week minimum, planned starters
  protected, no waiver claims, and pessimistic stress OFF as an exclusion gate.
  Stress remains visible as a caution. Bigger PG deals explicitly allow starters.
  Missing starter snapshots fall back to optimized evaluation with a visible notice.
- Offer cards show own planned gain, opponent optimized gain, positional rationale
  and risk. Full analysis is collapsed; manual evaluator remains available.
  Top-level positional priority distinguishes production from backup cover.
- No new scraped feed or projection adjustments in this pass. Role/minutes outlook,
  injury timelines and market evidence remain unverified gaps, not invented inputs.
  Existing eligibility-count surplus is explicitly not proof of opponent benefit.
- Verified 39 focused regression tests; Edge assertions cover defaults, presets,
  default/bold Olkku searches, collapsed settings/results, 390px layout and manual
  evaluator handoff. Default Olkku search found four qualifying candidates (only
  three shown). This is dated model output, not a recommendation or live valuation.
- Source edits limited to league_tools_ui.js plus regenerated dashboard and this
  record. UI skill guided progressive disclosure; no dependencies added. Obsidian
  logging remains blocked by the previously unavailable supplied vault path.

### September 9 continuation

- Added editable, validated planned starters seeded from the saved Sleeper Week 1
  lineup. Auto trades rank against that plan by default; keeping those starters
  out of offers is independently selectable. Flexible replacement is allowed on
  absences; no changes are sent to Sleeper.
- Separated healthy gain, original-PG-leader-out gain and actual absence-penalty
  reduction. Optional insurance filter requires the latter to be positive.
- Added Week 1/selected-week chronological schedule comparisons alongside
  season-average results, including the opponent's saved starters where valid.
  Missing forecast/schedule coverage withholds estimates rather than inventing zero.
- Added a bolder PG preset permitting surplus frontcourt and starter offers,
  opponent selection, planned-lineup pessimistic and recorded-injury stress.
  Two-team packages and existing manual evaluator remain; three-way deals deferred.
- Added dated role/source reviews and a strict optional role + market evidence
  gate. No market-price model was added: incomplete evidence is clearly exploratory.
  Review sources live in config/trade_reviews.json; seven-day freshness is enforced.
- Launcher now tries usable Python installations, reports errors and keeps its CMD
  window open. Verified cold start and repeated launch at fixed port 8765.
- Browser verified all nine requested starters, Olkku singles and package search,
  research gate, and 390px layout without page errors/overflow. Deni -> Maxey is
  a speculative saved-model candidate: planned +1.95 FP/week, pessimistic -3.97,
  actual PG absence-penalty reduction zero; Week 1 +6.31 versus opponent -6.31.
  Do not describe this as a confirmed fair/acceptable trade. Strict insurance
  search found zero Olkku offers (126 singles, 213 packages screened).
- Tests: added 11 planned-lineup/week/research regressions. Full rerun: **479 passed
  in 137.62 seconds**. Corrected payload preservation and review initialization
  order; final Edge rerun passed including automatic-offer handoff to manual
  evaluation, Week 1, regular-season and playoff sections. Use a fresh --basetemp under the task workspace:
  the default temporary-directory permissions caused unrelated setup errors.
- Regenerated HTML without refreshing ownership or forecasts. Snapshot remains
  September 7 ownership / September 8 forecast; no live injury clearance assumed.
- Obsidian session logging blocked: supplied D: manifest path did not exist and
  Projects access was denied. This file is the local continuation record.
- Source: new league_trade_context.js, tests/test_trade_context.py; related
  renderer, LINEUP_JS, needs, search/UI and launcher changes are uncommitted.
  Service was cold-started successfully; final renderer-only correction was made
  afterward. Next safe service restart loads that correction for future rebuilds.

### Previous September 8 baseline (historical evidence)

- The league dashboard is implemented and usable at **http://127.0.0.1:8765/**.
- Latest completed work: negative-score preservation; **Refresh NBA performance**;
  ordered fixture/context-aware Lock-In advice; optional **Pay for positional fit**
  trade search. Existing rankings, Sleeper refresh and manual evaluator are retained.
- Latest full test run: **468 passed in 136.65 seconds**. New upgrade/refresh tests
  cover scoring tails, ordered decisions, context validation, overpay constraints,
  immutable snapshots, malformed/partial downloads, rollback and HTTP security.
- Live Edge checks passed at desktop and 390px, including Lock-In decisions,
  three PG proposals, opening an offer in the manual evaluator, and an actual
  performance-button POST → async rebuild → reload. No page errors or mobile overflow.
  The agent-browser CLI failed with a CDP-channel error; existing Playwright/Edge
  checks supplied browser verification instead. No browser test remains running.
- The approved four improvements are implemented. The local service was restarted
  hidden after Python changes and intentionally left running; verify it if needed.
- Work is local and uncommitted. Many modified/untracked files predate this
  checkpoint, including demo CSV changes. Preserve them; do not reset the worktree.

## Correct repository and startup

Active repository: `C:/Users/emilh/Documents/Codex/nba-fantasy-analytics-engine`.
Some configuration paths capitalize `NBA`; on this Windows filesystem this is
the same directory. **Do not modify the older copy at
`C:/Users/emilh/nba-fantasy-analytics-engine`.** The Codex task's working directory
may instead be `C:/Users/emilh/Documents/Codex/2026-09-06/the`; run project commands
from the actual repository. Request scoped write permission when required.

Double-click **Open League Dashboard.cmd** in the repository root. It launches
the local-only service hidden if necessary and opens the dashboard. Run it again
after restarting the computer. The offline snapshot is
`outputs/league_dashboard.html`; its refresh button opens the local service.

Known working Python:
`C:/Users/emilh/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe`.
Use `-X utf8` on Windows. From the repository root:

```powershell
& 'C:/Users/emilh/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe' -X utf8 -m pytest -p no:cacheprovider -o addopts='' -q --tb=short
```

The suite can take roughly 2–3 minutes, with a slow exact trade-parity test.
To serve manually, use the same Python with `-m src.cli league-serve --port 8765`.
Logs: `outputs/refresh-service.log` and `outputs/refresh-service-errors.log`.
After Python renderer/server changes, safely restart the identified, idle local
service; an already-running process retains imported Python code. Never reuse an
old process ID or terminate an unrelated process based only on a port assumption.

## League and user decisions to preserve

- League ID **1374183914238648320**, ten teams, NBA 2026–27, Sleeper **Lock-In**,
  **head-to-head 1v1** matchups. Emil is the user's team.
- Nine starter slots: PG, SG, G, SF, PF, F, C, UTIL, UTIL; five bench spots and one
  IR spot. Active-roster calculations exclude IR, but IR players remain owned.
- Sleeper roster IDs: 1 Eppu, 2 Santtu, 3 Luki, 4 Elppu, 5 Antti, 6 Mymmeli,
  7 Olkku, 8 Tomppa, 9 Emil, 10 Mikki. **Eppu and Elppu are different managers.**
  Roster IDs differ from draft slots; use the reconciled mapping, not draft order.
- User-supplied names: Tomppa = BBL Bandits; Olkku = Olkku55; Mikki = Mikkelsson;
  Antti = nextmj; Luki = Joe niggles; Eppu = TheBigEDawgs; Mymmeli = lukamagic6.
- Scoring: points 0.5; rebounds/assists 1; steals/blocks 2; turnovers/fouls -1;
  made FT 0.25; made three 0.5; double-double 2; triple-double 3; 40+ points 3;
  50+ points 4; 15+ assists 3; 20+ rebounds 3. **40+/50+ bonuses stack**;
  preserve the configured double/triple-double stacking too.
- Keep the existing manual trade evaluator alongside automatic proposals.
- User explicitly chose **two-team packages only**, not automatic three-way trades.
- Default trade intent: exchange surplus wings for useful PG depth (or another
  selected need), not sell Luka simply because package totals look bigger.
- Other managers do not have this engine. Offers need plausible direct roster
  benefits for both sides; do not justify their gain through assumed waiver pickups.
- September 8 refinement: the user explicitly authorized an **optional** positional-fit
  mode that allows a slight individual-value overpay and does not require mutual
  lineup profit. Strict defaults remain. Disclose any opponent loss and speculative
  stress results; keep your own expected lineup gain positive. See details below.
- Never submit trades, claims, lineup changes or locks to Sleeper. All integration
  is read-only public data; decisions remain the user's.

## Data checkpoint and provenance

Read directly from the saved dashboard while writing this file:

- Ownership verified: **true**.
- Latest ownership observation: **2026-09-07T18:17:30.840985+00:00**.
- Latest refresh summary has **no new changes**.
- Performance forecast generated: **2026-09-08T12:53:06.578625+00:00**.
- NBA performance downloaded: **2026-09-08T12:53:06.637609+00:00**.
- Latest regular-season history: **2026-04-12**. Live downloads: 26,651 rows for
  2025–26; 26,306 for 2024–25; 26,401 for 2023–24. Download/build times are not the
  latest game date. Performance refresh intentionally leaves ownership unchanged.

An earlier successful refresh at 18:00 UTC verified Tomppa adding **Collin
Gillespie** and dropping **Christian Braun**, and Elppu adding **Jaime Jaquez**
and dropping **Day'Ron Sharpe**. Do not append these again manually. Imported
draft picks 139/140 were Zach LaVine/Darius Acuff. Daniel Gafford's move onto
Emil's roster is already incorporated. Current ownership must come from replay
and the latest validated snapshot, not conversation memory.

`data/league/1374183914238648320/` holds gitignored immutable raw payloads and
manifests, `sleeper_events.jsonl`, separate provisional `manual_events.jsonl`, and
`current.json`. Never edit old events to force reconciliation. New imports are
idempotent and verify event replay against all current rosters/reserves.

All ten teams had complete active-player projection coverage in the tested
snapshot. Missing active projections must block affected comparisons, never
silently become zero. A missing IR projection must not block active strength.

Yaxel Lendeborg now has an explicitly **provisional rookie prior** blending the
user's pasted per-game estimates and FanScout's rookie estimates equally where
both supply a field. Do not present this as NBA history or broad expert consensus.
Inputs and sources are in `config/assumptions.yaml` and `docs/decision_tools.md`.
Personal fouls of 2.5/game remain an unsourced editable assumption; rookie
historical-game counts are zero. This restored Eppu's projection coverage.

## Completed features and model corrections

### League desk and Lock-In advisor

Reconciled league rosters, free agents, team profiles, positional needs, manual
trades, automatic two-team proposals and Lock-In advice are available together.
The full Sleeper catalogue is loaded; recommendations require valid projections,
eligibility and active metadata. Unprojected players are not invented.

Lock-In has points-maximizing and head-to-head objectives. Cached Sleeper matchup
pairings inform the matchup view; completed scores, remaining estimates and lock
eligibility require user confirmation. A pre-week starter helper provides editable
scenarios, not verified live locks or calibrated win odds. Final-game DNPs score
zero. Old passed scores cannot be recovered. No automated lock submission exists.

### Positional needs and realistic trade defaults

Needs distinguish leading-player quality from cover when that leader is out.
The need index compares production and leader-out losses to peer medians; it is
a heuristic with overlapping positions, **not additive positional FP gains**.
UI prioritizes eligibility counts, leader quality and whole-lineup cover changes.
Positive cover credit is withheld when a trade changes the position's leader.

Default automatic search is **Surplus for added depth**, outgoing surplus wings,
protecting positional leaders and existing target-position players. A directly
received player must add target eligibility and improve the legal lineup with
the same original leader absent. Broader search remains optional. No waiver
claims by default; both sides must benefit after required cuts. Default own gain
threshold is 1 FP/week; pessimistic projection stress is enabled at 10%.
No qualifying results is acceptable: never force an offer or invent acceptance odds.
Packages use a shortlist, not exhaustive enumeration. No ADP/market-value model
is configured, so projected mutual benefit does not establish market fairness.

### Availability and injury corrections

Roster valuation averages legal lineups across joint weekly availability of
starters **and backups**, avoiding double-counted backup coverage. Weekly
absences across players are assumed independent. General profiles and candidate
screening use 512 fixed antithetic scenarios for this league; the manual evaluator,
up to 60 automatic finalists and Power Rankings use exact integration. Finalists
are filtered and ranked again after exact evaluation.

Recorded injury flags flow through both horizons. Flagged players are excluded
from automatic offers/cuts/claims. Manual trades expose flags and sensitivity;
both sides must also pass the all-flagged-players-unavailable test for automatic
recommendations. Historical availability is **not a recovery forecast**; blank
flags are not verified health. Explicit zero availability remains zero.

The problematic Jimmy Butler-for-Jamal Murray example is no longer recommended:
the corrected tested snapshot gave Elppu about **-2.05 FP/week**, despite Emil's
gain. This is a dated model regression example, not a current injury diagnosis.

### September 8: positional-fit overpay option

Automatic Trades → **Pay for positional fit** → **Strengthen PG**. Default own
minimum gain becomes 0.25 FP/week; individual overpay cap 15% (editable 0–50%);
maximum opponent baseline/injury-scenario loss 1 FP/week (editable 0–3).
Individual value = conditional weekly FP × weekly availability, summed across
offered players; this is not ADP, market value, name reputation or acceptance odds.
If the opponent loses baseline lineup points, they must receive at least as much
individual model value. Nonnegative opponent-lineup deals also qualify: overpay
is allowed, not required. Pessimistic projection-stress filtering starts OFF in
this explicitly exploratory mode, but losses are shown. Turning it ON restores
strict positive/nonnegative pessimistic checks. Your recorded-injury stress gain
must remain positive; theirs cannot lose more than the selected limit.

Keeps the highest individual-value asset and every existing target-eligible player;
other positional leaders may be offered. Requires direct added eligibility and
improved legal-lineup cover with the original target leader absent. Both wing
search modes now exclude C-eligible bigs; use any-position mode explicitly to
consider them. No hypothetical FA pickups in fit mode. Finalists checked exactly;
near-zero opponent baseline results get a 0.25 FP screen-only tolerance, never a
displayed-result tolerance. PG cover FP gain is visible and labeled non-additive.

Latest full real-roster PG fit check: 945 singles, 763 packages screened, 305 fully
evaluated, 5 exact finalists, 3 qualifying offers (all with Olkku): Deni→Maxey,
Saddiq→VJ Edgecombe, Saddiq→Pritchard. These are dated **speculative scenarios**,
not trade recommendations or market-fairness claims; all fail pessimistic projection
stress. Example Deni→Maxey: Emil +1.86, Olkku +0.06 FP/week; PG leader-out cover
+2.29. An automated fixture separately proves a genuine slight-overpay / small
opponent-loss trade is supported. Never relax filters merely to force results.

### September 8: scoring tails and game context

`rescale_distribution` preserves negative scores and projected means. Positive
sample/target means use multiplication; nonpositive means/targets use a shift,
not a sign-flipping multiplier or constant replacement. Percentiles, SD and
threshold probabilities are recomputed from the transformed distribution.
Final-game negative scores remain possible; a final DNP is zero, not a rewind.

Lock-In fixture selection populates the remaining game count. Points and head-to-head
backward induction use per-fixture factors in chronological order; inconsistent
counts stop the calculation. Manual count remains unadjusted. Pre-week helper
uses the same context factors. The reference threshold table is explicitly unadjusted.

`src/schedule/context.py`: latest historical season ridge fit on relative FP/min
residuals, opponent/home/team-back-to-back features; earlier 80% of dates train,
later dates test using training-only player baselines. Needs at least 0.2% held-out
MSE improvement before applying, otherwise neutral. Applied factor capped ±10%;
unknown opponents neutral; checkbox allows neutral comparisons. Latest check:
16,981 train / 4,118 validation rows, MSE 0.217852→0.212198 (~2.60% reduction).
Passed one historical holdout, **not calibrated future accuracy**. No separate
pace/travel/return-date model. Rookie roles and team-personnel changes remain uncertain.

### Refresh from Sleeper

The button detects the league's current leg, reads rounds 0 through that leg,
fetches rosters/metadata and completed transactions, validates replay, stages
the new view and publishes after checks. Errors retain the old usable snapshot;
publication failures attempt rollback. Immutable archives may remain after a
failed attempt. Interruption during multi-file publication is not a database
transaction; inspect integrity before clearing any leftover lock.

Service binds only to 127.0.0.1 and exposes fixed routes. Host/Origin checks and
a per-process refresh token protect writes. No arbitrary files, commands or CORS.
Duplicate clicks share a running job; completed attempts have a 30-second cooldown.
Polling occurs only during a requested refresh. Successful refresh clears stale
trade suggestions and recalculates analytics from the refreshed snapshot.
**Refresh does not fetch new performance history or invent new projections.**

That statement applies to **Refresh from Sleeper**. The new separate **Refresh NBA
performance** button fetches all configured historical training seasons, then stages
and validates a full forecast/advisor/dashboard rebuild. Keeps ownership, schedule,
rookie priors, roles and season weights unchanged. Configuring 2026–27 in-season
weighting is still future reviewed model work, not silently enabled by the button.

`src/league/performance_refresh.py` validates schema, finite stats, dates, season,
synthetic flags and coverage of previously loaded player-games. Downloads are new
immutable per-season snapshots with manifests; a checksummed season `current.json`
selects the active version. Originals remain intact. Pipeline provenance includes
the selected file and pointer. Full active-roster coverage and unchanged guarded
inputs/outputs are required before publication; failures attempt rollback. Same
shared refresh lock/controller/cooldown and Host/Origin/token checks, new fixed
POST `/api/refresh-performance`. Publication is not power-loss atomic; failed
immutable archives may remain. Do not delete snapshots or locks without inspection.

Internal `refresh_performance(download=False)` safely rebuilds from reviewed local
history without claiming a fresh download. Browser and source timestamps distinguish
ownership, performance download, latest historical game and forecast generation.

### Power Rankings (latest addition)

Ranks all teams by exact expected legal-lineup FP/week, separately for regular
season and playoffs. Baseline includes modeled availability; injury stress sets
all recorded flagged active players unavailable together. IR is excluded with
no invented return dates. Bench cover is displayed, not added twice.

Shows selected-team rank and gap, baseline/stress/healthy output, baseline bench
cover and named injury/IR cautions. Displayed two-decimal ties share competition
ranks (1, 1, 3). Incomplete active projection coverage withholds the whole league
ranking. Ranks recompute from the current payload on opening or changing controls
and therefore after successful refresh. They are not standings, dynasty values,
championship odds or statistically meaningful distinctions for tiny gaps.

At the last feature validation, Emil was **#1 baseline, 255.52 FP/week**, and
**#5 injury stress, 218.13 FP/week**. Treat these as dated model outputs, not
permanent ranks or a forecast that every flagged player will remain absent.

## Source map and documentation

- `src/reporting/league_dashboard.py`: payload and HTML renderer; combines the JS
  modules. Edit source modules, not only generated `outputs/league_dashboard.html`.
- `src/reporting/league_ui.js`: main navigation and views.
- `src/reporting/league_rankings.js`: pure ranking calculation and ranking UI.
- `src/reporting/league_math.js`, `league_needs.js`, `league_tools_ui.js`:
  browser trade/roster/needs calculations and decision tools.
- `src/reporting/league_advisor_math.js`: Lock-In scenario calculations.
- `src/reporting/league_refresh.js`: refresh button and progress handling.
- `src/league/refresh_server.py`, `storage.py`: local service and Sleeper import.
- `src/league/performance_refresh.py`: staged NBA download/rebuild/publication.
- `src/schedule/context.py`: held-out-tested game-context adjustment.
- `src/ingestion/csv_source.py`: checksummed current raw snapshot selection.
- `src/trade.py`: Python roster/availability and trade math; browser/Python
  agreement matters. `src/projections/games_played.py`: availability overrides.
- `src/projections/consensus.py`, `rookies.py`: source blends and rookie priors.
- `src/cli.py`: `league-sync`, `league-build`, `league-dashboard`,
  `league-matchups`, `league-serve`, profiles, free agents and trades.
- `tests/test_power_rankings.py`, `test_league_refresh.py`, `test_trade_health.py`,
  `test_positional_needs.py`, `test_surplus_trades.py`: key regression coverage.
- `tests/test_model_upgrades.py`, `tests/test_performance_refresh.py`: September 8 fixes.
- `docs/decision_tools.md`: user workflows, formulas, source details and caveats.
- `docs/league_state.md`: league storage, refresh, reconciliation and commands.
- `docs/trade_evaluation.md`: valuation and exact/sampled trade methodology.

## Known limitations and safe continuation

- Calendar still excludes six unresolved/date-less fixtures. Week-one and playoff
  boundaries have configurable assumptions; the advisor UI still labels weeks from
  the first loaded Monday and would need alignment if calendar overrides change.
  Experimental opponent/venue/back-to-back effects are fitted, but no separate
  pace/travel or live injury forecast exists.
- No calibrated return-date model, live automatic rest-of-matchup estimator,
  championship simulation or trade-acceptance model. Rookie inputs remain uncertain.
- Some rookie landing spots are unconfirmed; inspect forecast warnings. The forecast
  reports one unmatched projection row; complete active-roster coverage is a
  separate check and does not imply the whole NBA catalogue has projections.
- Negative-score clipping is fixed. Distribution/context/availability uncertainty
  is still not calibrated, and roster valuation is horizon-average rather than a
  full week-by-week simulation of schedule, lineup moves and live matchup scores.
- A model/config change requires rebuilding the forecast, not bypassing checksums.
  A source-only UI change needs rerendering; preserve legitimate refresh metadata.
- Optional future work needs the user's next request. Three-way proposals were
  explicitly deferred. Do not assume authorization to trade, publish or auto-monitor.

After each meaningful change, update this file's date, completion state, latest
test evidence, data timestamps and remaining issues. Re-read current payloads
before repeating old roster claims. Keep earlier safety constraints and user
preferences unless the user changes them.
