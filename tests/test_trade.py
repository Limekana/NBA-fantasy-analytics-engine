"""Trade evaluation tests.

Two things here are load-bearing beyond the usual unit-test remit.

The lineup solver is checked against exhaustive brute force rather than against
expected values, because the failure mode that matters is subtle: a solver that
is *nearly* optimal produces plausible numbers and quietly grades trades wrong.

And the dashboard's JavaScript is executed under Node against the Python
implementation, because the offline requirement forces the same mathematics to
exist twice. That duplication is only safe if something fails when they diverge.
"""
from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from src.config import load_config
from src import trade
from src.reporting.trade_dashboard import LINEUP_JS, _UI_JS, render_trade_dashboard

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def league(cfg):
    return cfg.league


def player(pid, position, value, availability=1.0, is_pick=False):
    return trade.RosterPlayer(pid, pid, position, value, availability, is_pick)


# ---------------------------------------------------------------------------
# The lineup solver
# ---------------------------------------------------------------------------

def brute_force_lineup(players, league_cfg):
    """Every legal assignment, scored. Exponential, and only used on tiny inputs.

    Players sitting in a slot they are not eligible for contribute nothing, which
    is the same thing as being benched - so enumerating injective assignments
    covers every lineup, including the ones that leave slots empty.
    """
    slots = trade.expand_slots(league_cfg)
    eligible = trade.eligibility_map(league_cfg)
    best = 0.0
    if len(players) <= len(slots):
        for assignment in itertools.permutations(range(len(slots)), len(players)):
            best = max(best, sum(
                p.weekly_value
                for i, p in enumerate(players)
                if slots[assignment[i]] in eligible.get(p.position, frozenset())
            ))
    else:
        for assignment in itertools.permutations(range(len(players)), len(slots)):
            best = max(best, sum(
                players[assignment[s]].weekly_value
                for s in range(len(slots))
                if slots[s] in eligible.get(players[assignment[s]].position, frozenset())
            ))
    return best


def test_optimal_lineup_matches_brute_force(league):
    """The exact answer, on inputs small enough to enumerate."""
    import random

    rng = random.Random(4)
    positions = list(league.position_eligibility)
    for _ in range(40):
        n = rng.randint(2, 6)
        players = [
            player(f"p{i}", rng.choice(positions), round(rng.uniform(1, 50), 2))
            for i in range(n)
        ]
        assert trade.optimal_lineup(players, league).total == pytest.approx(
            brute_force_lineup(players, league)
        )


def test_lineup_respects_position_eligibility(league):
    """A centre cannot fill a guard slot, however valuable they are."""
    centres = [player(f"c{i}", "C", 60.0) for i in range(4)]
    lineup = trade.optimal_lineup(centres, league)
    filled = {slot for slot, _ in lineup.assignments}
    assert filled <= {"C", "UTIL"}
    # One C slot and two UTIL slots: the fourth centre cannot start at all.
    assert len(lineup.assignments) == 3
    assert len(lineup.bench) == 1


def test_lineup_reshuffles_rather_than_squatting(league):
    """The case naive greedy gets wrong.

    A greedy pass that seats each player in their best free slot and never
    revisits will put a guard in UTIL and then find a centre locked out. The
    augmenting-path solver moves the guard and seats both.
    """
    players = [player("g", "PG", 40.0), player("c", "C", 30.0)]
    lineup = trade.optimal_lineup(players, league)
    assert lineup.total == pytest.approx(70.0)
    assert len(lineup.assignments) == 2


def test_picks_can_fill_any_slot(league):
    pick = player("pick", "PICK", 25.0, is_pick=True)
    lineup = trade.optimal_lineup([pick], league)
    assert lineup.total == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Marginal value and depth
# ---------------------------------------------------------------------------

