"""Trade evaluation: what a deal is worth *to your roster*, not in the abstract.

The naive way to grade a trade is to add up the players on each side and compare
the totals. That is wrong here, for two reasons that both come out of the league
format rather than out of basketball.

**Only the starting lineup scores.** This league starts nine and rosters
fourteen. A player's value to you is therefore not their projected output but
their *marginal* contribution to your best legal lineup - which depends entirely
on who else you already have. Your third startable centre is worth a fraction of
your first, and no amount of projection accuracy will tell you that; only the
assignment problem will. So every number in this module is a difference between
two solved lineups.

**Depth is insurance, and insurance has a price.** A two-for-one that upgrades
your best starter also thins the bench that covers your starters when they sit.
Under Lock-In a benched player scores nothing at all, so bench value is entirely
contingent - it shows up only in the weeks somebody is out. That is why roster
strength here is not the healthy lineup total but the healthy total minus the
expected loss from absences, and why a deal that looks like a clear win on
talent can grade out flat once the fifth guard is gone.

The two together mean a trade's verdict is a difference of *expected* lineups:

    value = best_lineup(roster) - E[loss when a starter is unavailable]

evaluated before and after the swap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

# Cost used for a (slot, player) pair the eligibility rules forbid. The matrix is
# always padded with enough zero-value dummy players to fill every slot, so an
# ineligible pair is never cheaper than leaving the slot empty - which makes this
# a hard constraint rather than a heavy penalty.
_INELIGIBLE_COST = 1e9


@dataclass(frozen=True)
class RosterPlayer:
    """A rostered asset, real or hypothetical.

    ``weekly_value`` is the Lock-In weekly expectation from the draft board
    (``lockin_value``), which already contains the schedule, the game-level
    distribution and the bonus corrections. ``availability`` is the probability
    the player is usable in a given week.

    Draft picks are represented as players too (``is_pick``), priced by
    :func:`src.trade.pick_value_curve` and eligible everywhere, because the point
    of a pick is that you have not committed to a position yet.
    """

    player_id: str
    name: str
    position: str
    weekly_value: float
    availability: float = 1.0
    is_pick: bool = False
    #: Explicit slot eligibility, overriding ``position``. Picks use this to say
    #: "realistically this lands at one of these positions" instead of claiming
    #: they can fill whatever hole happens to be open.
    slots: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class Lineup:
    """A solved starting lineup."""

    total: float
    assignments: tuple[tuple[str, str], ...]   # (slot, player_id), slot order preserved
    bench: tuple[str, ...]

    @property
    def starter_ids(self) -> tuple[str, ...]:
        return tuple(player_id for _slot, player_id in self.assignments)


@dataclass
class RosterStrength:
    """A roster's expected weekly output, and where it comes from."""

    healthy_weekly: float
    expected_weekly: float
    lineup: Lineup
    #: player_id -> points the lineup loses in a week that player is out. This is
    #: the player's true marginal value to *this* roster, and it is the number a
    #: trade should actually be argued over.
    marginal_value: dict[str, float] = field(default_factory=dict)

    @property
    def depth_cost(self) -> float:
        """Weekly points given up to absences. Small means the bench covers you."""
        return self.healthy_weekly - self.expected_weekly


def expand_slots(league_cfg) -> list[str]:
    """The starting lineup as a flat list of slots, e.g. [PG, SG, G, ..., UTIL, UTIL]."""
    slots: list[str] = []
    for slot, count in league_cfg.starting_slots.items():
        slots.extend([str(slot)] * int(count))
    return slots


def eligibility_map(league_cfg) -> dict[str, frozenset[str]]:
    """position -> the set of slots it may fill."""
    return {
        str(position): frozenset(str(s) for s in slots)
        for position, slots in league_cfg.position_eligibility.items()
    }


