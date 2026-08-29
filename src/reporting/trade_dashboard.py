"""trade_dashboard.html - grade a trade against your own roster, offline.

Trades get argued in a group chat during a game, so this has to be one file that
opens instantly on a phone with no network and no build step, exactly like the
draft board.

That constraint forces a design decision worth stating: the lineup mathematics
is implemented twice, once in :mod:`src.trade` and once in JavaScript here. The
alternative - a Python server the page talks to - fails the offline requirement,
and precomputing every possible trade is combinatorially hopeless. Two
implementations means they can drift, so ``tests/test_trade.py`` runs this exact
JavaScript under Node against the Python version on randomised rosters and fails
if the two disagree. The duplication is deliberate and it is pinned.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# The shared core. Mirrors src/trade.py; tests/test_trade.py proves they agree.
# ---------------------------------------------------------------------------
# The Python side solves the assignment with scipy. Here it is a greedy pass with
# augmenting paths, which is exact for this problem rather than an approximation:
# the sets of players that can legally fill distinct slots form a transversal
# matroid, and greedy by weight is optimal on any matroid. Same answer, no
# dependency, a few hundred bytes.
LINEUP_JS = r"""
function eligibleSlots(player, cfg) {
  if (player.slots && player.slots.length) return player.slots;
  if (player.isPick) return cfg.slotNames;
  return cfg.elig[player.position] || [];
}

// Augmenting path: try to seat player `i`, displacing seated players who have
// somewhere else to go. This is what makes the greedy exact - without it, an
// early pick can squat in the only slot a later, better player could use.
function seat(i, chosen, owner, seen, cfg) {
  const wanted = eligibleSlots(chosen[i], cfg);
  for (let s = 0; s < cfg.slots.length; s++) {
    if (seen[s] || wanted.indexOf(cfg.slots[s]) === -1) continue;
    seen[s] = true;
    if (owner[s] === -1 || seat(owner[s], chosen, owner, seen, cfg)) {
      owner[s] = i;
      return true;
    }
  }
  return false;
}

function optimalLineup(players, cfg) {
  const sorted = players.slice().sort(function (a, b) {
    if (b.value !== a.value) return b.value - a.value;
    return String(a.id).localeCompare(String(b.id));
  });
  const owner = new Array(cfg.slots.length).fill(-1);
  const chosen = [];
  let total = 0;
  for (let k = 0; k < sorted.length; k++) {
    if (chosen.length >= cfg.slots.length) break;
    chosen.push(sorted[k]);
    const i = chosen.length - 1;
    const seen = new Array(cfg.slots.length).fill(false);
    if (seat(i, chosen, owner, seen, cfg)) {
      total += sorted[k].value;
    } else {
      chosen.pop();
    }
  }
  const assignments = [];
  const seated = {};
  for (let s = 0; s < cfg.slots.length; s++) {
    if (owner[s] !== -1) {
      assignments.push({ slot: cfg.slots[s], player: chosen[owner[s]] });
      seated[chosen[owner[s]].id] = true;
    } else {
      assignments.push({ slot: cfg.slots[s], player: null });
    }
  }
  const bench = players.filter(function (p) { return !seated[p.id]; })
                       .sort(function (a, b) { return b.value - a.value; });
  return { total: total, assignments: assignments, bench: bench };
}

// Absences priced one at a time: what the lineup actually loses when a starter
// sits, which is the gap to whoever slides up - not the starter's whole value.
function rosterStrength(players, cfg) {
  const healthy = optimalLineup(players, cfg);
  const marginal = {};
  let expectedLoss = 0;
  for (let i = 0; i < healthy.assignments.length; i++) {
    const p = healthy.assignments[i].player;
    if (!p) continue;
    const without = players.filter(function (q) { return q.id !== p.id; });
    const loss = healthy.total - optimalLineup(without, cfg).total;
    marginal[p.id] = loss;
    const avail = Math.min(Math.max(p.availability, 0), 1);
    expectedLoss += (1 - avail) * loss;
  }
  return {
    healthy: healthy.total,
    expected: healthy.total - expectedLoss,
    depthCost: expectedLoss,
    lineup: healthy,
    marginal: marginal
  };
}