def test_marginal_value_is_the_gap_to_the_replacement(league):
    """A player you are already covering is worth nearly nothing to your lineup.

    A point guard can fill PG, G and both UTIL slots - four seats. So a fifth
    point guard sits, and if he is worth 28 then losing any of the four starters
    costs 2, not 30. That difference is the whole argument for trading from a
    position of surplus.
    """
    roster = [player(f"pg{i}", "PG", 30.0) for i in range(4)] + [player("pg4", "PG", 28.0)]
    strength = trade.roster_strength(roster, league)
    assert len(strength.lineup.assignments) == 4
    assert strength.lineup.bench == ("pg4",)
    assert all(value == pytest.approx(2.0) for value in strength.marginal_value.values())


def test_deep_roster_pays_less_for_absences(league):
    """Depth cost is what makes a two-for-one more than the sum of its parts."""
    starters = [player(f"s{i}", ["PG", "SG", "SF", "PF", "C"][i % 5], 30.0, availability=0.8)
                for i in range(9)]
    thin = trade.roster_strength(starters, league)
    deep = trade.roster_strength(
        starters + [player(f"b{i}", ["PG", "SG", "SF", "PF", "C"][i % 5], 28.0) for i in range(5)],
        league,
    )
    assert deep.healthy_weekly == pytest.approx(thin.healthy_weekly)
    assert deep.depth_cost < thin.depth_cost


def test_absence_of_a_backed_up_player_costs_nothing(league):
    """An injury-prone player behind an identical backup is not a risk at all.

    A centre can fill C and the two UTIL slots, so four identical centres means
    three start and one waits. Whoever sits out is replaced at no cost, and the
    availability discount correctly disappears.
    """
    roster = [player("a", "C", 20.0, availability=0.5)] + [
        player(f"c{i}", "C", 20.0) for i in range(3)
    ]
    strength = trade.roster_strength(roster, league)
    assert strength.depth_cost == pytest.approx(0.0)
    assert strength.expected_weekly == pytest.approx(strength.healthy_weekly)


# ---------------------------------------------------------------------------
# Trade evaluation
# ---------------------------------------------------------------------------

def test_trade_is_graded_on_fit_not_on_raw_totals(league):
    """The whole reason this module exists.

    Trading two surplus guards for one centre can raise the lineup even though
    the raw point totals say you lost the deal, because the guards were not
    starting and the centre is.
    """
    roster = [player(f"pg{i}", "PG", 30.0) for i in range(6)] + [
        player(f"sf{i}", "SF", 25.0) for i in range(2)
    ]
    incoming = [player("c", "C", 40.0)]
    evaluation = trade.evaluate_trade(
        roster, ["pg4", "pg5"], incoming, league, weeks_remaining=22
    )
    assert evaluation.get.raw_weekly < evaluation.give.raw_weekly     # 40 < 60
    assert evaluation.delta_weekly > 0                                 # and yet
    assert evaluation.verdict.startswith("ACCEPT")


def test_over_roster_limit_charges_the_drop(league):
    roster = [player(f"p{i}", "PG", 20.0 + i) for i in range(league.roster_size)]
    incoming = [player("x", "C", 45.0), player("y", "C", 44.0)]
    evaluation = trade.evaluate_trade(roster, ["p0"], incoming, league, weeks_remaining=22)
    assert evaluation.dropped                                   # 14 - 1 + 2 = 15
    assert any("roster" in flag.lower() for flag in evaluation.flags)


def test_close_calls_are_reported_as_close(league):
    """When projection error can flip the sign, say so rather than pick a side."""
    roster = [player(f"p{i}", ["PG", "SG", "SF", "PF", "C"][i % 5], 30.0) for i in range(10)]
    evaluation = trade.evaluate_trade(
        roster, ["p0"], [player("x", "PG", 30.2)], league, weeks_remaining=22,
        stress_fraction=0.10,
    )
    assert not evaluation.robust
    assert evaluation.verdict == "TOO CLOSE TO CALL"


def test_trading_a_player_you_do_not_have_is_an_error(league):
    with pytest.raises(ValueError, match="not on your roster"):
        trade.evaluate_trade([player("a", "PG", 10.0)], ["b"], [], league, weeks_remaining=22)