def optimal_lineup(players: Sequence[RosterPlayer], league_cfg) -> Lineup:
    """Best legal assignment of players to starting slots.

    This is a max-weight bipartite matching, solved exactly. Greedy assignment -
    take the highest-value player, give them their best slot, repeat - is the
    obvious implementation and is wrong: it will spend the UTIL slot on a guard
    and then find the only remaining centre locked out of the lineup. The
    difference is small most of the time and decisive exactly when a trade
    changes your positional shape, which is when you are asking.
    """
    slots = expand_slots(league_cfg)
    if not slots or not players:
        return Lineup(0.0, (), tuple(p.player_id for p in players))

    eligible = eligibility_map(league_cfg)
    n_slots = len(slots)
    n_players = len(players)

    # Pad with dummies so every slot can always be filled without ever having to
    # reach for an ineligible player. Dummies contribute nothing and are dropped
    # from the result; they are what "this slot stays empty" looks like.
    width = n_players + n_slots
    cost = np.full((n_slots, width), _INELIGIBLE_COST, dtype=float)
    cost[:, n_players:] = 0.0

    all_slots = frozenset(slots)
    for column, player in enumerate(players):
        # A pick carries its own slot set, derived from the positions the
        # simulation actually produced at that pick. Letting a pick fill any hole
        # - the obvious shortcut - credits it with whatever gap happens to be
        # open, which quietly turns "I have nobody at shooting guard" into a
        # guaranteed shooting guard.
        if player.slots:
            allowed = frozenset(player.slots)
        elif player.is_pick:
            allowed = all_slots
        else:
            allowed = eligible.get(player.position, frozenset())
        for row, slot in enumerate(slots):
            if slot in allowed:
                cost[row, column] = -float(player.weekly_value)

    rows, columns = linear_sum_assignment(cost)

    assignments: list[tuple[str, str]] = []
    used: set[int] = set()
    total = 0.0
    for row, column in zip(rows, columns):
        if column >= n_players or cost[row, column] >= _INELIGIBLE_COST:
            continue
        player = players[column]
        assignments.append((slots[row], player.player_id))
        used.add(column)
        total += float(player.weekly_value)

    # Report slots in configured order so two lineups read comparably.
    order = {slot: index for index, slot in enumerate(dict.fromkeys(slots))}
    assignments.sort(key=lambda pair: order.get(pair[0], 99))

    bench = tuple(p.player_id for index, p in enumerate(players) if index not in used)
    return Lineup(total=total, assignments=tuple(assignments), bench=bench)


def roster_strength(players: Sequence[RosterPlayer], league_cfg) -> RosterStrength:
    """Expected weekly output, charged for the weeks starters are unavailable.

    Absences are priced one at a time: for each starter, re-solve the lineup
    without them and charge the difference, weighted by how often they are out.
    Two absences in the same week are ignored, which understates the cost
    slightly - the second hole is always at least as expensive as the first,
    since it is filled from a shallower bench.

    Doing it this way rather than simply multiplying each player's value by their
    availability is what makes depth visible. Multiplying prices an absence as if
    nobody replaced the missing player; re-solving prices it as what it actually
    costs, which is the gap between the starter and whoever slides up. A deep
    roster barely notices; a top-heavy one bleeds.
    """
    healthy = optimal_lineup(players, league_cfg)
    if not healthy.assignments:
        return RosterStrength(healthy_weekly=0.0, expected_weekly=0.0, lineup=healthy)

    by_id = {p.player_id: p for p in players}
    marginal: dict[str, float] = {}
    expected_loss = 0.0

    for player_id in healthy.starter_ids:
        without = [p for p in players if p.player_id != player_id]
        loss = healthy.total - optimal_lineup(without, league_cfg).total
        marginal[player_id] = loss
        absence = 1.0 - float(np.clip(by_id[player_id].availability, 0.0, 1.0))
        expected_loss += absence * loss

    return RosterStrength(
        healthy_weekly=healthy.total,
        expected_weekly=healthy.total - expected_loss,
        lineup=healthy,
        marginal_value=marginal,
    )