function scalePlayer(p, factor) {
  return { id: p.id, name: p.name, position: p.position, value: p.value * factor,
           availability: p.availability, isPick: p.isPick, slots: p.slots };
}

function evaluateTrade(roster, giveIds, getPlayers, cfg) {
  const giving = {};
  giveIds.forEach(function (id) { giving[id] = true; });
  const kept = roster.filter(function (p) { return !giving[p.id]; });
  let after = kept.concat(getPlayers);

  const dropped = [];
  if (after.length > cfg.rosterLimit) {
    const ranked = after.slice().sort(function (a, b) { return a.value - b.value; });
    const cut = ranked.slice(0, after.length - cfg.rosterLimit);
    const cutIds = {};
    cut.forEach(function (p) { cutIds[p.id] = true; dropped.push(p.name); });
    after = after.filter(function (p) { return !cutIds[p.id]; });
  }

  const before = rosterStrength(roster, cfg);
  const afterStrength = rosterStrength(after, cfg);

  function shifted(fracIn, fracOut) {
    const incoming = {};
    getPlayers.forEach(function (p) { incoming[p.id] = true; });
    const b = roster.map(function (p) { return giving[p.id] ? scalePlayer(p, 1 + fracOut) : p; });
    const a = after.map(function (p) { return incoming[p.id] ? scalePlayer(p, 1 + fracIn) : p; });
    return rosterStrength(a, cfg).expected - rosterStrength(b, cfg).expected;
  }

  const s = cfg.stress;
  const pessimistic = shifted(-s, s);
  const optimistic = shifted(s, -s);
  const delta = afterStrength.expected - before.expected;

  return {
    before: before, after: afterStrength, dropped: dropped,
    deltaWeekly: delta,
    deltaSeason: delta * cfg.weeksRemaining,
    deltaStarters: afterStrength.healthy - before.healthy,
    deltaDepth: -(afterStrength.depthCost - before.depthCost),
    relative: before.expected > 0 ? delta / before.expected : 0,
    pessimistic: pessimistic,
    optimistic: optimistic,
    robust: (pessimistic > 0) === (optimistic > 0),
    afterRoster: after
  };
}