def test_delta_season_scales_with_weeks_remaining(league):
    roster = [player(f"p{i}", "PG", 20.0) for i in range(5)]
    incoming = [player("x", "C", 40.0)]
    short = trade.evaluate_trade(roster, [], incoming, league, weeks_remaining=4)
    long = trade.evaluate_trade(roster, [], incoming, league, weeks_remaining=20)
    assert long.delta_season == pytest.approx(short.delta_season * 5)


# ---------------------------------------------------------------------------
# Pick pricing
# ---------------------------------------------------------------------------

@pytest.fixture
def board():
    rows = []
    for i in range(80):
        rows.append(
            {
                "player_name": f"Player {i:03d}",
                "team": ["BOS", "LAL", "DEN", "MIA", "NYK"][i % 5],
                "position": ["PG", "SG", "SF", "PF", "C"][i % 5],
                "model_rank": i + 1,
                "adp": float(i + 1),
                "projected_season_value": 1000.0 - 9 * i,
                "lockin_value": 50.0 - 0.45 * i,
                "availability_probability": 0.9,
            }
        )
    return pd.DataFrame(rows)


def test_pick_value_falls_as_the_draft_runs_on(board, cfg):
    curve = trade.pick_value_curve(
        board, cfg.league, cfg.model, [1, 10, 30, 60], n_simulations=60, seed=1
    )
    values = [curve[p].weekly_value for p in (1, 10, 30, 60)]
    assert values == sorted(values, reverse=True)


def test_pick_value_excludes_players_already_rostered(board, cfg):
    """A drafted list that silently does nothing would over-price every pick."""
    top = [f"Player {i:03d}" for i in range(12)]
    open_curve = trade.pick_value_curve(
        board, cfg.league, cfg.model, [1], n_simulations=60, seed=2
    )
    taken_curve = trade.pick_value_curve(
        board, cfg.league, cfg.model, [1], n_simulations=60, seed=2, already_drafted=top
    )
    assert taken_curve[1].weekly_value < open_curve[1].weekly_value
    assert not set(taken_curve[1].typical) & set(top)


def test_pick_becomes_a_tradeable_asset(board, cfg):
    curve = trade.pick_value_curve(board, cfg.league, cfg.model, [5], n_simulations=40, seed=3)
    as_player = curve[5].as_player(cfg.league)
    assert as_player.is_pick
    assert as_player.player_id == "pick_5"


def test_pick_eligibility_reflects_who_actually_falls_there(cfg):
    """A pick that always lands on a centre must not be credited as a guard.

    Treating every pick as able to fill any hole is the tempting simplification,
    and it quietly converts "I have nobody at shooting guard" into a guaranteed
    shooting guard.
    """
    value = trade.PickValue(
        pick=9, weekly_value=30.0, availability=0.9, typical=("Someone",),
        position_weights=(("C", 0.9), ("PG", 0.1)),
    )
    slots = value.eligible_slots(cfg.league)
    assert set(slots) == {"C", "UTIL"}            # 90% covers the threshold alone
    assert "G" not in slots and "PG" not in slots

    spread = trade.PickValue(
        pick=9, weekly_value=30.0, availability=0.9, typical=("Someone",),
        position_weights=(("C", 0.5), ("PG", 0.4), ("SF", 0.1)),
    )
    assert set(spread.eligible_slots(cfg.league)) == {"C", "UTIL", "PG", "G"}


def test_restricted_pick_cannot_fill_a_slot_it_never_lands_on(cfg):
    """The behaviour the eligibility restriction exists to produce.

    A roster of four point guards holds PG, G and both UTIL slots and has nobody
    at centre. Two picks of identical value are then worth wildly different
    amounts: the one that lands on centres fills the hole, the one that lands on
    guards has nowhere to sit. Treating both as position-flexible would have
    priced them the same.
    """
    def pick_landing_on(position):
        return trade.PickValue(
            pick=9, weekly_value=20.0, availability=1.0, typical=(),
            position_weights=((position, 1.0),),
        ).as_player(cfg.league)

    roster = [player(f"pg{i}", "PG", 35.0) for i in range(4)]
    base = trade.optimal_lineup(roster, cfg.league).total

    guard = trade.optimal_lineup(roster + [pick_landing_on("PG")], cfg.league)
    centre = trade.optimal_lineup(roster + [pick_landing_on("C")], cfg.league)

    assert guard.total == pytest.approx(base)            # nowhere to sit
    assert centre.total == pytest.approx(base + 20.0)    # fills the empty C slot
    assert "pick_9" in guard.bench