# ---------------------------------------------------------------------------
# Trade evaluation
# ---------------------------------------------------------------------------

@dataclass
class TradeSide:
    """One direction of a deal."""

    players: tuple[RosterPlayer, ...] = ()

    @property
    def raw_weekly(self) -> float:
        """Sum of the pieces in isolation. Shown only to contrast with the real answer."""
        return sum(float(p.weekly_value) for p in self.players)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.label for p in self.players)


@dataclass
class TradeEvaluation:
    """The verdict, and every component that produced it."""

    before: RosterStrength
    after: RosterStrength
    give: TradeSide
    get: TradeSide
    dropped: tuple[str, ...]
    weeks_remaining: int
    stress_fraction: float
    pessimistic_weekly: float
    optimistic_weekly: float
    flags: tuple[str, ...] = ()

    @property
    def delta_weekly(self) -> float:
        return self.after.expected_weekly - self.before.expected_weekly

    @property
    def delta_season(self) -> float:
        return self.delta_weekly * self.weeks_remaining

    @property
    def delta_starters(self) -> float:
        """Change in the healthy starting lineup - the 'talent' half of the deal."""
        return self.after.healthy_weekly - self.before.healthy_weekly

    @property
    def delta_depth(self) -> float:
        """Change in what absences cost you. Negative means the bench got thinner."""
        return -(self.after.depth_cost - self.before.depth_cost)

    @property
    def relative(self) -> float:
        """Delta as a share of the lineup you already had."""
        base = self.before.expected_weekly
        return self.delta_weekly / base if base > 0 else 0.0

    @property
    def robust(self) -> bool:
        """True when projection error of the assumed size cannot flip the sign."""
        return (self.pessimistic_weekly > 0) == (self.optimistic_weekly > 0)

    @property
    def verdict(self) -> str:
        if not self.robust:
            return "TOO CLOSE TO CALL"
        size = abs(self.relative)
        direction = "ACCEPT" if self.delta_weekly > 0 else "DECLINE"
        if size < 0.005:
            return "NEUTRAL"
        if size < 0.02:
            return f"{direction} (slight)"
        if size < 0.05:
            return f"{direction} (clear)"
        return f"{direction} (big)"


def apply_trade(
    roster: Sequence[RosterPlayer],
    give_ids: Iterable[str],
    get_players: Sequence[RosterPlayer],
) -> list[RosterPlayer]:
    """Roster after the swap. Raises if you are trading somebody you do not have."""
    give = list(give_ids)
    have = {p.player_id for p in roster}
    missing = [g for g in give if g not in have]
    if missing:
        raise ValueError(f"not on your roster: {', '.join(missing)}")
    giving = set(give)
    return [p for p in roster if p.player_id not in giving] + list(get_players)


