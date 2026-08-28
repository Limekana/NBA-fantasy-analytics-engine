# Sleeper Lock-In mechanics: what was verified, and what was not

Handoff §2 requires that Lock-In rules be **researched and verified before
implementing**, and §4 requires that bonus stacking not be silently assumed.
This document records what was actually established, what remains an assumption,
and exactly how to close each gap.

Research date: **2026-08-28**. Draft: **2026-08-30**.

> **Environment limitation.** This repository was built in a sandbox whose egress
> policy returned HTTP 403 for `support.sleeper.com`, `stats.nba.com`,
> `basketball-reference.com`, `api.sleeper.app` and `rotowire.com`. Primary
> Sleeper documentation could not be fetched directly. Findings below come from
> search-result summaries of Sleeper's own support articles and reputable
> secondary sources. **Every item marked UNVERIFIED must be confirmed by you in
> the Sleeper UI before the draft.**

---

## 1. Core Lock-In rule — VERIFIED (consistent across three sources)

- **Only one game per player counts toward the fantasy week.**
- After a player's game finishes, the manager may **lock in** that performance.
- The lock must happen **before that player's next game starts**. Once the next
  game tips, the previous performance can no longer be selected.
- **Locks are irreversible.** Once submitted it cannot be undone or changed.
- The player must be **in the starting lineup before the game** to be lockable,
  and cannot be moved to another position after locking.
- You cannot pre-lock a future game.

Sources:
- [Sleeper Support — Lock-In Mode Details](https://support.sleeper.com/en/articles/6522833-lock-in-mode-details)
- [FantasyPros — What is Lock-In Mode?](https://www.fantasypros.com/2023/10/sleeper-nba-lock-in-fantasy-basketball-what-is-lock-in-mode/)
- [RotoWire — How to Play Sleeper's Lock-In Mode](https://www.rotowire.com/basketball/article/nba-fantasy-how-to-play-sleepers-new-lock-in-mode-75705)
- [Sleeper blog — Lock-In Mode](https://sleeper.com/blog/lock-in-mode-fantasy-basketball-re-imagined-again/)

### The auto-lock rule, and why it dominates the model

> "Should you forget to Lock-In a game for a player, Sleeper will automatically
> take the fantasy points scored in their team's **final game of the week**."

This single sentence is the most consequential fact in the whole document, and it
is easy to get backwards. The do-nothing baseline is **the last game**, not the
best game and not the average. Three concrete consequences:

1. **Inattention is expensive and quantifiable.** A manager who never locks
   scores each player's last game — an unconditioned draw from their
   distribution, i.e. their raw mean. Every point of Lock-In value above the mean
   is earned by actively deciding.
2. **A model that used `max()` as the default would be badly wrong.** It would
   price perfect clairvoyance as the baseline. The code implements `perfect` as
   an explicitly labelled **upper bound** only, per handoff §2.
3. It is configurable anyway: `lock_in.auto_lock_fallback` in
   `config/league.yaml` accepts `last_game` (Sleeper's actual behaviour),
   `first_game`, or `best_game`.

Implemented in `src/lockin/simulator.py`; pinned by
`test_auto_lock_fallback_takes_last_game_even_when_it_is_bad`.

### What this implies for valuation

| Ordinary fantasy | Lock-In |
|---|---|
| Value ≈ FP/game × games played | Value ≈ Σ over weeks of E[best *chosen* game] |
| Consistency is prized | **Variance is an asset** — the week keeps the good draw and discards the bad ones |
| An extra game adds its own points | An extra game adds an **option**, not points |
| Schedule is a minor tiebreak | Games-per-week is a first-order value driver |

The variance result is the one that most often surprises people, and it is
tested: `test_volatility_is_an_asset_in_lock_in` shows two players with an
identical 35.0 FP mean differing by ~5.8 FP/week purely from spread.

---

## 2. Double-double / triple-double stacking — VERIFIED

> "If using both scoring options, they will stack on top of each other. That
> means if a player gets a Triple-Double, they'll get the point values for both
> double-double and triple-double."
> — Sleeper Support, *What scoring settings are available?*

So a triple-double under our config pays **3 + 2 = 5** bonus points.

Categories counted: **points, rebounds, assists, steals, blocks** (10+ in two for
a double-double, three for a triple-double).

- Config: `bonus_rules.triple_double_stacks_with_double_double: true`
- Both branches tested: `test_10_pts_10_reb_10_ast_is_a_triple_double`,
  `test_triple_double_without_stacking_pays_only_td`

Source: [Sleeper Support — What scoring settings are available?](https://support.sleeper.com/en/articles/4645009-what-scoring-settings-are-available)

---

## 3. 40+ / 50+ point stacking — **UNVERIFIED. ACTION REQUIRED.**

**This could not be confirmed.** Sleeper's general documented principle is that
scoring settings stack, and 40+/50+ are separate independent threshold settings,
so stacking is the more likely behaviour — but no source states it explicitly and
the primary documentation was unreachable from the build environment.

**Current assumption:** `bonus_rules.points_thresholds_stack: true`
→ a 50-point game pays **3 + 4 = 7**.
**If false**, it pays only **4**, and every high-scoring player is overvalued by
3 points per 50-point game.

### How to close this in two minutes

```bash
python -m src.cli scoring-check
```

Find the line marked `50 pts exactly  <-- KEY TEST`. Compare it against a real
50-point game in your league's Sleeper scoring view (or the league's scoring
settings page). If they disagree:

```yaml
# config/league.yaml
bonus_rules:
  points_thresholds_stack: false
```

Then re-run `python -m src.cli build-board`. Nothing else needs to change — the
switch is read by both the scalar and vectorised scoring paths, and both branches
are already tested (`test_50_points_exactly_stacks_both_point_bonuses` and
`test_50_points_without_stacking_pays_only_the_higher_tier`).

**Impact if wrong:** moderate, and concentrated. It affects only players with a
meaningful 50-point rate — a handful of elite scorers, precisely the players
drafted in the first two rounds.

---

## 4. Other assumptions in the scoring engine

| Assumption | Config key | Status |
|---|---|---|
| Quadruple-double pays the triple-double bonus | `quadruple_double_pays_triple_double` | ASSUMED true (satisfies the ≥3 condition). Near-zero practical impact. |
| No 60+ point tier exists | — | Follows from the league settings as given; 60 points scores the same bonus as 50. |
| Fantasy weeks run Monday–Sunday | `calendar.week_start_weekday: 1` | ASSUMED standard for Sleeper NBA. Verify against your league's matchup boundaries. |
| A bonus absent from config never fires | — | Enforced: `test_removing_a_bonus_from_config_disables_it`. |

---

## 5. Verify the whole config against the live league

Once the league exists on Sleeper, set `meta.sleeper_league_id` in
`config/league.yaml` and run:

```bash
python -m src.cli verify-league
```

This diffs Sleeper's live `scoring_settings` against the YAML and reports any
mismatch. **Every valuation in this system is downstream of those numbers**, so
this is the single highest-value check available before the draft. It needs
network access to `api.sleeper.app`.