def test_incomplete_roster_is_flagged_as_inflating_everything(league):
    """Mid-draft, an empty slot makes every acquisition look enormous."""
    roster = [player(f"pg{i}", "PG", 30.0) for i in range(3)]
    evaluation = trade.evaluate_trade(
        roster, [], [player("c", "C", 20.0)], league, weeks_remaining=22
    )
    assert evaluation.delta_weekly == pytest.approx(20.0)   # a whole empty slot
    assert any("cannot fill" in flag for flag in evaluation.flags)


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------

def test_resolve_names_reports_misses_with_suggestions(board):
    index = trade.board_to_roster_players(board)
    found, unmatched = trade.resolve_names(["Player 001", "Playr 002"], index)
    assert len(found) == 1
    assert unmatched and unmatched[0][0] == "Playr 002"
    assert unmatched[0][1], "a near miss should suggest something"


def test_accented_and_punctuated_names_resolve():
    frame = pd.DataFrame(
        [
            {"player_name": "Nikola Jokić", "position": "C", "lockin_value": 50.0,
             "availability_probability": 0.9},
            {"player_name": "Shai Gilgeous-Alexander", "position": "PG", "lockin_value": 48.0,
             "availability_probability": 0.9},
        ]
    )
    index = trade.board_to_roster_players(frame)
    found, unmatched = trade.resolve_names(
        ["nikola jokic", "Shai Gilgeous Alexander"], index
    )
    assert not unmatched
    assert len(found) == 2


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------

def test_dashboard_renders_without_leftover_placeholders(board, cfg, tmp_path):
    curve = trade.pick_value_curve(board, cfg.league, cfg.model, [5], n_simulations=30, seed=5)
    path = render_trade_dashboard(
        board, tmp_path / "trade.html", cfg.league, curve, my_roster=["Player 000"]
    )
    text = path.read_text(encoding="utf-8")
    for token in ("__TITLE__", "__PLAYERS__", "__PICKS__", "__CFG__", "__ROSTER__",
                  "__LINEUP_JS__", "__UI_JS__", "__WEEKS__", "__SUBTITLE__", "__WARNING__"):
        assert token not in text
    assert "Player 000" in text
    assert "pick_5" in text


def test_dashboard_carries_every_board_player(board, cfg, tmp_path):
    path = render_trade_dashboard(board, tmp_path / "trade.html", cfg.league)
    text = path.read_text(encoding="utf-8")
    start = text.index("var PLAYERS = ") + len("var PLAYERS = ")
    payload = json.loads(text[start: text.index("\n", start)].rstrip(";"))
    assert len(payload) == len(board)


@needs_node
def test_dashboard_javascript_parses(tmp_path):
    script = tmp_path / "page.js"
    script.write_text(
        "var PLAYERS=[],PICKS=[],CFG={},PRESET_ROSTER=[];\n" + LINEUP_JS + "\n" + _UI_JS,
        encoding="utf-8",
    )
    subprocess.run([NODE, "--check", str(script)], check=True, capture_output=True)