function verdictOf(result) {
  if (!result.robust) return 'TOO CLOSE TO CALL';
  const size = Math.abs(result.relative);
  const direction = result.deltaWeekly > 0 ? 'ACCEPT' : 'DECLINE';
  if (size < 0.005) return 'NEUTRAL';
  if (size < 0.02) return direction + ' (slight)';
  if (size < 0.05) return direction + ' (clear)';
  return direction + ' (big)';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { optimalLineup: optimalLineup, rosterStrength: rosterStrength,
                     evaluateTrade: evaluateTrade, verdictOf: verdictOf };
}
"""


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: light dark; --bg:#ffffff; --fg:#16191d; --muted:#5b6470;
    --line:#e3e6ea; --head:#f5f6f8; --accent:#1a56db; --good:#0f7b3d; --bad:#b3261e;
    --warn:#8a5a00; --warnbg:#fff4d6; --card:#fbfcfd; }
  @media (prefers-color-scheme: dark) { :root { --bg:#12151a; --fg:#e6e9ee;
    --muted:#98a2b3; --line:#242a33; --head:#1a1f27; --accent:#7aa2f7; --good:#4ade80;
    --bad:#f87171; --warn:#fbbf24; --warnbg:#3a2f10; --card:#161b22; } }
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg); max-width:1180px;
    font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  h1 { font-size:19px; margin:0 0 2px; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
    margin:0 0 8px; font-weight:700; }
  .sub { color:var(--muted); font-size:12px; margin-bottom:14px; }
  .warn { background:var(--warnbg); color:var(--warn); border:1px solid var(--warn);
    border-radius:6px; padding:10px 12px; margin-bottom:12px; font-weight:600; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:9px;
    padding:12px 14px; margin-bottom:14px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .grid3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; }
  @media (max-width:820px){ .grid2,.grid3 { grid-template-columns:1fr; } }
  input,select,textarea,button { font:inherit; padding:7px 9px; border:1px solid var(--line);
    border-radius:6px; background:var(--bg); color:var(--fg); }
  textarea { width:100%; min-height:64px; resize:vertical; }
  button { cursor:pointer; }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); font-weight:600; }
  button.link { background:none; border:none; color:var(--accent); padding:2px 4px; font-size:12px; }
  .search { position:relative; }
  .search input { width:100%; }
  .menu { position:absolute; z-index:20; left:0; right:0; top:100%; background:var(--bg);
    border:1px solid var(--line); border-radius:6px; max-height:260px; overflow:auto;
    box-shadow:0 6px 20px rgba(0,0,0,.18); }
  .menu div { padding:7px 10px; cursor:pointer; display:flex; justify-content:space-between; gap:8px; }
  .menu div:hover,.menu div.active { background:var(--head); }
  .menu .meta { color:var(--muted); font-size:11px; white-space:nowrap; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:9px; min-height:26px; }
  .chip { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--line);
    border-radius:999px; padding:3px 6px 3px 10px; background:var(--bg); font-size:12px; }
  .chip b { font-weight:600; }
  .chip .v { color:var(--muted); font-variant-numeric:tabular-nums; }
  .chip button { border:none; background:none; color:var(--muted); padding:0 3px; font-size:14px; line-height:1; }
  .chip button:hover { color:var(--bad); }
  .chip.pick { border-style:dashed; }
  .verdict { font-size:26px; font-weight:800; letter-spacing:-.01em; margin:2px 0 6px; }
  .verdict.good { color:var(--good); } .verdict.bad { color:var(--bad); }
  .verdict.flat { color:var(--muted); }
  .big { font-size:22px; font-weight:700; font-variant-numeric:tabular-nums; }
  .stat { display:flex; justify-content:space-between; gap:10px; padding:3px 0;
    border-bottom:1px dotted var(--line); font-variant-numeric:tabular-nums; }
  .stat:last-child { border-bottom:none; }
  .stat span:last-child { font-weight:600; }
  .pos { color:var(--good); } .neg { color:var(--bad); }
  table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
  th,td { padding:4px 7px; border-bottom:1px solid var(--line); text-align:right; }
  th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.03em; }
  td:first-child,th:first-child,td:nth-child(2),th:nth-child(2) { text-align:left; }
  .flag { border-left:3px solid var(--warn); background:var(--warnbg); color:var(--warn);
    padding:7px 10px; border-radius:0 6px 6px 0; margin-top:7px; font-size:12px; }
  .empty { color:var(--muted); font-style:italic; font-size:12px; }
  footer { color:var(--muted); font-size:11px; margin-top:16px; line-height:1.65; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .gained { color:var(--good); } .lost { color:var(--bad); }
</style></head><body>
<h1>__TITLE__</h1>
<div class="sub">__SUBTITLE__</div>
__WARNING__

<div class="card">
  <h2>1 &middot; My roster <span id="rosterCount" class="meta"></span></h2>
  <div class="grid2">
    <div>
      <div class="search">
        <input id="rosterSearch" type="search" placeholder="Add a player to my roster...">
        <div class="menu" id="rosterMenu" hidden></div>
      </div>
      <div class="chips" id="rosterChips"></div>
    </div>
    <div>
      <textarea id="paste" placeholder="...or paste your whole roster here, one name per line (straight from Sleeper)."></textarea>
      <div class="row" style="margin-top:6px">
        <button class="primary" id="pasteBtn">Load pasted names</button>
        <button class="link" id="clearRoster">Clear roster</button>
        <span id="pasteResult" class="meta"></span>
      </div>
    </div>
  </div>
</div>

<div class="card">
  <h2>2 &middot; The deal</h2>
  <div class="grid2">
    <div>
      <div class="row" style="margin-bottom:6px"><b class="lost">I give away</b>
        <span class="meta">click a roster chip above, or search</span></div>
      <div class="search">
        <input id="giveSearch" type="search" placeholder="Player from my roster, or one of my picks...">
        <div class="menu" id="giveMenu" hidden></div>
      </div>
      <div class="chips" id="giveChips"></div>
    </div>
    <div>
      <div class="row" style="margin-bottom:6px"><b class="gained">I get back</b>
        <span class="meta">any player, or a draft pick</span></div>
      <div class="search">
        <input id="getSearch" type="search" placeholder="Player or pick coming to me...">
        <div class="menu" id="getMenu" hidden></div>
      </div>
      <div class="chips" id="getChips"></div>
    </div>
  </div>
  <div class="row" style="margin-top:10px">
    <label class="meta">Weeks left in the season
      <input id="weeks" type="number" min="1" max="30" value="__WEEKS__" style="width:64px">
    </label>
    <label class="meta">Assume projections could be off by
      <input id="stress" type="number" min="0" max="50" value="10" style="width:56px">%
    </label>
    <button class="link" id="clearTrade">Clear this trade</button>
  </div>
</div>

<div id="result"></div>

<footer>
  <b>How the verdict is reached.</b> Only your nine starters score, so a player is worth
  what they add to your best legal lineup &mdash; not what they score in the abstract.
  Every number here is the difference between two solved lineups. Absences are then
  charged at what they actually cost: the gap between the starter and whoever slides
  up, weighted by how often they miss. That is why a deep bench shows up as a small
  <i>depth cost</i>, and why stripping depth to buy a star can grade out flat.<br>
  <b>Marginal value</b> is what your lineup loses in a week without that player. It is
  the number to trade on: a player with a huge projection and a tiny marginal value is
  a player you are already covering, and therefore the one to sell.<br>
  <b>Stress test.</b> Week-to-week noise averages out over a season; being wrong about a
  player does not. So the verdict is re-run with the incoming side marked down and the
  outgoing side marked up. If that flips the sign, it says TOO CLOSE TO CALL instead of
  pretending to a decimal it does not have.<br>
  Projections are assumptions, not facts &mdash; see docs/assumptions.md.
</footer>

<script>
var PLAYERS = __PLAYERS__;
var PICKS = __PICKS__;
var CFG = __CFG__;
var PRESET_ROSTER = __ROSTER__;
__LINEUP_JS__
__UI_JS__
</script>
</body></html>"""


