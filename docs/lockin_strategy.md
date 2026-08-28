# How to actually play Lock-In

Everything here is measured by `python -m src.cli backtest`, not asserted.

---

## The rule that drives everything

Only one game per player counts each week. You lock a completed performance
before that player's next game tips. **If you never lock, Sleeper takes their
final game of the week.**

That last sentence is the one people get backwards, and it sets the baseline: an
inattentive manager scores each player's *last* game — an unconditioned draw from
their distribution, which in expectation is simply their season average. Every
point above that average is earned by deciding.

## The four numbers on the draft board

| Column | What it is |
|---|---|
| `lockin_auto` | You never lock. **Lower bound**, equals raw FP/game. |
| `lockin_value` | Optimal stopping, no knowledge of the future. **The realistic estimate.** |
| `lockin_perfect` | Clairvoyance. **Upper bound only** — never a projection. |
| `lock_in_advantage` | `lockin_value − FP/game`. The edge Lock-In gives you. |

The handoff (§2) is explicit that the clairvoyant number must never be presented
as a projection, which is why `perfect` is labelled an upper bound everywhere.

---

## Is the 37% rule right? No.

The secretary problem's 1/e ≈ 36.8% rule — observe the first 37% of candidates,
then take the first one better than all of them — is genuinely optimal, but for a
different problem than the one Lock-In poses.

| | Secretary problem | Lock-In week |
|---|---|---|
| Objective | maximise P(picking the single best) | maximise **expected points** |
| Payoff | all-or-nothing; 2nd best scores zero | 2nd-best game scores nearly as much |
| Distribution | unknown; only relative ranks observable | **known** — the player's own game log |
| n | large | 2–4 |

All four differences push the same way, and the third is decisive: **we are not
guessing in the dark.** We know a player's scoring distribution, so we can compute
the exact expected value of continuing. The 37% rule's whole design is to infer
that from relative ranks when you *cannot* compute it. Using it here throws away
information we already have.

The small `n` makes it worse. With 3.4 games in a typical week, `floor(n/e) = 1`:
"watch Monday, then take the first game that beats it." In a two-game week,
`floor(2/e) = 0`, so there is no observation phase at all and it locks Monday
unconditionally — scoring exactly the same as doing nothing.

### Measured, on real chronological weeks

Not resampled — actual weekly sequences, replayed, with each player's decision
thresholds fitted leave-one-week-out so no week informs its own decision:

```
strategy         fp_per_week    share of available edge
optimal_iid           19.55                       100%
percentile            19.40                        95%
secretary (37%)       18.05                        49%
threshold (40 FP)     17.18                        17%
last_game (nothing)   16.63                         0%
perfect (oracle)      20.74                         —
```

The 37% rule beats doing nothing, but leaves half the edge on the table. Over a
22-week season across 9 starting slots that difference is on the order of
**800 fantasy points**.

---

## What the model uses instead

Optimal stopping by backward induction over the player's own empirical
distribution. With `k` games left in the week:

```
W[1] = E[F]                    the last game: you are forced to take it
W[k] = E[max(F, W[k-1])]       k games left: take F now, or continue
```

Facing a score with `k` games still to come, continuing is worth `W[k]`, so
**lock if the game in hand beats `W[k]`**.

The practical shape of this: early in the week you should pass on a good game,
because there is time for a better one. By Saturday the same score is an easy
lock. A 40-point game with three games left is a pass; with one left it is a
lock. The threshold falls as the week runs out.

Because `W[k]` is computed from the *empirical* distribution, it inherits each
player's real skew — which matters, because a boom/bust player's fat right tail
makes waiting far more valuable than a normal approximation would suggest.

---

## Three consequences for the draft

**1. Variance is an asset.** Two players averaging an identical 35.0 FP/game
differ by roughly **5.8 FP per week** if one is volatile and the other is not —
the week keeps the good draw and discards the bad one. This inverts the usual
season-long preference for consistency. It is why the board reports `std_dev`
and `ceiling` next to the mean rather than collapsing everything into one number.

**2. Games per week is leverage, not volume.** A fourth game in a week does not
add a fourth game's points — it adds a fourth *draw* to choose between. That is
worth much less than a whole game and much more than nothing, and it is why the
real schedule is the highest-value missing input in the system.

**3. Availability works differently.** Missing one game of a four-game week costs
almost nothing; missing the whole week costs everything. So the model reduces
usable *weeks* rather than scaling a season point total.

---

## Practical guidance for the season

- **Do not lock early in the week unless the game is genuinely big.** The
  threshold is highest on Monday and falls through the week.
- **The threshold is player-specific.** A 45 FP night is a lock for a role player
  and a shrug for a star. `percentile` captures 95% of the optimal edge with far
  less arithmetic, if you want a rule you can apply in your head: *lock anything
  above roughly the player's 70th percentile, and get greedier as the week ends.*
- **Never let a week auto-lock if you can help it.** Doing nothing is the single
  biggest cost available to you — larger than the gap between a good policy and a
  perfect one.
- **A fixed threshold ("always lock 40+") is the worst realistic policy tested**,
  capturing only 17% of the edge. It is wrong for both ends of the roster.

## Re-run these numbers on your own data

```bash
python -m src.cli backtest --lockin-season 2025-26
```

The strategy comparison needs no projection and no distributional assumption, so
it is the most trustworthy output in the system. Everything above comes from it.
