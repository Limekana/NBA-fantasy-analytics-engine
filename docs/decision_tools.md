# League decision tools — September 8, 2026

The existing offline dashboard and manual trade evaluator are retained. Reload
`outputs/league_dashboard.html` after rebuilding. No control submits a trade,
waiver, lineup change or lock to Sleeper. The original repository under
`C:/Users/emilh/nba-fantasy-analytics-engine` is not modified.

## Try it now

### Power Rankings

The **Power Rankings** tab orders every team by exact expected legal-lineup
fantasy points per week. Regular-season and playoff horizons remain separate.
The baseline includes modeled weekly availability of starters and backups;
bench cover is displayed as a decomposition, never added to strength twice.
The injury-stress option instead ranks the scenario where all active players
with recorded injury flags are unavailable together. This is not a forecast
that they miss the season. IR players are excluded without invented return dates.

The view shows the selected team's rank, projected output and gap to first,
each team's baseline and injury-stress output, healthy lineup and baseline
bench insurance, plus named injury/IR cautions. Two-decimal ties share competition
ranks (1, 1, 3); a tiny gap is not evidence of a meaningful advantage. Missing
active-player projections withhold the whole league ranking for that horizon.

Ranks are recomputed locally from the current dashboard snapshot whenever the
view opens. Successful Sleeper refreshes therefore update the next calculation
and clear older trade suggestions without needing saved rankings. These are
roster-strength projections, not actual standings, win probabilities, manager
skill ratings, dynasty valuations or championship odds. General team profiles
use faster approximate integration and can differ slightly. The source roster
timestamp and performance-forecast timestamp are displayed separately.

Tests: `tests/test_power_rankings.py` verifies exact Python-model agreement,
injury-driven reordering, ties, missing data, IR exclusion and changed rosters.

1. **Automatic Trades** → **Find trades across the league**. Emil is the default
   team. You do not need to select an opponent or player. Each result shows both
   teams' expected gains, starters versus bench effects, stress bounds, cuts and
   assumed claims. **Open in existing evaluator** pre-fills the original tool.
2. **Lock-In Advisor** → select a player and week. Week 1's API pairing is Emil
   versus Mikki. Enter a completed-game FP score and remaining team games. For
   a pre-season experiment, **Fill a pre-week starter scenario** supplies editable
   matchup estimates. Eligibility boxes remain unchecked: a scenario is not a
   verified live recommendation. During the season enter actual locked points
   and update the other players' expected remaining points.
3. **Team Strengths** now includes Eppu. His complete active-roster projection
   is available, although Yaxel's contribution remains a provisional rookie prior.

For a simple head-to-head test, set your locked score and both remaining-point
   estimates to zero, their locked score to 20, and uncertainty to zero. A completed
   25-point score wins that scenario. A points-only policy may prefer waiting
   if that player's future expected score is higher. This is a hypothetical
   test, not an assertion that any current game is eligible to lock.

Refresh real data from the repository root:

```powershell
python -m src.cli league-sync --through-round 1
python -m src.cli league-matchups --week 1
python -m src.cli league-build
python -m src.cli league-dashboard
```

Use the actual current league leg and fantasy week, not permanently `1`.
The matchup command saves a timestamped, checksummed read-only observation;
it neither changes ownership nor counts the endpoint's displayed points as
verified locked scores. Future cached weeks can be added by calling it again
with another week. Dashboard generation itself does not fetch the internet.

## Yaxel's provisional projection