_UI_JS = r"""
var BY_ID = {};
PLAYERS.forEach(function (p) { BY_ID[p.id] = p; });
PICKS.forEach(function (p) { BY_ID[p.id] = p; });

var STORE = 'nba_trade_dashboard_v1';
var state = { roster: [], give: [], get: [] };
try {
  var saved = JSON.parse(localStorage.getItem(STORE) || 'null');
  if (saved && saved.roster) state = saved;
} catch (e) { /* private browsing, or no storage. The page still works. */ }
if (!state.roster.length && PRESET_ROSTER.length) state.roster = PRESET_ROSTER.slice();

function save() {
  try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) {}
}

function fmt(x, digits) {
  var d = digits === undefined ? 1 : digits;
  return (x >= 0 ? '+' : '') + x.toFixed(d);
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}
function asLineupPlayer(p) {
  return { id: p.id, name: p.name, position: p.position, value: p.value,
           availability: p.availability, isPick: !!p.isPick, slots: p.slots || [] };
}

// --- searchable add boxes -------------------------------------------------
function wireSearch(inputId, menuId, candidates, onPick) {
  var input = document.getElementById(inputId);
  var menu = document.getElementById(menuId);
  var active = -1;

  function close() { menu.hidden = true; active = -1; }
  function render() {
    var q = input.value.trim().toLowerCase();
    if (!q) { close(); return; }
    var list = candidates().filter(function (p) {
      return p.search.indexOf(q) !== -1;
    }).slice(0, 9);
    if (!list.length) { close(); return; }
    menu.innerHTML = list.map(function (p, i) {
      return '<div data-id="' + esc(p.id) + '"' + (i === active ? ' class="active"' : '') + '>' +
        '<span><b>' + esc(p.name) + '</b> <span class="meta">' + esc(p.position) +
        (p.team ? ' &middot; ' + esc(p.team) : '') + '</span></span>' +
        '<span class="meta">' + p.value.toFixed(1) + '/wk</span></div>';
    }).join('');
    menu.hidden = false;
  }
  function choose(id) { onPick(id); input.value = ''; close(); refresh(); }

  input.addEventListener('input', render);
  input.addEventListener('focus', render);
  input.addEventListener('keydown', function (e) {
    var items = menu.querySelectorAll('div');
    if (e.key === 'ArrowDown') { active = Math.min(active + 1, items.length - 1); render(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
    else if (e.key === 'Enter') {
      var pick = items[active >= 0 ? active : 0];
      if (pick) { choose(pick.dataset.id); e.preventDefault(); }
    } else if (e.key === 'Escape') { close(); }
  });
  menu.addEventListener('mousedown', function (e) {
    var row = e.target.closest('div[data-id]');
    if (row) { e.preventDefault(); choose(row.dataset.id); }
  });
  document.addEventListener('click', function (e) {
    if (!menu.contains(e.target) && e.target !== input) close();
  });
}

function notOnRoster() {
  return PLAYERS.concat(PICKS).filter(function (p) {
    return state.roster.indexOf(p.id) === -1 && state.get.indexOf(p.id) === -1;
  });
}
function onRoster() {
  return state.roster.filter(function (id) { return state.give.indexOf(id) === -1; })
                     .map(function (id) { return BY_ID[id]; })
                     .filter(Boolean);
}

wireSearch('rosterSearch', 'rosterMenu', function () {
  return PLAYERS.filter(function (p) { return state.roster.indexOf(p.id) === -1; });
}, function (id) { if (state.roster.indexOf(id) === -1) state.roster.push(id); });

wireSearch('giveSearch', 'giveMenu', onRoster, function (id) {
  if (state.give.indexOf(id) === -1) state.give.push(id);
});

wireSearch('getSearch', 'getMenu', notOnRoster, function (id) {
  if (state.get.indexOf(id) === -1) state.get.push(id);
});

// --- chips ----------------------------------------------------------------
function renderChips(elementId, ids, onRemove, onClick) {
  var host = document.getElementById(elementId);
  if (!ids.length) { host.innerHTML = '<span class="empty">nothing yet</span>'; return; }
  host.innerHTML = ids.map(function (id) {
    var p = BY_ID[id];
    if (!p) return '';
    return '<span class="chip' + (p.isPick ? ' pick' : '') + '" data-id="' + esc(id) + '">' +
      '<b>' + esc(p.name) + '</b> <span class="v">' + p.value.toFixed(1) + '</span>' +
      '<button data-remove="' + esc(id) + '" title="remove">&times;</button></span>';
  }).join('');
  host.onclick = function (e) {
    var remove = e.target.dataset.remove;
    if (remove) { onRemove(remove); refresh(); return; }
    var chip = e.target.closest('.chip');
    if (chip && onClick) { onClick(chip.dataset.id); refresh(); }
  };
}

// --- the verdict ----------------------------------------------------------
function flagsFor(result, cfg) {
  var flags = [];
  if (result.dropped.length) {
    flags.push('Puts you over the ' + cfg.rosterLimit + '-man roster limit: you would have to drop ' +
      result.dropped.join(', ') + '. That cost is already in the numbers above.');
  }
  if (result.deltaStarters > 0 && result.deltaDepth < -0.5) {
    flags.push('This upgrade is paid for with depth. Starters ' + fmt(result.deltaStarters) +
      '/wk, but absences now cost ' + Math.abs(result.deltaDepth).toFixed(1) + '/wk more.');
  }
  var holes = result.after.lineup.assignments.filter(function (a) { return !a.player; });
  if (holes.length) {
    flags.push('Leaves a slot you cannot legally fill: ' +
      holes.map(function (a) { return a.slot; }).join(', ') + '.');
  }
  var risky = getPlayers().filter(function (p) { return !p.isPick && p.availability < 0.75; });
  if (risky.length) {
    flags.push('Injury risk coming in: ' + risky.map(function (p) {
      return p.name + ' (' + Math.round(p.availability * 100) + '% of weeks)'; }).join(', ') + '.');
  }
  var rawGap = sumValue(getPlayers()) - sumValue(givePlayers());
  if (rawGap * result.deltaWeekly < 0) {
    flags.push('Raw totals say ' + fmt(rawGap) + '/wk but your lineup says ' + fmt(result.deltaWeekly) +
      '/wk. Fit is deciding this one, not talent — trust the lineup number.');
  }
  if (getPlayers().concat(givePlayers()).some(function (p) { return p.isPick; })) {
    flags.push('Picks are priced as the average player still available at that slot. That is an ' +
      'average outcome, not a promise — the player who falls to you may not be one you want.');
  }
  var emptyBefore = result.before.lineup.assignments
    .filter(function (a) { return !a.player; })
    .map(function (a) { return a.slot; });
  if (emptyBefore.length) {
    flags.push('Your roster cannot fill ' + Array.from(new Set(emptyBefore)).join(', ') +
      ' right now, so anyone arriving is credited with that whole empty slot. Mid-draft that ' +
      'inflates everything: compare picks against each other rather than against a part-built lineup.');
  }
  return flags;
}

function sumValue(list) {
  return list.reduce(function (t, p) { return t + p.value; }, 0);
}
function givePlayers() { return state.give.map(function (id) { return BY_ID[id]; }).filter(Boolean); }
function getPlayers() { return state.get.map(function (id) { return BY_ID[id]; }).filter(Boolean); }

function lineupTable(before, after) {
  var rows = '';
  for (var i = 0; i < before.lineup.assignments.length; i++) {
    var b = before.lineup.assignments[i], a = after.lineup.assignments[i];
    var bn = b.player ? b.player.name : '—';
    var an = a.player ? a.player.name : '—';
    var changed = bn !== an;
    rows += '<tr' + (changed ? ' style="font-weight:600"' : '') + '><td>' + esc(b.slot) + '</td>' +
      '<td>' + esc(bn) + '</td><td>' + (b.player ? b.player.value.toFixed(1) : '') + '</td>' +
      '<td>' + esc(an) + '</td><td>' + (a.player ? a.player.value.toFixed(1) : '') + '</td></tr>';
  }
  return '<table><thead><tr><th>Slot</th><th>Now</th><th>FP/wk</th><th>After</th><th>FP/wk</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>';
}

function marginalTable(strength) {
  var entries = strength.lineup.assignments.filter(function (a) { return a.player; })
    .map(function (a) { return { p: a.player, m: strength.marginal[a.player.id] || 0 }; })
    .sort(function (x, y) { return x.m - y.m; });
  var rows = entries.map(function (e) {
    return '<tr><td>' + esc(e.p.name) + '</td><td>' + esc(e.p.position) + '</td>' +
      '<td>' + e.p.value.toFixed(1) + '</td><td>' + e.m.toFixed(1) + '</td></tr>';
  }).join('');
  return '<table><thead><tr><th>Player</th><th>Pos</th><th>FP/wk</th><th>Marginal</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>';
}

function refresh() {
  save();
  document.getElementById('rosterCount').textContent =
    '(' + state.roster.length + ' of ' + CFG.rosterLimit + ')';
  renderChips('rosterChips', state.roster,
    function (id) {
      state.roster = state.roster.filter(function (x) { return x !== id; });
      state.give = state.give.filter(function (x) { return x !== id; });
    },
    function (id) { if (state.give.indexOf(id) === -1) state.give.push(id); });
  renderChips('giveChips', state.give, function (id) {
    state.give = state.give.filter(function (x) { return x !== id; });
  });
  renderChips('getChips', state.get, function (id) {
    state.get = state.get.filter(function (x) { return x !== id; });
  });

  var host = document.getElementById('result');
  var roster = state.roster.map(function (id) { return BY_ID[id]; }).filter(Boolean).map(asLineupPlayer);
  if (!roster.length) {
    host.innerHTML = '<div class="card"><span class="empty">Add your roster above and the verdict appears here.</span></div>';
    return;
  }
  if (!state.give.length && !state.get.length) {
    var only = rosterStrength(roster, CFG);
    host.innerHTML = '<div class="card"><h2>Your roster as it stands</h2>' +
      '<div class="grid3"><div><div class="stat"><span>Best lineup, everyone healthy</span><span>' +
      only.healthy.toFixed(1) + '</span></div>' +
      '<div class="stat"><span>Expected, allowing for absences</span><span>' + only.expected.toFixed(1) + '</span></div>' +
      '<div class="stat"><span>Cost of absences</span><span>' + only.depthCost.toFixed(1) + '</span></div></div>' +
      '<div style="grid-column:span 2">' + marginalTable(only) +
      '<div class="meta" style="margin-top:6px">Lowest marginal value at the top &mdash; those are the ' +
      'players you are already covering, and the ones to put in a trade.</div></div></div></div>';
    return;
  }

  var cfg = Object.assign({}, CFG, {
    weeksRemaining: parseInt(document.getElementById('weeks').value, 10) || 22,
    stress: (parseFloat(document.getElementById('stress').value) || 0) / 100
  });
  var result = evaluateTrade(roster, state.give, getPlayers().map(asLineupPlayer), cfg);
  var verdict = verdictOf(result);
  var tone = verdict.indexOf('ACCEPT') === 0 ? 'good' : (verdict.indexOf('DECLINE') === 0 ? 'bad' : 'flat');
  var sign = function (x) { return x >= 0 ? 'pos' : 'neg'; };

  var flags = flagsFor(result, cfg).map(function (f) {
    return '<div class="flag">' + f + '</div>';
  }).join('');

  host.innerHTML =
    '<div class="card"><h2>3 &middot; Verdict</h2>' +
    '<div class="verdict ' + tone + '">' + esc(verdict) + '</div>' +
    '<div class="grid3">' +
      '<div><h2>The bottom line</h2>' +
        '<div class="stat"><span>Change per week</span><span class="' + sign(result.deltaWeekly) + '">' +
          fmt(result.deltaWeekly) + '</span></div>' +
        '<div class="stat"><span>Over ' + cfg.weeksRemaining + ' weeks left</span><span class="' +
          sign(result.deltaSeason) + '">' + fmt(result.deltaSeason, 0) + '</span></div>' +
        '<div class="stat"><span>As a share of your lineup</span><span class="' + sign(result.relative) +
          '">' + fmt(result.relative * 100) + '%</span></div>' +
      '</div>' +
      '<div><h2>Where it comes from</h2>' +
        '<div class="stat"><span>Starting lineup</span><span class="' + sign(result.deltaStarters) + '">' +
          fmt(result.deltaStarters) + '</span></div>' +
        '<div class="stat"><span>Depth / injury cover</span><span class="' + sign(result.deltaDepth) + '">' +
          fmt(result.deltaDepth) + '</span></div>' +
        '<div class="stat"><span>Raw totals say</span><span class="meta">' +
          fmt(sumValue(getPlayers()) - sumValue(givePlayers())) + '</span></div>' +
      '</div>' +
      '<div><h2>If the projections are wrong</h2>' +
        '<div class="stat"><span>If they underperform</span><span class="' + sign(result.pessimistic) + '">' +
          fmt(result.pessimistic) + '</span></div>' +
        '<div class="stat"><span>If they overperform</span><span class="' + sign(result.optimistic) + '">' +
          fmt(result.optimistic) + '</span></div>' +
        '<div class="stat"><span>Survives that test</span><span>' + (result.robust ? 'yes' : 'no') + '</span></div>' +
      '</div>' +
    '</div>' + flags + '</div>' +
    '<div class="grid2">' +
      '<div class="card"><h2>Lineup, before and after</h2>' + lineupTable(result.before, result.after) + '</div>' +
      '<div class="card"><h2>Marginal value after the trade</h2>' + marginalTable(result.after) +
      '<div class="meta" style="margin-top:6px">What each week without that player would cost you.</div></div>' +
    '</div>';
}

// --- paste import ---------------------------------------------------------
// Must agree character-for-character with normalise_name() in src/adp/loader.py,
// or a pasted roster silently loses players. tests/test_trade.py checks it does.
function normalise(name) {
  return String(name)
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().trim()
    .replace(/[-\u2010-\u2015]/g, ' ')
    .replace(/[.'\u2019`]/g, '')
    .replace(/\b(jr|sr|ii|iii|iv|v)\b/g, '')
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\s+/g, ' ').trim();
}
var BY_NAME = {};
PLAYERS.forEach(function (p) { BY_NAME[normalise(p.name)] = p.id; });

document.getElementById('pasteBtn').addEventListener('click', function () {
  var raw = document.getElementById('paste').value.split(/[\n,;]+/);
  var added = 0, missed = [];
  raw.forEach(function (line) {
    var name = line.trim();
    if (!name) return;
    var id = BY_NAME[normalise(name)];
    if (!id) { missed.push(name); return; }
    if (state.roster.indexOf(id) === -1) { state.roster.push(id); added++; }
  });
  document.getElementById('pasteResult').innerHTML = 'Added ' + added +
    (missed.length ? '. <b style="color:var(--bad)">Not found: ' + esc(missed.join(', ')) +
      '</b> — check the spelling against the board.' : '.');
  refresh();
});
document.getElementById('clearRoster').addEventListener('click', function () {
  state.roster = []; state.give = []; refresh();
});
document.getElementById('clearTrade').addEventListener('click', function () {
  state.give = []; state.get = []; refresh();
});
['weeks', 'stress'].forEach(function (id) {
  document.getElementById(id).addEventListener('input', refresh);
});

refresh();
"""


