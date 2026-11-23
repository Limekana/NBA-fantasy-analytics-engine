# Trade evaluation

September 6 update: the original one-roster API remains supported. The new
post-draft layer adds ownership validation, two-sided evaluation, marginal cuts,
replacement claims, team profiles, partner screening and separate playoff values.
See [league_state.md](league_state.md) for the schemas, commands and exact
assumptions. New league forecasts distinguish conditional weekly value from
weekly availability; game availability is no longer used as a weekly outage rate.

## The question

"Is this trade good?" is not answerable in the abstract. The answerable question
is "does this trade raise the number of points my starting lineup scores in a
week?" — and the two come apart constantly, because a trade changes the *shape*
of a roster as well as its total.

Everything in `src/trade.py` follows from taking the second question literally.

## Why adding up the two sides is wrong

**Only nine players score.** The league starts PG, SG, G, SF, PF, F, C and two
UTIL, out of fourteen rostered. Five of your players contribute nothing in any
given week except as insurance. So a player's worth to you is their *marginal*
contribution to your best legal lineup, and that depends entirely on who else
you have. The third startable centre on a roster is worth a fraction of the
first, and no projection — however accurate — contains that fact.

**Depth is insurance, and it has a price.** Under Lock-In a benched player scores
exactly zero; bench value is entirely contingent on somebody else being out. A
two-for-one that upgrades your best starter also removes the cover behind him.
That cost is real, it is invisible to a talent comparison, and it is frequently
the whole difference between a good deal and a bad one.

## The model

    roster value = best_lineup(roster) − E[points lost to absences]

evaluated before and after the swap. The difference is the verdict.

### Best lineup

A maximum-weight bipartite matching between players and slots, respecting
`position_eligibility` from `config/league.yaml`. Solved exactly.

The obvious shortcut — walk down the roster by value, give each player their best
free slot — is wrong in a way that matters. It will spend a UTIL slot on a guard
and then discover the only remaining centre has nowhere to sit. The error is
small most weeks and largest exactly when a trade changes your positional shape,
which is when you are asking the question.

Python solves it with `scipy.optimize.linear_sum_assignment`. The dashboard,
which has to run offline in a browser, solves it greedily with augmenting paths —
also exact, because the sets of players that can fill distinct slots form a
transversal matroid, and greedy is optimal on a matroid. `tests/test_trade.py`
checks the Python solver against exhaustive brute force, and then runs the
JavaScript under Node against the Python on randomised rosters, because the same
mathematics existing twice is only safe if something breaks when they diverge.

### Marginal value

For each starter: re-solve the lineup without them, and take the difference. That
is what a week without that player actually costs — the gap to whoever slides up,
not the player's whole projection.

This is the single most useful number the module produces. A player with a large
projection and a small marginal value is one you are already covering, which
makes him the piece to trade rather than the piece to keep. Both the CLI and the
dashboard print the list lowest-first for that reason.

### The cost of absences

Expected strength now averages the best legal lineup over joint availability
of **all** players, including backups. An unavailable backup contributes zero;
two absent starters cannot both use the same replacement. Depth cost is the
healthy total minus this expectation. Healthy single-outage losses remain useful
descriptions, but are no longer summed to estimate roster strength.

The alternative — multiplying every player's value by their availability — prices
an absence as though nobody replaced the missing player. That is never what
happens, and it systematically overvalues durability while hiding the value of a
bench.

**Known approximation.** Weekly availability is independent between players.
Leagues with up to eight active roster spots are integrated exactly; others use 512
fixed antithetic scenarios, keyed by stable player IDs and shared across trades.
This is an approximation, not an exact season forecast or a confidence interval.
Small differences can reflect numerical integration error as well as projection
error. Team-wide correlated absences and recovery dates are not inferred.
The integration mode is fixed by league size, so removing a player cannot switch
methods and manufacture a gain. Adding a zero-availability player has zero value.

