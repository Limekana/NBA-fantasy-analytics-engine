# Assumption register

Engineering Rule 7: *separate facts from assumptions.*

```
FACT:         Player averaged 37.4 FP/game in 2025-26.
ASSUMPTION:   Projected 32 minutes/game in 2026-27.
MODEL OUTPUT: Projected 41.2 FP/game.
```

This file lists every assumption the system makes, where it lives, and how much
damage it does if wrong. Anything not listed here should be a fact derived from
game logs — if you find one that is not, it is a bug.

## How assumptions surface in output

- `outputs/draft_board.csv` has an `assumption_notes` column, per player.
- The pipeline prints a `WARNINGS` block naming every global assumption in play.
- `docs/lock_in_mechanics.md` tracks the rules-level assumptions separately.

---

## Tier 1 — could materially change the draft board

| # | Assumption | Where | If wrong |
|---|---|---|---|
| 1 | **50+ point bonus stacks with 40+** | `league.yaml → bonus_rules.points_thresholds_stack` | Confirmed by the league manager on 2026-09-06 and recorded in `league.yaml`. |
| 2 | **League settings are as transcribed** | all of `league.yaml` | Everything downstream is wrong. Run `verify-league`. Handoff §1 says explicitly not to assume these are final. |
| 3 | **The 2026-27 NBA schedule is available, but fantasy-week boundaries remain an assumption** | `data/external/schedule_2026-27.csv` and `league.yaml → calendar` | Schedule-derived games per week are used; confirm the league's week-one anchor and any NBA Cup schedule changes. |
| 4 | **Manager makes optimal lock decisions given no future knowledge** | `model.yaml → lockin.primary_strategy: optimal_iid` | If you are less attentive, real value sits between `lockin_auto` and `lockin_value` — both columns are on the board so you can see the range. |
| 5 | **A player's future FP distribution has the same *shape* as their past** | `src/valuation.py → rescale_distribution` | Rescaling preserves CV and skew. Wrong for players whose role changes shape, not just level (e.g. a bench scorer becoming a starter). |

## Tier 2 — affects individual players

| # | Assumption | Where | Notes |
|---|---|---|---|
| 6 | Season blend weights 0.60 / 0.30 / 0.10 | `model.yaml → projection.season_weights` | These apply to `projection.method: model`; `blend` uses each player's latest available season. Run rolling holdouts before changing weights. |
| 7 | Age curve (peak ~27, decline after 30) | `model.yaml → projection.age_curve` | Standard shape, not fitted to this data. When DOB is available, age is computed at Oct 1 of the projected season. |
| 8 | Availability baseline by age | `model.yaml → games_played.baseline_availability` | Regresses last season's games played toward an age norm rather than trusting it (handoff §7). |
| 9 | Small samples shrink toward the positional mean (k=8 games) | `model.yaml → projection.shrinkage_games_k` | Protects against small-sample breakouts ranking too highly. |
| 10 | Bonus rates scale with production, exponent 2.0 (thresholds) / 1.3 (DD-TD) | `src/valuation.py → project_bonus_points` | A deliberate approximation of a tail effect. Documented in the module; a full re-simulation would be more precise but less inspectable. |
| 11 | Rookies need a manual prior | `assumptions.yaml → rookies` | No NBA game logs exist for them, so they are **absent from the board** unless you add one. |
| 12 | Role uncertainty defaults: 0.25 veteran / 0.55 under 30 games | `src/projections/baseline.py` | Override per player in `assumptions.yaml → role_uncertainty`. |
| 13 | Availability reduces usable *weeks*, not a season point total | `src/valuation.py` | Correct for Lock-In: missing one game of a 4-game week costs little; missing a whole week costs everything. |

## Tier 3 — draft simulation

| # | Assumption | Where |
|---|---|---|
| 14 | Opponents: 60% ADP-followers, 20% value, 20% need-based | `model.yaml → draft_simulation.manager_archetypes` |
| 15 | ADP deviation SD = 6 picks; 20% chance of going off-board | `model.yaml → draft_simulation` |
| 16 | Players without ADP are drafted at their model rank | `src/draft/simulator.py → board_to_players` |

---

## When the model loses to the baseline

`backtest` may report that the model ranks players no better — or worse — than
"last season's FP/game". That is a real finding, not a bug, and the system is
built to surface it rather than hide it (handoff Rule 5).

**Investigate before deciding anything:**

```bash
python -m src.cli backtest --diagnose
```

It answers two questions in order:

1. **Is the gap real?** It bootstraps over players and reports a 95% interval on
   the difference. A gap of ~0.01 Spearman on a few hundred players is usually
   inside the noise, and the interval will say so. "Not proven better" is a
   different conclusion from "proven worse", and only the second demands a fix.

2. **Which component is at fault?** It ablates each piece — age curve,
   shrinkage, multi-season blend, bonus projection — and reports which one the
   model scores *better* without. That is the thing to change.

### Why the age curve often goes neutral after switching to `last_season`/`blend`

A result worth understanding rather than just accepting: once the multi-season
blend is switched off, the age curve frequently stops mattering — the ablation
reports it as reshuffling players while changing accuracy by nothing.

The age curve may add little over one season because those statistics already
reflect an age close to the projected season. With multiple seasons, it can
adjust older rates forward. The DOB-based age is aligned to the projected season,
so the current download date cannot leak a future age into a past holdout.

**Caveat before flattening it entirely:** the backtest scores only players with
≥20 games in both the prior and target seasons. That excludes rookies (no prior
season) and players whose age ended their season — precisely the cases where an
age adjustment matters most. "Neutral on the measured population" is not the same
as "neutral on the board". Softening the curve is safer than deleting it.

**The important structural point:** the projection layer and the Lock-In layer
are independent. If the simple projection ranks better, use it —

```yaml
projection:
  method: last_season
```

— and you keep the Lock-In valuation, distributions, games-played model,
schedule and risk scoring on top of it. None of those come from the baseline,
and none of them are invalidated by the projection being simple. Losing the
projection contest does not mean losing the board.

## Known gaps (honest list)

1. **The two latest full-season projection holdouts are mixed.** On 357 players
   with at least 20 games in both the prior and target seasons, the 2024-25
   model scored 0.8816 Spearman versus 0.8766 for prior-season fantasy points
   per game; its paired 95% bootstrap interval was [+0.0002, +0.0099]. On 358
   players in 2025-26, the model scored 0.7845 versus 0.7810; the interval was
   [-0.0034, +0.0108]. One small holdout gain is not replicated in the next
   season, so these results do not establish a reliable improvement. DOB-based
   ages are aligned to each target year, but neither holdout has archived
   season-specific positions; current positions are used and shrinkage findings
   remain provisional. New archives capture positions. Both holdouts exclude
   rookies and low-game players. Re-run `python -m src.cli backtest --diagnose`
   after each completed season.
2. **The current projection method is `blend`, which means each player's latest
   available season only** with age adjustment and shrinkage. The `model` method
   uses configured multi-season weights. The backtest diagnosis output now
   states the active method so those options are not confused.
3. **In-season player-week outcomes are now archived**, but Sleeper does not
   expose which NBA game or lock timestamp produced each player's weekly score.
   The archive can compare weekly fantasy outcomes and preserve team results;
   it cannot reconstruct each manager's lock decisions for a decision-strategy
   backtest. The projection backtest uses per-game NBA logs scored with the
   league rules, not these weekly Sleeper outcomes. Immutable pre-week team
   forecasts are not saved yet, so the dashboard's weekly team forecast
   accuracy is not validated by the current archive.
4. **Current-season games update projections through a validated blend**
   (`src/projections/in_season.py`, `in_season` in `config/model.yaml`). Rates
   still train on completed seasons; games archived by `league-archive` then
   move minutes (weight games/(games+4)) and per-minute production (weight
   minutes/(minutes+360)). The weights were chosen on 2024-25 and tested on
   2025-26 and the reverse: rest-of-season FP/game Spearman after 7 days
   0.787 -> 0.853 and 0.882 -> 0.908, after 28 days 0.777 -> 0.877 and
   0.873 -> 0.922. Early games alone were worse than the blend. Players with no
   preseason projection are not created from a few games. Availability and
   Lock-In score shapes still come from completed seasons. A separate
   regression-to-the-mean minutes model was tested and rejected: it improved
   2025-26 but fell below the naive baseline on 2024-25.
4. **Starter flag is not ingested from the NBA game-log source**; minutes are
   used as the role proxy.
5. **Rookies are excluded** unless manually added.
6. **Playoff-schedule weighting** is implemented (`playoff_week_pmf`) but not yet
   wired into the headline valuation.

## Adding an assumption

Put it in `config/assumptions.yaml` with a `note` recording **the source and the
date**. It will appear in the player's `assumption_notes` on the board.

```yaml
injuries:
  "Player Name":
    games_missed: 20
    ramp_minutes_penalty: 3.0
    note: "Achilles repair Feb 2026; Aug 2026 reporting suggests December return."
```