def _player_payload(board: pd.DataFrame) -> list[dict]:
    """The player records the page runs on, keyed the same way the CLI keys them."""
    from src.trade import board_to_roster_players

    index = board_to_roster_players(board)
    display = {}
    for row in board.itertuples(index=False):
        name = str(getattr(row, "player_name", "")).strip()
        if name:
            display[name] = row

    payload: list[dict] = []
    for player in index.values():
        row = display.get(player.name)
        team = str(getattr(row, "team", "") or "") if row is not None else ""
        payload.append(
            {
                "id": player.player_id,
                "name": player.name,
                "team": team,
                "position": player.position,
                "value": round(player.weekly_value, 2),
                "availability": round(player.availability, 3),
                "isPick": False,
                "search": f"{player.name} {team} {player.position}".lower(),
            }
        )
    payload.sort(key=lambda p: -p["value"])
    return payload


def _pick_payload(pick_curve: dict, league_cfg=None) -> list[dict]:
    picks = []
    for pick, value in sorted((pick_curve or {}).items()):
        typical = ", ".join(value.typical[:2]) if value.typical else ""
        slots = list(value.eligible_slots(league_cfg)) if league_cfg is not None else []
        picks.append(
            {
                "id": f"pick_{pick}",
                "name": f"Pick #{pick}",
                "team": typical,
                "position": "PICK",
                "value": round(value.weekly_value, 2),
                "availability": round(value.availability, 3),
                "isPick": True,
                "slots": slots,
                "search": f"pick #{pick} draft pick {typical}".lower(),
            }
        )
    return picks