The two-team manual evaluator and up to 60 automatic-search finalists use exact
enumeration of joint weekly availability (bounded to 20 uncertain assets). All
displayed automatic offers are filtered and ranked again using those exact
values, including roster cuts, claims, projection stress and injury sensitivity.
This removes numerical screening error from displayed offers, not uncertainty
in player projections or the independence assumption. Broad profile screens
remain approximate and may differ slightly from exact evaluator baselines.

Automatic trades and suggested claims exclude injury-flagged assets. Both teams
must also avoid a loss when all remaining flagged players are unavailable
together (your side must improve). With robust filtering enabled, that scenario
also receives the selected projection stress. This tests dependence on injured
cover; it does **not** assume flagged players miss the entire season. Manual
offers remain inspectable but moving flagged assets requires injury review.
Blank injury metadata is not verified health. Flags reflect the saved Sleeper
observation, whose date is shown in the dashboard.

## Pricing draft picks

A pick is worth the expected value of the best player *still available* when it
comes round — which is not the player ranked at that slot, because boards never
go to plan. `pick_value_curve()` simulates the draft with the same opponent model
the availability tool uses (`draft_simulation` in `config/model.yaml`), takes the
best available player at each target pick, and averages over simulations.

The resulting curve falls steeply and then flattens, which is the honest shape:
by the middle rounds the remaining players are close to interchangeable, and a
pick's value collapses toward replacement level.

### What position is a pick?

Letting a pick fill whichever slot happens to be open is the tempting
simplification and it is badly wrong: it silently converts "I have nobody at
shooting guard" into a guaranteed shooting guard, and prices two picks that land
on completely different positions identically.

So a pick carries the slots of the positions that actually came back in the
simulation, taking them in order of likelihood until 80% of outcomes are covered
(`PICK_POSITION_COVERAGE`). A pick that produces a centre nine times in ten is
eligible at C and UTIL and nowhere else; one whose outcomes are spread across the
board keeps most of its flexibility, which is correct — that spread *is* the
option value.

**It is still an average.** A pick is priced at the mean outcome, which ignores
that the player who falls to you may not be one you want. That is flagged
wherever a pick appears in a trade.

### Rosters that cannot fill themselves

If your roster has nobody eligible for a slot, the lineup solver leaves it empty
and scores it zero — which is what Sleeper does. An incoming player who fills
that slot is then credited with their entire value.

For a complete roster that is simply correct. Mid-draft, when your roster is
three players and seven holes, it makes every acquisition look enormous. The
evaluator detects this and says so, because at that point the useful comparison
is between picks, not between a pick and a lineup that does not exist yet.

## Uncertainty

The verdict is stress-tested by marking the incoming side down by a fixed
fraction and the outgoing side up by it, then reversing. If that flips the sign,
the output is `TOO CLOSE TO CALL` rather than a number.

The choice of *which* uncertainty to test is deliberate. Week-to-week scoring
variance is larger, and it is the obvious thing to model — but it averages out
over a season and is therefore nearly irrelevant to a trade you will hold for
twenty weeks. Projection error does not average out: if the model is wrong about
a player, it is wrong about him every week. So the stress test targets the error
that actually survives repetition.

The default is 10%. Raise it with `--stress 20` when the players involved are
rookies, injury returns, or anyone whose role you are guessing at.

## What this deliberately does not model

- **Opponent-specific schedule.** Lock-In is a points format; who you play in a
  given week does not change what your lineup is worth.
- **Playoff-week schedules in the legacy command.** Use the new `league-trade
  --horizon playoffs` or League desk for a separate playoff-window result. The
  legacy one-roster `trade` command retains its supplied board's horizon.
- **Trade-market psychology.** Whether the other manager will accept is not a
  quantitative question, and pretending otherwise would be dressing up a guess.
- **Roster moves in the legacy command.** Automatic replacement claims are
  modeled in `league-trade`, with waiver-access caveats; the old `trade` command
  remains a no-replacement evaluation. Both paths now cut by marginal impact.

## Commands

```
python -m src.cli trade --roster my_roster.txt --give "A,B" --get "C"
python -m src.cli trade --roster my_roster.txt --give "A" --get-pick 15 35
python -m src.cli trade-dashboard --slot 4
```

See `README.md` Step 12 for the beginner walkthrough.