def evaluate_trade(
    roster: Sequence[RosterPlayer],
    give_ids: Sequence[str],
    get_players: Sequence[RosterPlayer],
    league_cfg,
    weeks_remaining: int,
    stress_fraction: float = 0.10,
) -> TradeEvaluation:
    """Grade a trade against your actual roster.

    ``stress_fraction`` is the projection error the verdict is stress-tested
    against: the pessimistic case marks the incoming players down by that
    fraction and the outgoing players up by it, and the optimistic case does the
    reverse. This matters more than week-to-week noise, which averages out over a
    season, whereas being wrong about a player does not. If the two cases
    disagree about whether the trade helps, the honest verdict is that the model
    cannot tell you - and it says so rather than reporting a decimal.
    """
    roster = list(roster)
    before = roster_strength(roster, league_cfg)

    after_roster = apply_trade(roster, give_ids, get_players)

    # Roster limits: a 3-for-1 that leaves you 16 deep in a 14-man league forces
    # cuts, and the cut is part of the price. Drop the least valuable pieces.
    limit = int(league_cfg.roster_size)
    dropped: list[str] = []
    if len(after_roster) > limit:
        ranked = sorted(after_roster, key=lambda p: float(p.weekly_value))
        for player in ranked[: len(after_roster) - limit]:
            dropped.append(player.label)
        drop_ids = {p.player_id for p in ranked[: len(after_roster) - limit]}
        after_roster = [p for p in after_roster if p.player_id not in drop_ids]

    after = roster_strength(after_roster, league_cfg)

    def shifted(fraction_in: float, fraction_out: float) -> float:
        incoming = {p.player_id for p in get_players}
        outgoing = set(give_ids)
        scaled_before = [
            _scale(p, 1.0 + fraction_out) if p.player_id in outgoing else p for p in roster
        ]
        scaled_after = [
            _scale(p, 1.0 + fraction_in) if p.player_id in incoming else p for p in after_roster
        ]
        return (
            roster_strength(scaled_after, league_cfg).expected_weekly
            - roster_strength(scaled_before, league_cfg).expected_weekly
        )

    pessimistic = shifted(-stress_fraction, +stress_fraction)
    optimistic = shifted(+stress_fraction, -stress_fraction)

    evaluation = TradeEvaluation(
        before=before,
        after=after,
        give=TradeSide(tuple(p for p in roster if p.player_id in set(give_ids))),
        get=TradeSide(tuple(get_players)),
        dropped=tuple(dropped),
        weeks_remaining=int(weeks_remaining),
        stress_fraction=float(stress_fraction),
        pessimistic_weekly=pessimistic,
        optimistic_weekly=optimistic,
    )
    evaluation.flags = tuple(_trade_flags(evaluation, after_roster, league_cfg))
    return evaluation


def _scale(player: RosterPlayer, factor: float) -> RosterPlayer:
    return RosterPlayer(
        player_id=player.player_id,
        name=player.name,
        position=player.position,
        weekly_value=float(player.weekly_value) * float(factor),
        availability=player.availability,
        is_pick=player.is_pick,
        slots=player.slots,
    )


def _trade_flags(
    evaluation: TradeEvaluation, after_roster: Sequence[RosterPlayer], league_cfg
) -> list[str]:
    """Things the headline number does not say out loud."""
    flags: list[str] = []

    if evaluation.dropped:
        flags.append(
            f"Roster is over the {league_cfg.roster_size}-man limit: you would have to drop "
            + ", ".join(evaluation.dropped)
            + " (already charged against the deal)."
        )

    if evaluation.delta_starters > 0 and evaluation.delta_depth < -0.5:
        flags.append(
            f"Upgrade paid for with depth: starters +{evaluation.delta_starters:.1f}/wk, "
            f"but absences now cost {abs(evaluation.delta_depth):.1f}/wk more."
        )

    # Positional shape: can the post-trade roster still fill every slot?
    unfillable = _unfillable_slots(after_roster, league_cfg)
    if unfillable:
        flags.append(
            "Leaves a hole you cannot legally fill: " + ", ".join(sorted(unfillable))
            + ". You would be starting nobody there."
        )

    incoming_risk = [p for p in evaluation.get.players if p.availability < 0.75 and not p.is_pick]
    if incoming_risk:
        flags.append(
            "Injury risk coming in: "
            + ", ".join(f"{p.name} ({p.availability * 100:.0f}% weeks)" for p in incoming_risk)
        )

    raw_gap = evaluation.get.raw_weekly - evaluation.give.raw_weekly
    if raw_gap * evaluation.delta_weekly < 0:
        flags.append(
            f"Raw totals disagree with the lineup maths ({raw_gap:+.1f} vs "
            f"{evaluation.delta_weekly:+.1f}/wk). Fit, not talent, is deciding this one."
        )

    if any(p.is_pick for p in evaluation.get.players) or any(
        p.is_pick for p in evaluation.give.players
    ):
        flags.append(
            "Picks are priced as the average player still available at that slot. That "
            "is an average outcome, not a promise - the player who falls to you may not "
            "be one you want."
        )

    # A roster that cannot fill its own lineup credits any incoming player with a
    # whole empty slot, which is arithmetically right and practically misleading
    # mid-draft, when everybody's roster is full of holes.
    empty_before = [slot for slot, _ in _empty_slots(evaluation.before.lineup, league_cfg)]
    if empty_before:
        flags.append(
            "Your roster cannot fill " + ", ".join(sorted(set(empty_before)))
            + " right now, so anyone arriving is credited with that whole empty slot. "
            "Mid-draft that inflates everything: compare picks against each other "
            "instead of against your part-built lineup."
        )

    return flags