@needs_node
def test_javascript_lineup_maths_matches_python(league, tmp_path):
    """The duplication guard.

    Python solves the assignment with scipy; the page solves it greedily with
    augmenting paths. Both are exact, so they must agree to floating point on
    every roster - and if someone changes one of them, this fails.
    """
    import random

    rng = random.Random(17)
    positions = list(league.position_eligibility)
    cases = []
    for _ in range(60):
        players = [
            player(f"p{i}", rng.choice(positions), round(rng.uniform(2, 60), 2),
                   round(rng.uniform(0.5, 1.0), 2))
            for i in range(rng.randint(3, 18))
        ]
        if rng.random() < 0.5:
            # Picks carry an explicit slot set, so the override path is covered too.
            slots = tuple(sorted(set(
                rng.choice(list(trade.eligibility_map(league)[rng.choice(positions)]))
                for _ in range(2)
            )))
            players.append(
                trade.RosterPlayer("pick", "Pick", "PICK", round(rng.uniform(10, 50), 2),
                                   1.0, True, slots)
            )
        strength = trade.roster_strength(players, league)
        cases.append(
            {
                "players": [
                    {"id": p.player_id, "name": p.name, "position": p.position,
                     "value": p.weekly_value, "availability": p.availability,
                     "isPick": p.is_pick, "slots": list(p.slots)}
                    for p in players
                ],
                "healthy": strength.healthy_weekly,
                "expected": strength.expected_weekly,
            }
        )

    slots = trade.expand_slots(league)
    js_cfg = {
        "slots": slots,
        "slotNames": sorted(set(slots)),
        "elig": {k: sorted(v) for k, v in trade.eligibility_map(league).items()},
        "rosterLimit": league.roster_size,
        "stress": 0.10,
        "weeksRemaining": 22,
    }

    core = tmp_path / "core.js"
    core.write_text(LINEUP_JS, encoding="utf-8")
    data = tmp_path / "cases.json"
    data.write_text(json.dumps({"cfg": js_cfg, "cases": cases}), encoding="utf-8")

    runner = tmp_path / "run.js"
    runner.write_text(
        """
        const core = require('./core.js');
        const data = require('./cases.json');
        const out = data.cases.map(c => {
          const s = core.rosterStrength(c.players, data.cfg);
          return [s.healthy, s.expected];
        });
        console.log(JSON.stringify(out));
        """,
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(runner)], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    for case, (healthy, expected) in zip(cases, json.loads(result.stdout)):
        assert healthy == pytest.approx(case["healthy"], abs=1e-6)
        assert expected == pytest.approx(case["expected"], abs=1e-6)


@needs_node
def test_javascript_name_normalisation_matches_python(tmp_path):
    """The pasted-roster path fails silently if these two ever drift apart."""
    from src.adp import normalise_name

    names = [
        "Nikola Jokić", "Luka Dončić", "Shai Gilgeous-Alexander", "P.J. Washington",
        "De'Aaron Fox", "Jaren Jackson Jr.", "  Trae  Young ", "Karl-Anthony Towns",
        "Alperen Şengün", "Bogdan Bogdanović", "O.G. Anunoby", "Xavier Tillman Sr.",
    ]
    start = _UI_JS.index("function normalise(name)")
    end = _UI_JS.index("var BY_NAME")
    script = tmp_path / "norm.js"
    script.write_text(
        _UI_JS[start:end]
        + "\nconsole.log(JSON.stringify(process.argv.slice(2).map(normalise)));",
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(script), *names], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == [normalise_name(n) for n in names]


def test_missing_lockin_column_is_a_clear_error():
    frame = pd.DataFrame([{"player_name": "A", "position": "PG"}])
    with pytest.raises(ValueError, match="lockin_value"):
        trade.board_to_roster_players(frame)


def test_blank_values_do_not_poison_the_lineup():
    """One NaN in the board would otherwise make the whole lineup total NaN."""
    import numpy as np

    frame = pd.DataFrame(
        [
            {"player_name": "A", "position": "PG", "lockin_value": np.nan,
             "availability_probability": np.nan},
            {"player_name": "B", "position": "C", "lockin_value": 30.0,
             "availability_probability": 0.8},
        ]
    )
    index = trade.board_to_roster_players(frame)
    assert index["a"].weekly_value == 0.0
    assert index["a"].availability == 1.0
    assert index["b"].weekly_value == 30.0
