"""Draft board, simulator and assistant tests."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.draft import (
    DraftAssistant,
    DraftSimulator,
    format_recommendation,
    assign_tiers,
    board_to_players,
    build_draft_board,
    picks_for_slot,
    replacement_levels,
    snake_pick_order,
)
from src.draft.board import _tier_label


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def fake_board():
    """A 60-player board with a deliberate value cliff after the top 5."""
    rows = []
    for i in range(60):
        value = 1000 - (i * 12 if i >= 5 else i * 3)
        if i >= 5:
            value -= 80  # the cliff
        rows.append(
            {
                "player_id": f"P{i:03d}",
                "player_name": f"Player {i:03d}",
                "team": ["BOS", "LAL", "DEN", "MIA", "NYK"][i % 5],
                "position": ["PG", "SG", "SF", "PF", "C"][i % 5],
                "projected_season_value": float(value),
                "projected_fp_game": 45.0 - i * 0.4,
                "projected_games": 70.0,
                "lockin_value": 48.0 - i * 0.4,
                "lock_in_advantage": 5.0,
                "games_per_week": 3.4,
                "median_fp": 40.0,
                "floor": 25.0,
                "ceiling": 55.0,
                "std_dev": 10.0,
                "risk": 0.3,
                "archetype": "balanced",
                "assumption_notes": "",
                "is_synthetic": True,
            }
        )
    return pd.DataFrame(rows)


# =========================================================================
# Snake draft order
# =========================================================================

def test_snake_order_reverses_each_round():
    order = snake_pick_order(4, 3)
    assert order == [0, 1, 2, 3, 3, 2, 1, 0, 0, 1, 2, 3]


def test_picks_for_slot_first_seat_gets_the_turn():
    """Slot 1 picks 1st and 20th in a 10-team snake - the classic turn."""
    picks = picks_for_slot(1, 10, 14)
    assert picks[0] == 1
    assert picks[1] == 20


def test_picks_for_slot_last_seat_picks_back_to_back():
    picks = picks_for_slot(10, 10, 14)
    assert picks[0] == 10
    assert picks[1] == 11


def test_every_slot_gets_one_pick_per_round():
    for slot in range(1, 11):
        assert len(picks_for_slot(slot, 10, 14)) == 14


def test_all_picks_are_accounted_for():
    every = sorted(p for slot in range(1, 11) for p in picks_for_slot(slot, 10, 14))
    assert every == list(range(1, 141))


# =========================================================================
# Tiers
# =========================================================================

def test_tier_labels_extend_past_z():
    assert [_tier_label(i) for i in (0, 1, 25, 26)] == ["A", "B", "Z", "AA"]


def test_tiers_are_assigned_and_ordered(fake_board, cfg):
    tiered = assign_tiers(fake_board, cfg.model)
    assert "tier" in tiered.columns
    assert tiered["tier"].iloc[0] == "A"
    # Value must be non-increasing down the board.
    values = tiered["projected_season_value"].to_numpy()
    assert np.all(np.diff(values) <= 0)


def test_tiers_respect_minimum_size(fake_board, cfg):
    tiered = assign_tiers(fake_board, cfg.model)
    sizes = tiered["tier"].value_counts()
    # Every tier except possibly the last must meet the minimum.
    min_size = cfg.model["tiers"]["min_tier_size"]
    assert (sizes.iloc[:-1] >= min_size).all()


def test_tiers_are_not_fixed_size_buckets(fake_board, cfg):
    """Handoff sec.16: tiers must mean something, not just chop every N players."""
    tiered = assign_tiers(fake_board, cfg.model)
    sizes = tiered["tier"].value_counts().tolist()
    assert len(set(sizes)) > 1


def test_empty_board_tiers_gracefully(cfg):
    out = assign_tiers(pd.DataFrame(), cfg.model)
    assert out.empty


# =========================================================================
# Replacement level and scarcity
# =========================================================================

def test_replacement_levels_computed_per_position(fake_board, cfg):
    levels = replacement_levels(fake_board, cfg.league)
    assert set(levels) == {"PG", "SG", "SF", "PF", "C"}
    assert all(isinstance(v, float) for v in levels.values())


def test_value_over_replacement_is_zero_at_the_baseline(fake_board, cfg):
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    assert "vor" in board.columns
    # The best player at any position must be worth more than replacement.
    for position in board["position"].unique():
        best = board[board["position"] == position].iloc[0]
        assert best["vor"] >= 0


def test_build_draft_board_produces_required_columns(fake_board, cfg):
    """Handoff sec.26 lists the columns the final board must carry."""
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    required = [
        "player_name", "team", "position", "model_rank", "tier",
        "projected_fp_game", "projected_games", "projected_season_value",
        "median_fp", "floor", "ceiling", "std_dev", "lockin_value", "risk",
    ]
    for column in required:
        assert column in board.columns, f"missing required column {column}"


def test_model_rank_is_dense_and_starts_at_one(fake_board, cfg):
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    assert board["model_rank"].tolist() == list(range(1, len(board) + 1))


# =========================================================================
# Simulator
# =========================================================================

def test_availability_decreases_for_better_players(fake_board, cfg):
    """The top player must be less likely to survive than the 40th."""
    simulator = DraftSimulator(cfg.model, cfg.league, rng=np.random.default_rng(2))
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    players = board_to_players(board)
    result = simulator.simulate(players, n_simulations=200, target_picks=[15])
    assert result.availability[players[0].player_id] < result.availability[players[40].player_id]


def test_availability_is_a_probability(fake_board, cfg):
    simulator = DraftSimulator(cfg.model, cfg.league, rng=np.random.default_rng(4))
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    result = simulator.simulate(board_to_players(board), n_simulations=100, target_picks=[12])
    assert all(0.0 <= v <= 1.0 for v in result.availability.values())


def test_earlier_picks_have_higher_availability(fake_board, cfg):
    """A player is likelier to survive to pick 5 than to pick 25."""
    simulator = DraftSimulator(cfg.model, cfg.league, rng=np.random.default_rng(6))
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    players = board_to_players(board)
    result = simulator.simulate(players, n_simulations=200, target_picks=[5, 25])
    for probabilities in result.picked_by_round.values():
        assert probabilities[0] >= probabilities[1] - 1e-9


def test_simulator_handles_empty_target_picks(fake_board, cfg):
    simulator = DraftSimulator(cfg.model, cfg.league)
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    result = simulator.simulate(board_to_players(board), n_simulations=10, target_picks=[])
    assert result.availability == {}


def test_board_to_players_falls_back_to_model_rank_without_adp(fake_board, cfg):
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    players = board_to_players(board)
    assert players[0].adp == float(players[0].model_rank)


# =========================================================================
# Live assistant
# =========================================================================

def test_assistant_returns_a_recommendation_with_reasoning(fake_board, cfg):
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    assistant = DraftAssistant(board, cfg.league, cfg.model, rng=np.random.default_rng(8))
    package = assistant.recommend(my_slot=3, current_pick=3, top_n=5, n_simulations=100)
    assert package["recommendation"] is not None
    # Handoff sec.21: never output a bare pick.
    assert len(package["recommendation"].reasoning) >= 3


def test_assistant_excludes_already_drafted_players(fake_board, cfg):
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    assistant = DraftAssistant(board, cfg.league, cfg.model, rng=np.random.default_rng(9))
    taken = ["Player 000", "Player 001", "Player 002"]
    package = assistant.recommend(
        my_slot=4, current_pick=4, drafted_names=taken, top_n=5, n_simulations=100
    )
    names = {r.player_name for r in package["top_available"]}
    assert not names & set(taken)


def test_assistant_reports_positional_need(fake_board, cfg):
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    assistant = DraftAssistant(board, cfg.league, cfg.model, rng=np.random.default_rng(10))
    package = assistant.recommend(
        my_slot=1, current_pick=1, my_roster=["PG", "SG", "C"], top_n=3, n_simulations=50
    )
    need = package["positional_need"]
    assert "PG" not in need   # already filled
    assert "SF" in need


def test_assistant_computes_next_pick_correctly(fake_board, cfg):
    board = build_draft_board(fake_board, cfg.league, cfg.model)
    assistant = DraftAssistant(board, cfg.league, cfg.model, rng=np.random.default_rng(12))
    package = assistant.recommend(my_slot=1, current_pick=1, top_n=3, n_simulations=50)
    assert package["next_pick"] == 20


def test_assistant_handles_an_exhausted_board(cfg):
    board = build_draft_board(
        pd.DataFrame(
            [{"player_id": "A", "player_name": "Only Player", "team": "BOS", "position": "PG",
              "projected_season_value": 500.0, "is_synthetic": True}]
        ),
        cfg.league, cfg.model,
    )
    assistant = DraftAssistant(board, cfg.league, cfg.model)
    package = assistant.recommend(my_slot=1, current_pick=1, drafted_names=["Only Player"])
    assert "error" in package


# =========================================================================
# Tiering must survive bad data and must not lump the board together
# =========================================================================

def test_tiers_do_not_collapse_on_a_nan_value(cfg):
    """A single NaN once made the global threshold NaN, silently putting every
    player in one tier. It must warn and still produce real tiers."""
    rows = [
        {"player_id": f"P{i}", "player_name": f"P{i}", "team": "BOS", "position": "PG",
         "projected_season_value": float(1000 - i * 10)}
        for i in range(40)
    ]
    rows[20]["projected_season_value"] = float("nan")
    frame = pd.DataFrame(rows)

    with pytest.warns(RuntimeWarning, match="non-finite"):
        tiered = assign_tiers(frame, cfg.model)
    assert tiered["tier"].nunique() > 1


def test_tiers_do_not_lump_the_long_tail_into_one_bucket(cfg):
    """Draft value decays steeply then flattens. A global gap threshold cut a few
    tiers at the top and dumped everything else together; local scaling fixes it."""
    values = [1000, 900, 820] + [600 - i * 3 for i in range(120)]
    frame = pd.DataFrame([
        {"player_id": f"P{i}", "player_name": f"P{i}", "team": "BOS",
         "position": "PG", "projected_season_value": float(v)}
        for i, v in enumerate(values)
    ])
    tiered = assign_tiers(frame, cfg.model)
    sizes = tiered["tier"].value_counts()
    assert tiered["tier"].nunique() >= 5
    # No tier may hold more than half the board.
    assert sizes.max() < len(frame) * 0.5


def test_tiers_find_a_real_cliff(cfg):
    """A deliberate 200-point cliff after the 6th player must start a new tier."""
    values = [500 - i * 4 for i in range(6)] + [270 - i * 4 for i in range(30)]
    frame = pd.DataFrame([
        {"player_id": f"P{i}", "player_name": f"P{i}", "team": "BOS",
         "position": "PG", "projected_season_value": float(v)}
        for i, v in enumerate(values)
    ])
    tiered = assign_tiers(frame, cfg.model)
    assert tiered["tier"].iloc[5] != tiered["tier"].iloc[6]


# =========================================================================
# Drafted-name matching must be forgiving, and loud when it fails
# =========================================================================
# Typing accented names correctly mid-draft is not realistic, and a strict
# comparison fails in the worst possible way: the player silently stays on the
# board and gets recommended after they are already gone.

@pytest.fixture
def real_name_board(cfg):
    names = [
        "Nikola Jokić", "Luka Dončić", "Shai Gilgeous-Alexander",
        "Victor Wembanyama", "Jaren Jackson Jr.", "P.J. Washington",
        "De'Aaron Fox", "Jayson Tatum", "Anthony Davis", "Trae Young",
    ]
    rows = [
        {
            "player_id": f"P{i}", "player_name": name, "team": "BOS",
            "position": ["PG", "SG", "SF", "PF", "C"][i % 5],
            "projected_season_value": float(900 - i * 40),
            "projected_fp_game": 45.0 - i, "projected_games": 70.0,
            "lockin_value": 48.0 - i, "lock_in_advantage": 5.0,
            "games_per_week": 3.4, "median_fp": 40.0, "floor": 25.0,
            "ceiling": 55.0, "std_dev": 10.0, "risk": 0.3,
            "archetype": "balanced", "assumption_notes": "", "is_synthetic": False,
        }
        for i, name in enumerate(names)
    ]
    return build_draft_board(pd.DataFrame(rows), cfg.league, cfg.model)


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("Luka Doncic", "Luka Dončić"),                       # no accents
        ("luka doncic", "Luka Dončić"),                       # all lowercase
        ("  Luka Doncic  ", "Luka Dončić"),                   # stray whitespace
        ("Nikola Jokic", "Nikola Jokić"),
        ("Shai Gilgeous Alexander", "Shai Gilgeous-Alexander"),  # no hyphen
        ("PJ Washington", "P.J. Washington"),                 # no periods
        ("DeAaron Fox", "De'Aaron Fox"),                      # no apostrophe
        ("Jaren Jackson", "Jaren Jackson Jr."),               # no suffix
    ],
)
def test_drafted_names_match_without_exact_spelling(real_name_board, cfg, typed, expected):
    assistant = DraftAssistant(real_name_board, cfg.league, cfg.model)
    remaining = set(assistant._remaining([typed])["player_name"])
    assert expected not in remaining, f"{typed!r} failed to remove {expected!r}"
    assert assistant.unmatched_names([typed]) == []


def test_a_real_typo_is_reported_with_a_suggestion(real_name_board, cfg):
    assistant = DraftAssistant(real_name_board, cfg.league, cfg.model)
    problems = assistant.unmatched_names(["Victor Wembanyma"])
    assert len(problems) == 1
    name, suggestions = problems[0]
    assert name == "Victor Wembanyma"
    assert "Victor Wembanyama" in suggestions


def test_typo_leaves_the_player_available_and_says_so(real_name_board, cfg):
    """The dangerous case: a mistyped name must never fail silently."""
    assistant = DraftAssistant(real_name_board, cfg.league, cfg.model)
    package = assistant.recommend(
        my_slot=3, current_pick=8, drafted_names=["Victor Wembanyma"], n_simulations=50
    )
    assert package["unmatched_drafted"]

    rendered = format_recommendation(package)
    assert "WARNING" in rendered
    assert "matched NOBODY" in rendered
    assert "Victor Wembanyama" in rendered      # the suggestion


def test_correctly_spelled_names_produce_no_warning(real_name_board, cfg):
    assistant = DraftAssistant(real_name_board, cfg.league, cfg.model)
    package = assistant.recommend(
        my_slot=3, current_pick=8,
        drafted_names=["Nikola Jokic", "Luka Doncic"], n_simulations=50,
    )
    assert package["unmatched_drafted"] == []
    assert "WARNING" not in format_recommendation(package)