def _empty_slots(lineup: Lineup, league_cfg) -> list[tuple[str, None]]:
    """Starting slots the lineup could not fill at all."""
    required = expand_slots(league_cfg)
    for slot, _player_id in lineup.assignments:
        if slot in required:
            required.remove(slot)
    return [(slot, None) for slot in required]


def _unfillable_slots(players: Sequence[RosterPlayer], league_cfg) -> set[str]:
    return {slot for slot, _ in _empty_slots(optimal_lineup(players, league_cfg), league_cfg)}


# ---------------------------------------------------------------------------
# Turning a draft board into tradeable assets
# ---------------------------------------------------------------------------

def board_to_roster_players(board) -> dict[str, RosterPlayer]:
    """Every player on the board, keyed by normalised name.

    Names are the only identifier a human will type, and the only one that
    survives a copy-paste out of Sleeper, so this keys on the normalised form
    rather than on an internal id.
    """
    from src.adp import normalise_name

    if "lockin_value" not in getattr(board, "columns", ()):
        raise ValueError(
            "This board has no `lockin_value` column, so there is nothing to value a "
            "trade with. Rebuild it with `python -m src.cli build-board`."
        )

    def number(value, fallback: float) -> float:
        """NaN and blanks become the fallback rather than poisoning the lineup maths."""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return fallback
        return result if np.isfinite(result) else fallback

    players: dict[str, RosterPlayer] = {}
    for row in board.itertuples(index=False):
        name = str(getattr(row, "player_name", "")).strip()
        if not name:
            continue
        key = normalise_name(name)
        players[key] = RosterPlayer(
            player_id=key,
            name=name,
            position=str(getattr(row, "position", "")),
            weekly_value=max(number(getattr(row, "lockin_value", 0.0), 0.0), 0.0),
            availability=float(
                np.clip(number(getattr(row, "availability_probability", 1.0), 1.0), 0.0, 1.0)
            ),
        )
    return players


def resolve_names(names: Iterable[str], index: Mapping[str, RosterPlayer]):
    """Map typed names onto board players, reporting what did not match.

    Returns ``(players, unmatched)``. Unmatched names carry close-match
    suggestions, because at a draft table the failure mode is a typo, and a
    silent drop from the roster would quietly change every number below it.
    """
    import difflib

    from src.adp import normalise_name

    found: list[RosterPlayer] = []
    unmatched: list[tuple[str, list[str]]] = []
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        key = normalise_name(name)
        player = index.get(key)
        if player is not None:
            found.append(player)
            continue
        close = difflib.get_close_matches(key, list(index), n=3, cutoff=0.7)
        unmatched.append((name, [index[c].name for c in close]))
    return found, unmatched


#: Share of the pick's simulated outcomes its slot eligibility must cover. A pick
#: that lands on a centre 60% of the time and a guard 30% should be credited with
#: both, but not with the 5% tail that would let it plug any hole on the roster.
PICK_POSITION_COVERAGE = 0.80