The user's pasted projection had no source URL/author. It is a user-provided
estimate, not independently verified research. [FanScout's rookie projections](https://fanscout.pro/rookie-projections)
were checked September 7, 2026: per-game, long-season projections. Its general
and rookie pages are the same input, not two independent votes. The general
page labels the target season 2026–27. Daily DFS forecasts, college stats and
Summer League production were excluded from the season-projection average.

Each shared field gets a simple equal-weight average. Missing fields do not
contribute zero. This is a two-input working estimate, not a broad or proven
independent expert consensus. The source rows, season, units and fallback are
editable in `config/assumptions.yaml`; `resolve_rookie` performs the blend on
each build and rejects duplicate source IDs, mixed seasons/units and missing
scoring fields.

| Per-game field | User | FanScout | Working input |
|---|---:|---:|---:|
| Minutes | 25.5 | 26 | 25.75 |
| Points | 9.4 | 10.47 | 9.935 |
| Rebounds | 5.8 | 5.99 | 5.895 |
| Assists | 2.4 | 2.25 | 2.325 |
| Steals | 1.1 | 0.81 | 0.955 |
| Blocks | 0.9 | 0.86 | 0.88 |
| Three-pointers made | 0.8 | 1.18 | 0.99 |
| Turnovers | 1.2 | 2.05 | 1.625 |

FanScout alone supplies the 73-game prior. Made free throws are approximately
1.4516/game, derived from its rounded 1.91 attempts × 76%. Personal fouls are
an **unsourced, editable 2.5/game modeling assumption**. Changing this by
0.5 changes base fantasy scoring by 0.5 FP/game in the opposite direction.
The user's 48.5% FG figure is not used because this league does not score FG%.
Decimal precision records the arithmetic, not certainty about outcomes.

The existing rookie generator supplies simulated variability and bonus rates.
The regular availability model subsequently adjusts the games prior. No
simulated rows are stored as real NBA game logs. The displayed historical-game
count is zero. Use the source/assumption notes and stress ranges when comparing
Eppu; complete coverage does not imply that rookie estimates are reliable.

## Lock-In: two objectives

[Sleeper's official rules](https://support.sleeper.com/en/articles/6522833-lock-in-mode-details)
require a completed game, eligible starting-lineup participation and a decision
before the next game starts. A lock is irreversible. Without a chosen lock,
the team's final game supplies the score, including zero if the player DNPs.
An old good performance cannot be recovered after passing it.

The advisor requires explicit confirmations instead of guessing eligibility
from schedule dates. Changing an input clears the previous recommendation.
Source schedule dates are not tipoff timestamps. Optional opponent/venue/team
back-to-back adjustments are fitted as described below; no separate pace or travel model exists. Calendar
fixtures remain incomplete (six unresolved fixtures in the current input).

**Points objective:** `W[1] = p E[X]`; for k > 1,
`W[k] = p E[max(X,W[k-1])] + (1-p)W[k-1]`. Keep an eligible completed score
when it is at least `W[remaining]`. The final game's DNP gives zero; a DNP in
an earlier game advances to the next opportunity. Appearance probability `p`
is an editable independent-game scenario, not a current injury report.

**Head-to-head objective:** margin = your locked points − their locked points
+ expected remaining points from your OTHER players − theirs. Exclude the
selected player's candidate/future points from both your inputs. Other players'
residual net points are modeled as Normal(0, entered SD), independent of the
selected player. `U(x) = P(margin + x + residual > 0)`, with exact ties counted
half when SD is zero. Replace score utility in the stopping recurrence by U.
Compare U(completed score) against the value of waiting. With no future games
there is no alternative to compare. Locked/invalid-eligibility cases do not
produce an actionable new lock recommendation.

The UI calls this a **modeled win-equivalent chance**, not calibrated win odds.
It assumes a static estimate of others' outcomes through future decisions;
it does not simulate new information arriving from concurrent games. Recompute
as the score, lineups and injury news change. Ties counting half is a decision
utility, not an assertion about playoff tiebreak rules.

The optional pre-week helper uses cached API starters (or projected starters
when absent), this week's loaded NBA games and independent appearance
probabilities. It computes the first two moments of points-optimal stopping
for each other starter and sums their independent variances. It assumes zero
locked points, no injury correlation and no future lineup swaps. A wrong or
outdated starter list must be corrected in the live inputs. It is not a live
rest-of-matchup estimator once games have begun.

Veteran samples are historical scores rescaled to the same projected mean used
by the valuation pipeline; rookie samples are explicitly simulated priors.
Rescaling now preserves negative scores and matches the projected mean without
clipping. A positive mean/target uses multiplication; a nonpositive mean or
target uses an additive shift to avoid reversing good and bad games. Distribution
percentiles, spread and threshold probabilities are recomputed from this sample.
Distributions are rounded to six decimals for offline packaging. The sidecar
has a forecast checksum; an edited/missing sidecar requires rebuilding.

## Automatic trade search and limits

### Optional: Pay for positional fit

Choose **Automatic Trades → Search approach → Pay for positional fit**, then
**Strengthen PG** (or another single position). This is separate from the strict
default and retains two-team packages and the manual evaluator.

- Your expected legal-lineup gain must be positive and reach the chosen minimum
  (0.25 FP/week initially). Leader-out target cover must improve, existing target
  players must stay, and your highest individual expected-value asset is protected.
  Other positional leaders may be offered; outgoing wings still need relative
  eligibility surplus and cannot have C eligibility.
- Overpay = max(0, sent individual value / received individual value − 1), capped
  at 15% initially (editable 0–50%). Individual value is conditional weekly FP ×
  weekly availability, summed across traded players, **not market price or ADP**.
- Offers with nonnegative opponent baseline lineup gain qualify without requiring
  an overpay. If their lineup loses, they must receive at least as much individual
  model value. Their baseline and recorded-injury-scenario loss cannot exceed the
  entered limit (1 FP/week initially; editable 0–3).
- Your gain must still be positive with all flagged teammates absent. Flagged
  offered players remain excluded. Hypothetical waiver claims are disabled.
- Pessimistic projection-stress filtering starts OFF in this exploratory mode;
  both teams' stress losses remain prominently visible. Re-enabling it restores
  strict nonnegative pessimistic outcomes for both sides (you strictly positive).
- Every displayed finalist is rechecked exactly. A 0.25 FP screening tolerance
  around their baseline zero boundary avoids rejecting borderline candidates
  solely on sampled integration; the displayed result has no such tolerance.

The result separates individual overpay, whole-team gain/loss, and target cover.
These are discussion candidates, not assured bargains, predictions of acceptance,
or proof that a tiny projected gain will occur. No deal is forced if filters fail.

### Schedule-aware future games

Choose the actual completed fixture in Lock-In Advisor. The remaining count is
filled from the loaded schedule; backward induction evaluates those future games
in their actual order, using their individual context factors for both points and
head-to-head objectives. A contradictory manual count stops the calculation.
The manual-count option keeps the unadjusted independent-game calculation. The
reference threshold table is explicitly unadjusted. Final-game DNP is still zero.

`src/schedule/context.py` fits ridge coefficients on relative fantasy-points per
minute residuals using the latest loaded historical season. Features are opponent,
home venue and team back-to-back status. Rows require ≥10 minutes; player baselines
require ≥15 training games and >0.2 FP/min. Validation uses the first 80% of dates
for training and later dates for testing; test player baselines use training only.
At least 1,000 training and 250 test rows are required. Ridge penalty is 200.
The model must improve held-out residual MSE by ≥0.2%, then refits on all usable
history. Opponent coefficients require ≥100 rows; missing opponents stay neutral.
The total applied change is capped at ±10%. Disabling the checkbox is neutral too.

The September 8 check used 16,981 training / 4,118 held-out rows from 2025–26.
Residual MSE fell from 0.217852 to 0.212198 (about 2.60%). This is one historical
holdout, not calibrated future accuracy or causal defense/pace effects. New team
personnel, role shifts, travel, injuries and rookie context are not separately
modeled. Back-to-back is a team-calendar feature, not proof a player appeared the
previous day. Counts can understate rest/opportunity when source fixtures are missing.

### Refresh NBA performance

The separate **Refresh NBA performance** button downloads each configured training
season via the existing [NBA API adapter contract](https://github.com/swar/nba_api/blob/master/docs/nba_api/stats/endpoints/playergamelogs.md),
validates it, rebuilds the forecast/context factors, and publishes only after
verified complete active-roster coverage. It preserves raw downloads in versioned
snapshots and does not change ownership, schedule CSV, rookie priors, role inputs,
or season weights. Current configured seasons are 2025–26, 2024–25 and 2023–24;
adding 2026–27 in-season weighting requires a reviewed model/config update.

The UI distinguishes download time, latest historical game date, forecast build
time and Sleeper ownership time. Failure keeps the last working forecast. See
`docs/league_state.md` for storage, concurrency and recovery limitations.

### Revised default: surplus for added depth

Following the Luka-for-Cade-and-Duren counterexample, the default automatic
search is now **Surplus for added depth**, with **surplus wings** as the outgoing
pool, the single highest-priority position as its target, and a 1 FP/week minimum
gain for the user. It is configurable: choose another target, any surplus
position, or **Broader trade search** for star packages and larger overall gains.

For an added-depth result, all existing players eligible at the target position
must remain after cuts. At least one directly received player must add eligibility
there (a waiver claim does not qualify). All original positional leaders are
protected. Every outgoing player must come from the selected source group where
the user has more eligible players than the other manager. Wings mean unique
SG/SF/PF-eligible players without C eligibility (C/PF bigs are not wings); this is relative
roster breadth, not an independently calibrated market-surplus metric.

The after-trade best legal lineup, with the SAME original target-position leader
removed, must score more than the corresponding before-trade lineup. This proves
stronger cover in that specific scenario rather than a smaller loss from replacing
the leader with a weaker player. Both teams must also pass the overall gain test.
This does not establish that an opponent will accept; current valuations have no
external market-price model and injury insurance is still approximated.

**No free-agent claims by default.** Both browser trade tools start with claims
off, so the displayed gain must come from the offer itself after required cuts.
Opening an automatic proposal in the manual evaluator preserves this setting.
The optional waiver-enabled analysis is visibly labeled when a result depends
on pickups. The legacy CLI retains its explicit `--no-replacements` switch.

Automatic search now also defaults to the existing pessimistic stress filter:
your gain must remain positive and the other team's nonnegative at the entered
stress (10% initially). Results which pass stress sort ahead of speculative ones,
then by your overall projected gain and mutual fit, with needs as a later tiebreak.
Unchecking this filter explores speculative cases; it does not turn them into
validated recommendations. The current snapshot can yield no qualifying offers
under the stricter defaults. The tool must not force a suggestion in that case.

For all trade comparisons, positive injury-cover credit is withheld if the
position's leader changes. Production improvements and cover deterioration are
still counted. Before/after raw scores remain visible, but search uses the new
`search_improvement` field. The comparison labels explain this difference.
This conservative rule avoids calling a downgrade of Luka “better PG cover.”

### Positional priorities and need-focused trades

**Positional Needs** ranks PG, SG, SF, PF and C using the same projected roster
snapshot as the trade tools, separately for regular season and playoffs. The
rankings are also shown under My Team. Comparisons are withheld unless every
league team has a complete roster projection for that horizon.

For each position, its leader is the eligible player with the highest
`conditional weekly value × weekly usable probability` (stable ID breaks ties).
That leading-player quality is ranked against the other teams' leaders; tied
values share rank. It is **not** a rank of all starters at that position.
The injury-cover measure is the loss in the best legal healthy lineup if that
leader is unavailable, after moving other players into legal slots.

The transparent priority heuristic is:

```text
production gap = max(0, other teams' median leader quality − your leader quality)
cover gap      = max(0, your leader-out lineup loss − others' median such loss)
need score     = production gap + cover gap
```

Higher scores mean more need. A missing eligible position is surfaced first;
positions with no usable starting slots in the league format are excluded.
Zero means neither gap is above its benchmark, not that an upgrade is impossible.
Bench-eligible players are listed as context, not counted as a separate arbitrary
bonus. All league eligibility and shared starting slots are respected.

**Star reliance is not weak starting quality.** An elite guard may rank first
in projected quality and still make guard injury cover the highest priority.
Raw outage loss is conditional on the leader being absent, not multiplied by
that player's absence probability. Different positions can share a leader or
replacement, so their scores overlap. Summed need reduction is only a ranking
heuristic, not an additive fantasy-points gain. The trade display separates
eligible-player count, leading-player quality, and whole-lineup gain with the
original leader absent. These overlapping whole-lineup scenarios must not be
added. Need-index changes are secondary diagnostics, not positional FP gains.

### Joint availability and injury review (September 7 model correction)

Roster valuation now averages joint availability of starters and backups, with
exact integration in leagues with up to eight active roster spots and 512 fixed
antithetic scenarios otherwise. Weekly absences are assumed independent across players;
profile screens and candidate screening are approximate. The manual evaluator
and up to 60 automatic finalists are re-evaluated exactly, then filtered again
before any offer is shown. See `docs/trade_evaluation.md` and regression tests
in `tests/test_trade_health.py` for the calculation and injury sensitivity rules.

No medical return date is invented. Historical availability remains a baseline,
not an ACL recovery forecast. Injury-flagged assets are excluded from automatic
offers and claims; manual results expose the status, observation timestamp and
the all-flagged-players-unavailable sensitivity. PG depth is shown as an eligible
count, not a promise those players will be healthy.

In **Broader trade search**, **Strengthen my top needs** uses the first two
positive priorities, with options for a particular position or **Largest
overall gain**. A focused result must improve the selected position(s)' summed
comparable score without worsening the sum across all positions, **and** still pass
the existing overall gains and stress filters for both teams. Individual
positions can worsen; every result lists improvements and deteriorations.
Stress resilience and overall gain now rank ahead of comparable need reduction.
If there are no measured top needs, broader search falls back to overall gain.
Added-depth mode instead asks for an explicit target or a switch to broader search.
Targeting a zero-gap position can yield no results; this is explained rather
than inventing a need. Package search remains a shortlist, not exhaustive.

Both the original manual evaluator and automatic proposals show each side's
before/after scores, leading-quality change and leader-out loss change, including
cuts and assumed claims. Peer medians remain fixed at the pre-trade snapshot
so a changed benchmark cannot masquerade as a roster improvement. A lower need
score alone is never sufficient to recommend a trade. `team-profile` returns
the same rankings; `league-trade` includes both teams' positional changes.

The full Sleeper player catalogue is already loaded. Tradeable candidates are
the current active fantasy rosters with projections and valid eligibility;
IR assets, picks, unprojected players and future IR activations are not searched.
All rostered 1-for-1 pairs are evaluated across every complete opponent.
For Emil's current 14-player roster against nine 14-player opponents this is
1,764 swaps. The unowned, projected ACT/team-qualified pool supplies potential
claims; search it directly under **Free Agents** for add/drop suggestions.

Optional package search is deliberately **not exhaustive**: per opponent, take
the twelve highest mutual-fit single-swap seeds; combine disjoint assets into
2-for-2s and form unequal packages from at most six outgoing/incoming assets.
Score unequal packages without claims, then fully evaluate the best eight
with the entire eligible replacement pool. This heuristic can miss a package
whose value appears only after a particularly strong pickup. The UI reports
screened and fully evaluated counts separately.

Outside the explicitly exploratory **Pay for positional fit** mode, every
displayed proposal passes the selected minimum gains for both teams,
and your gain must be positive. Optional pessimistic screening requires your
stress result to stay positive and theirs nonnegative. Projection stress is
not a probability interval. Final results use the unchanged exact manual
evaluator's lineup, cut and claim logic. A pickup is assumed available after
waiver clearance, not guaranteed. At most three results per opponent are shown,
ordered by stress resilience, then your gain and mutual fit, with need reduction
as a later tiebreak (added-depth candidates are screened for actual fit first).
No acceptance percentages are invented.

Fair enough here means positive modeled roster utility under the chosen
filters. A manager may value players, name recognition, injuries, dynasty
upside or roster construction differently. No ADP/market-value source is
configured. These proposals should start a discussion, not be treated as
guaranteed profit or acceptance. Head-to-head state affects Lock-In decisions;
trade rankings still optimize expected lineup FP/week, not title probability.