def render_trade_dashboard(
    board: pd.DataFrame,
    output_path: Path | str,
    league_cfg,
    pick_curve: dict | None = None,
    subtitle: str = "",
    my_roster: list[str] | None = None,
    weeks_remaining: int | None = None,
) -> Path:
    """Write the self-contained trade dashboard."""
    from src.trade import eligibility_map, expand_slots, resolve_names, board_to_roster_players

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    slots = expand_slots(league_cfg)
    weeks = int(
        weeks_remaining
        if weeks_remaining is not None
        else league_cfg.calendar.get("regular_season_weeks", 22)
    )

    cfg = {
        "slots": slots,
        "slotNames": sorted(set(slots)),
        "elig": {position: sorted(s) for position, s in eligibility_map(league_cfg).items()},
        "rosterLimit": int(league_cfg.roster_size),
        "weeksRemaining": weeks,
        "stress": 0.10,
    }

    preset: list[str] = []
    if my_roster:
        found, _unmatched = resolve_names(my_roster, board_to_roster_players(board))
        preset = [p.player_id for p in found]

    is_synthetic = bool(board["is_synthetic"].any()) if "is_synthetic" in board.columns else False
    warning = ""
    if is_synthetic:
        warning = (
            '<div class="warn">&#9888; SYNTHETIC DATA &mdash; these are generated players, '
            "not real ones. Every verdict on this page is a demonstration of the machinery, "
            "not advice. Ingest real game logs before trading on it.</div>"
        )

    replacements = {
        "__TITLE__": "Trade Evaluator 2026-27",
        "__SUBTITLE__": html.escape(subtitle),
        "__WARNING__": warning,
        "__WEEKS__": str(weeks),
        "__PLAYERS__": json.dumps(_player_payload(board), ensure_ascii=False),
        "__PICKS__": json.dumps(_pick_payload(pick_curve or {}, league_cfg), ensure_ascii=False),
        "__CFG__": json.dumps(cfg),
        "__ROSTER__": json.dumps(preset),
        "__LINEUP_JS__": LINEUP_JS,
        "__UI_JS__": _UI_JS,
    }
    page = _TEMPLATE
    for token, value in replacements.items():
        page = page.replace(token, value)

    output_path.write_text(page, encoding="utf-8")
    return output_path