@dataclass(frozen=True)
class PickValue:
    """What an unspent draft pick is worth, in the same currency as a player."""

    pick: int
    weekly_value: float
    availability: float
    typical: tuple[str, ...]
    #: (position, share of simulations), most likely first.
    position_weights: tuple[tuple[str, float], ...] = ()

    def eligible_slots(self, league_cfg) -> tuple[str, ...]:
        """Slots this pick can realistically be spent on.

        The positions that make up the bulk of its simulated outcomes, rather
        than every position - a pick is an option on the best player left, not an
        option on the exact player you need.
        """
        if not self.position_weights:
            return ()
        eligible = eligibility_map(league_cfg)
        allowed: set[str] = set()
        covered = 0.0
        for position, weight in self.position_weights:
            allowed |= set(eligible.get(position, ()))
            covered += weight
            if covered >= PICK_POSITION_COVERAGE:
                break
        return tuple(sorted(allowed))

    def as_player(self, league_cfg=None) -> RosterPlayer:
        return RosterPlayer(
            player_id=f"pick_{self.pick}",
            name=f"Pick #{self.pick}",
            position="PICK",
            weekly_value=self.weekly_value,
            availability=self.availability,
            is_pick=True,
            slots=self.eligible_slots(league_cfg) if league_cfg is not None else (),
        )


def pick_value_curve(
    board,
    league_cfg,
    model_cfg: Mapping,
    picks: Sequence[int],
    n_simulations: int | None = None,
    already_drafted: Sequence[str] = (),
    seed: int | None = None,
) -> dict[int, PickValue]:
    """Price draft picks by simulating who is actually still on the board.

    A pick's worth is not the value of the player ranked at that slot - it is the
    expected value of the best player *left*, which is higher early (the board
    rarely goes exactly to plan) and collapses toward replacement level late.
    Simulating the opponents is the only way to get that curve for a specific
    league, and it is why a mid-round pick is worth so much less than its rank
    suggests: by then every remaining player is roughly interchangeable.
    """
    from src.draft.simulator import DraftSimulator, board_to_players
    from src.adp import normalise_name

    picks = [int(p) for p in picks if int(p) > 0]
    if board is None or len(board) == 0 or not picks:
        return {}

    # The simulator keys players however the board does; callers key them by name.
    # Translating here rather than at the call site is what stops an "already
    # drafted" list from silently doing nothing - which would price every pick as
    # though your own roster were still on the board.
    by_sim_id: dict[str, str] = {}
    sim_id_by_key: dict[str, str] = {}
    for row in board.itertuples(index=False):
        name = str(getattr(row, "player_name", ""))
        sim_id = str(getattr(row, "player_id", "") or name)
        key = normalise_name(name)
        by_sim_id[sim_id] = key
        sim_id_by_key[key] = sim_id

    drafted_ids = [
        sim_id_by_key.get(normalise_name(str(d)), str(d)) for d in already_drafted
    ]

    rng = np.random.default_rng(seed) if seed is not None else None
    simulator = DraftSimulator(model_cfg, league_cfg, rng=rng)
    pool = board_to_players(board)
    taken = simulator.best_available_curve(
        pool, picks, n_simulations=n_simulations, already_drafted=drafted_ids
    )

    index = board_to_roster_players(board)

    curve: dict[int, PickValue] = {}
    for pick, drafted_ids in taken.items():
        selected = [index.get(by_sim_id.get(i, "")) for i in drafted_ids]
        selected = [p for p in selected if p is not None]
        if not selected:
            continue
        counts: dict[str, int] = {}
        positions: dict[str, int] = {}
        for player in selected:
            counts[player.name] = counts.get(player.name, 0) + 1
            if player.position:
                positions[player.position] = positions.get(player.position, 0) + 1
        typical = tuple(
            name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        )
        total = sum(positions.values()) or 1
        weights = tuple(
            (position, count / total)
            for position, count in sorted(positions.items(), key=lambda kv: -kv[1])
        )
        curve[pick] = PickValue(
            pick=pick,
            weekly_value=float(np.mean([p.weekly_value for p in selected])),
            availability=float(np.mean([p.availability for p in selected])),
            typical=typical,
            position_weights=weights,
        )
    return curve
