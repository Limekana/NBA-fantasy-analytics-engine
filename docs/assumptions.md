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
| 1 | **50+ point bonus stacks with 40+** | `league.yaml → bonus_rules.points_thresholds_stack` | Elite scorers overvalued by 3 pts per 50-point game. **UNVERIFIED — see `docs/lock_in_mechanics.md` §3.** |
| 2 | **League settings are as transcribed** | all of `league.yaml` | Everything downstream is wrong. Run `verify-league`. Handoff §1 says explicitly not to assume these are final. |
| 3 | **Games-per-week from historical/fallback schedule** | `model.yaml → schedule.fallback_games_per_week_pmf` | Lock-In value is schedule-sensitive; players on dense schedules are underrated. Fix by ingesting the real 2026-27 schedule. |
| 4 | **Manager makes optimal lock decisions given no future knowledge** | `model.yaml → lockin.primary_strategy: optimal_iid` | If you are less attentive, real value sits between `lockin_auto` and `lockin_value` — both columns are on the board so you can see the range. |
| 5 | **A player's future FP distribution has the same *shape* as their past** | `src/valuation.py → rescale_distribution` | Rescaling preserves CV and skew. Wrong for players whose role changes shape, not just level (e.g. a bench scorer becoming a starter). |

## Tier 2 — affects individual players

| # | Assumption | Where | Notes |
|---|---|---|---|
| 6 | Season blend weights 0.60 / 0.30 / 0.10 | `model.yaml → projection.season_weights` | Handoff §6 says these should be **empirically tested, not assumed**. They currently are assumed — see Known Gaps below. |
| 7 | Age curve (peak ~27, decline after 30) | `model.yaml → projection.age_curve` | Standard shape, not fitted to this data. |
| 8 | Availability baseline by age | `model.yaml → games_played.baseline_availability` | Regresses last season's games played toward an age norm rather than trusting it (handoff §7). |
| 9 | Small samples shrink toward the positional mean (k=20 games) | `model.yaml → projection.shrinkage_games_k` | Protects against 8-game breakouts ranking top-20. |
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

## Known gaps (honest list)

1. **No real NBA data was ingested** — network blocked. Every number currently
   produced by `demo` is synthetic.
2. **Backtesting is not implemented.** Handoff §20 calls it *mandatory before
   trusting the model*, and it genuinely is: without it, the season weights (#6),
   the age curve (#7) and the shrinkage constant (#9) are educated guesses rather
   than empirical results. This needs ≥2 seasons of real logs to run at all, so
   it could not be built here. **Do not treat model rank as validated until this
   exists** — the honest framing today is that the board is a well-specified
   *hypothesis*, not a backtested model.
3. **No strategy comparison** (handoff §19) — depends on backtesting.
4. **Starter flag is not ingested**; minutes are used as the role proxy.
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
