# NBA Fantasy Analytics Engine

Quantitative draft system for a **10-team Sleeper Fantasy Basketball league in
Lock-In mode**, 2026-27 season.

Player value is computed from your league's actual scoring settings, game-level
history, projected roles, schedule, availability, bonus frequency and Lock-In
mechanics — not from generic fantasy rankings.

---

# Setup guide (start here)

Written for someone who has never used a terminal. Follow it top to bottom.
Commands are **Windows PowerShell**; where macOS/Linux differs it's marked.

**Do all of this before draft day.** Steps 5–7 need internet, and venue wifi is
not something to bet a draft on.

> **The code is not "in the cloud."** It lives on GitHub. You copy it to your
> laptop once, and from then on it runs locally — offline.

---

## Step 0 — Open PowerShell

Press `Windows key`, type `powershell`, press Enter. A blue window opens.

That's it — that's "the terminal". You type a command, press Enter, it runs.

Two things worth knowing before anything else:

- **You are always "in" a folder.** The path shown before the `>` is where you
  are. Commands act on that folder.
- **`cd` means "change directory"** — it's how you move between folders.

Try it. Type this and press Enter:

```powershell
cd ~
```

`~` is shorthand for your user folder (`C:\Users\YourName`). You're now there.

> *macOS/Linux: open **Terminal** instead. Everything below works the same
> except where noted.*

---

## Step 1 — Install Python

Python is the language this tool is written in. You need it to run anything.

1. Go to **[python.org/downloads](https://www.python.org/downloads/)**
2. Download the latest Python 3 (3.11 or newer)
3. Run the installer — **⚠️ tick the box that says "Add python.exe to PATH"** at
   the bottom of the first screen. This is the single most common thing people
   miss, and skipping it means every command below fails with
   `python: command not found`.
4. Click Install Now

**Check it worked.** Close PowerShell, open a fresh one, and type:

```powershell
python --version
```

You should see something like `Python 3.12.4`.

<details>
<summary>If you get an error or it opens the Microsoft Store</summary>

Windows ships a fake `python` that opens the Store. Try `py --version` instead.
If `py` works, use `py` everywhere below in place of `python`.

If neither works, you missed the "Add to PATH" checkbox — re-run the installer,
choose **Modify**, and tick it.
</details>

---

## Step 2 — Install Git

Git is how you copy the code from GitHub, and how you get updates later.

1. Go to **[git-scm.com/downloads](https://git-scm.com/downloads)**
2. Download and run the installer
3. Accept every default (there are a lot of screens — just keep clicking Next)

**Check it worked** in a fresh PowerShell window:

```powershell
git --version
```

You should see something like `git version 2.45.1`.

---

## Step 3 — Copy the code to your laptop

This is called **cloning**. It downloads the whole project into a new folder.

```powershell
cd ~
git clone https://github.com/Limekana/NBA-fantasy-analytics-engine.git
cd NBA-fantasy-analytics-engine
```

Line by line:
- `cd ~` — go to your user folder
- `git clone <url>` — download the project (creates a folder with that name)
- `cd NBA-fantasy-analytics-engine` — step into it

**You are now in the project folder. Every command from here on assumes that.**
If you close PowerShell and come back later, you must `cd` back in first:

```powershell
cd ~\NBA-fantasy-analytics-engine
```

Check you're in the right place — this should list files like `README.md` and
folders like `src`:

```powershell
ls
```

---

## Step 4 — Install the tool's dependencies

This project uses a few Python libraries (for maths and tables). Install them:

```powershell
python -m pip install -r requirements.txt
```

`requirements.txt` is a list of what's needed; `pip` is Python's installer. This
takes a minute or two and prints a lot of text. That's normal.

<details>
<summary>Optional but recommended: use a virtual environment</summary>

A "venv" keeps this project's libraries separate from everything else on your
machine, so projects can't break each other. Good practice, and worth learning.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

After activating you'll see `(.venv)` at the start of your prompt. **You must
re-activate every time you open a new PowerShell window:**

```powershell
cd ~\NBA-fantasy-analytics-engine
.\.venv\Scripts\Activate.ps1
```

**If activation fails** with *"running scripts is disabled on this system"*,
Windows is blocking scripts by default. Fix it once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Then try activating again. This only affects your own user account.

*macOS/Linux activation is `source .venv/bin/activate` instead.*
</details>

---

## Step 5 — Check your league settings are right

**Do this before anything else touches data.** Every number the system produces
comes from these settings, so if they're wrong, everything downstream is wrong.

```powershell
python -m src.cli check-config
```

This prints your scoring rules. **Compare them against your league's settings
page on Sleeper, line by line.** Currently configured:

| | |
|---|---|
| points | 0.5 |
| rebounds / assists | 1 |
| steals / blocks | 2 |
| turnovers / fouls | −1 |
| free throws made | 0.25 |
| threes made | 0.5 |
| double-double | +2 |
| triple-double | +3 |
| 40+ points | +3 |
| 50+ points | +4 |
| 15+ assists | +3 |
| 20+ rebounds | +3 |

If anything differs, open `config\league.yaml` in Notepad and change the number.
Nothing else needs editing — the whole pipeline reads from that one file.

### One rule still needs your eyes

```powershell
python -m src.cli scoring-check
```

Find the line marked `50 pts exactly  <-- KEY TEST`. It currently assumes a
50-point game pays **both** the 40+ and 50+ bonuses (7 points total). I couldn't
verify that against Sleeper's docs. Check a real 50-point game in Sleeper's
scoring view. If it only pays 4, open `config\league.yaml` and change:

```yaml
  points_thresholds_stack: false
```

---

## Step 6 — Try it on fake data first

> Demo data is written to `data\demo\`, kept separate from `data\raw\` so it can
> never mix into a real board. If you followed an older version of this guide and
> see `SYNTHETIC_draft_board.csv` after Step 9, delete the leftovers:
> `Remove-Item data\raw\*\SYNTHETIC_* -Recurse`

Before dealing with real data, prove the tool runs:

```powershell
python -m src.cli demo
```

This generates fictional players, runs the entire pipeline, and writes a draft
board. Takes a minute or two.

**Everything it produces is fake** and labelled as such. It exists only to
confirm the machinery works on your machine.

Open the result to see what you're building toward:

```powershell
start outputs\SYNTHETIC_draft_board.html
```

*(macOS: `open outputs/SYNTHETIC_draft_board.html`)*

A sortable board should open in your browser. Click column headers to sort.

---

## Step 7 — Get the real data

Now the real thing. **This step needs internet.**

### 7a. Install the NBA data library

```powershell
python -m pip install nba_api
```

### 7b. Download three seasons of game logs

Run these one at a time. Each takes 1–3 minutes.

```powershell
python -m src.cli ingest --season 2025-26
python -m src.cli ingest --season 2024-25
python -m src.cli ingest --season 2023-24
```

**Check each one.** It prints something like `Wrote 26,431 rows`. A full NBA
season is roughly **26,000 rows**. If you see a few hundred, the download was
cut short — run it again. Don't build a board on a truncated season.

You need **at least two seasons** or Step 8 can't run.

### 7c. Download player ages and positions

```powershell
python -m src.cli fetch-players
```

Game logs don't include ages or positions. Without this, the model assumes
everyone is 27 and can't work out positional scarcity.

### 7d. Download the schedule

```powershell
python -m src.cli fetch-schedule --season 2026-27
```

This one matters more than it looks. In Lock-In, how many games a team plays in
a week directly changes player value. If the 2026-27 schedule isn't published
yet, skip it — the model falls back to historical patterns and tells you it did.

> Both files land in `data\external\` and are picked up automatically. No flags
> to remember.

<details>
<summary>No internet, or the downloads fail?</summary>

Any CSV works. Run `python -m src.cli data-help` for the full list of sources
and the exact format. You don't need to rename columns — `PTS`, `TRB`, `TOV`,
`MP` and friends are all recognised automatically.
</details>

---

## Step 8 — Backtest (do not skip this)

This is the step that tells you whether to trust the model.

```powershell
python -m src.cli backtest
```

It does two things:

**1. Tests the locking strategies** on real past weeks — replaying what each
approach would actually have scored. No projections involved, so this is the
most trustworthy output in the system.

**2. Tests the projection model** against dumb baselines like "just use last
season's average". It trains only on older seasons and predicts a newer one, so
it can't cheat.

**Read the VERDICT at the bottom.** It says one of three things:

- *"Model beats the best baseline"* — good, trust the model rank.
- *"Model is level with..."* — the extra machinery isn't earning its place yet.
  Weight the model rank and last season's FP/game about equally.
- *"Model LOSES to..."* — something is wrong. Don't trust model rank over the
  baseline until you've looked into it.

It's designed to tell you when it isn't working. That's the point.

<details>
<summary>Tuning the model (optional)</summary>

```powershell
python -m src.cli backtest --tune
```

Grid-searches how much to weight each past season. If it consistently prefers
different weights than the defaults, edit `season_weights` in
`config\model.yaml`. Read the caution it prints — it's easy to fool yourself
here.
</details>

---

## Step 9 — Build your real draft board

```powershell
python -m src.cli build-board
```

Writes three files into `outputs\`:

| File | What it's for |
|---|---|
| `draft_board.html` | **The main thing.** Sortable, filterable, open in a browser |
| `draft_board.csv` | Same data as a spreadsheet |
| `player_values.csv` | Underlying per-player stats |

Open it:

```powershell
start outputs\draft_board.html
```

**Read the WARNINGS the command prints.** They tell you what's assumed rather
than measured — missing schedule, unverified rookie landing spots, no ADP.

> **Do this bit now, not on draft day:** that HTML file is completely
> self-contained. No Python, no internet, no terminal. **Bookmark it, and email
> it to yourself so it's on your phone too.** If everything else goes wrong at
> the venue, that one file is still a full draft board.

---

## Step 10 — Set your draft slot

Once you know your pick position from the lottery, open `config\league.yaml` in
Notepad and set:

```yaml
draft:
  my_draft_slot: 4     # your position, 1 to 10
```

---

## Step 11 — Draft day

**Before you leave the house**, confirm it still runs:

```powershell
python -m src.cli availability
```

This shows who's likely to still be there at each of your picks. Nothing below
needs internet.

### As the draft runs

Keep a plain text file of who's been taken. Make it in Notepad, save it as
`drafted.txt` in the project folder, one name per line:

```
# round 1
Nikola Jokic
Luka Doncic
Victor Wembanyama
```

**Spelling is forgiving — don't fight the accents.** All of these find the right
player:

| You type | It finds |
|---|---|
| `Luka Doncic` | Luka Dončić |
| `nikola jokic` | Nikola Jokić |
| `Shai Gilgeous Alexander` | Shai Gilgeous-Alexander |
| `PJ Washington` | P.J. Washington |
| `DeAaron Fox` | De'Aaron Fox |
| `Jaren Jackson` | Jaren Jackson Jr. |

Accents, capitals, hyphens, apostrophes, periods, `Jr.`/`Sr.` suffixes and extra
spaces are all ignored. Blank lines and lines starting with `#` are skipped, so
you can keep notes in the file.

**If you genuinely misspell a name**, the tool won't quietly ignore it — it
prints a loud warning at the top of the output naming the unmatched entry and
suggesting who you probably meant. Take that seriously: an unmatched name means
that player is still being treated as available, so the recommendation below it
may already be off the board.

When you're on the clock:

```powershell
python -m src.cli draft --pick 17 --drafted drafted.txt
```

Change `--pick 17` to whatever the current overall pick number is. Add names to
`drafted.txt` and save as players come off the board.

You'll get top available players, who's likely to disappear before your next
pick, and a recommendation **with the reasoning shown** — never just a name.

If you want to tell it what you already have (helps with positional need):

```powershell
python -m src.cli draft --pick 17 --drafted drafted.txt --roster PG,C,SF
```

---

## The whole thing, as a checklist

```powershell
# One time
cd ~\NBA-fantasy-analytics-engine
python -m pip install -r requirements.txt
python -m pip install nba_api

# Before draft day (needs internet)
python -m src.cli check-config
python -m src.cli scoring-check
python -m src.cli ingest --season 2025-26
python -m src.cli ingest --season 2024-25
python -m src.cli ingest --season 2023-24
python -m src.cli fetch-players
python -m src.cli fetch-schedule --season 2026-27
python -m src.cli backtest
python -m src.cli build-board
start outputs\draft_board.html

# Draft day (offline)
python -m src.cli availability
python -m src.cli draft --pick 17 --drafted drafted.txt
```

---

## When something breaks

| Error | Fix |
|---|---|
| `python : The term 'python' is not recognized` | Python isn't on PATH. Try `py` instead, or reinstall ticking "Add to PATH" |
| `No module named 'src'` | You're not in the project folder. `cd ~\NBA-fantasy-analytics-engine` |
| `No module named 'pandas'` | Step 4 didn't finish. Re-run `python -m pip install -r requirements.txt` |
| `running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |
| `No game logs found` | Step 7b didn't work. Check `data\raw\` has folders with CSVs in them |
| Ingest returns only a few hundred rows | Download was truncated. Run it again |
| `backtest` says "needs at least two seasons" | Ingest a second season |

Getting an update later:

```powershell
cd ~\NBA-fantasy-analytics-engine
git pull
```

---

## Learning Git and Python

You mentioned wanting to learn these. You genuinely only need a small amount to
use this tool, and you've already used most of it above.

**Git** — you've now used `clone` and `pull`, which covers most day-to-day use.
The next three worth knowing are `status` (what changed), `add`/`commit` (save a
checkpoint), and `push` (send it to GitHub).

- [Git in 15 minutes](https://try.github.io) — interactive, no install
- [Learn Git Branching](https://learngitbranching.js.org) — visual, genuinely fun
- [Pro Git, chapters 1–3](https://git-scm.com/book/en/v2) — free, the standard reference

**Python** — to read and modify this project you mainly need variables,
functions, dictionaries and lists.

- [Automate the Boring Stuff](https://automatetheboringstuff.com) — free online, aimed at non-programmers
- [Python Tutor](https://pythontutor.com) — paste code, watch it run line by line

**Best way to learn on this project specifically:** open `config\model.yaml`,
change a number, re-run `python -m src.cli build-board`, and see what moves.
Every value in there is documented with what it does. Nothing you change in
`config\` can break the code — worst case, `git checkout config/` puts it back.

If you want to see how a piece works, `src\scoring\engine.py` is the best
starting point: it's the smallest, most self-contained part, and everything else
depends on it.

---
---

# Reference

Everything below is background rather than instructions.

## Status

| Component | State |
|---|---|
| Scoring engine | Working, 260 tests |
| Lock-In simulator | Working — optimal-stopping model |
| Backtesting | Working — projection + strategy backtests |
| Projections, games-played model | Working |
| Rookie projections | 2026 class configured, 4 flagged for verification |
| Draft board, tiers, VOR | Working |
| Monte Carlo draft simulator | Working |
| Live draft assistant | Working |
| Docker image | Built and published by CI on tag |
| **Real NBA data ingested** | **Not yet — that's Step 7** |

## Documentation

| File | Contents |
|---|---|
| [`docs/running.md`](docs/running.md) | Per-OS setup, Docker vs Python |
| [`docs/lockin_strategy.md`](docs/lockin_strategy.md) | How to play Lock-In, measured |
| [`docs/lock_in_mechanics.md`](docs/lock_in_mechanics.md) | What was verified vs assumed, with sources |
| [`docs/assumptions.md`](docs/assumptions.md) | Every assumption, rated by impact |
| [`docs/data_sources.md`](docs/data_sources.md) | Where data comes from |
| [`docs/schemas.md`](docs/schemas.md) | Data formats |

## Running with Docker instead

If you'd rather not install Python. Needs Docker Desktop running.

```powershell
docker compose run --rm engine check-config
docker compose run --rm engine build-board
docker compose run --rm engine draft --pick 17
```

Works identically in PowerShell, Terminal and Git Bash. See
[`docs/running.md`](docs/running.md).

## Cutting a release

| Where | Command |
|---|---|
| Windows PowerShell | `.\scripts\release.ps1 v0.1.0` |
| macOS / Linux / Git Bash | `./scripts/release.sh v0.1.0` |
| Any browser, incl. phone | GitHub → Actions → Release → Run workflow |

## Is the 37% rule right for Lock-In? No — and the code shows why

The 37% (secretary) rule is genuinely optimal, but for a different problem:

| | Secretary problem | Lock-In week |
|---|---|---|
| Objective | maximise P(picking the single best) | maximise **expected points** |
| Payoff | all-or-nothing; 2nd best scores zero | 2nd-best game scores nearly as much |
| Distribution | unknown; only relative ranks | **known** — the player's own game log |
| n | large | 2–4 |

The third row is decisive: we are not guessing in the dark. Knowing a player's
scoring distribution lets us compute the exact value of continuing, which the 37%
rule throws away. The small `n` makes it worse — with 3.4 games a week,
`floor(n/e) = 1`, and in a 2-game week `floor(2/e) = 0`, so it locks Monday
unconditionally.

Replayed over real chronological weeks (`python -m src.cli backtest`):

```
strategy       fp_per_week   share of available edge
optimal_iid         19.55                     100%
percentile          19.40                      95%
secretary (37%)     18.05                      49%
threshold           17.18                      17%
last_game           16.63                       0%
```

The 37% rule beats doing nothing but captures under half the available edge.
`optimal_iid` — lock when the game in hand beats `W[k] = E[max(F, W[k-1])]` —
is what the model uses.

## What makes this a Lock-In model, not a fantasy ranking

In Lock-In **only one game per player counts each week**. You lock a completed
performance before that player's next game; if you never lock, Sleeper takes
their **final game of the week**. That single rule changes the arithmetic of
player value completely:

| Ordinary fantasy | Lock-In |
|---|---|
| Value ≈ FP/game × games played | Value ≈ Σ over weeks of E[best *chosen* game] |
| Consistency is prized | **Variance is an asset** |
| An extra game adds points | An extra game adds an **option** |
| Schedule is a tiebreak | Games-per-week is a first-order value driver |

Two players averaging an identical 35.0 FP/game differ by **~5.8 FP per week**
purely because one is volatile — the week keeps the good draw and discards the
bad ones. That result is a test
(`test_volatility_is_an_asset_in_lock_in`), not a claim.

The model reports the whole decision spectrum rather than one number:

| Column | Meaning |
|---|---|
| `lockin_auto` | You never lock. Sleeper takes the last game. **Lower bound.** |
| `lockin_value` | Optimal stopping with no knowledge of the future. **The realistic estimate.** |
| `lockin_perfect` | Clairvoyance. **Explicitly an upper bound**, never a projection. |
| `lock_in_advantage` | `lockin_value` − raw FP/game. The Lock-In edge. |

`lockin_value` uses backward induction over the player's own empirical
distribution: with `k` games left, continuing is worth `W[k] = E[max(F, W[k-1])]`,
so you lock when the game in hand beats that. Early in the week you should pass
on a good game; by Saturday the same score is a lock.

---

## Design decisions worth knowing

**Bonuses are non-linear, so the average stat line is not scored.** Projecting a
per-game line and pushing it through the scoring engine silently prices bonuses
near zero — a player averaging 26 points never crosses 40 on their *average*
line but does in real games. The linear terms are computed from the projected
line; expected bonus points are projected separately from historical bonus rates.
See `src/valuation.py`.

**Tiers are cut at gaps, not every N players.** A tier should mean "these players
are interchangeable"; the useful information is where that stops being true.
Cuts fall where the value drop exceeds ~1 SD of the local gap distribution.

**Archetypes are discovered, not defined.** k-means over standardised production
and distribution shape, labelled afterward by whichever feature each cluster is
most extreme on. No archetype is assumed valuable.

**Availability reduces usable weeks, not a season point total.** Missing one game
of a four-game week costs almost nothing in Lock-In. Missing the week costs
everything.

**Facts and assumptions are kept apart.** `src/distributions/` produces measured
facts; `src/projections/` produces assumptions; every player carries an
`assumption_notes` column explaining what was applied to them.

---

## Project layout

```
config/
  league.yaml        league settings + scoring + bonus interaction rules
  model.yaml         every tunable model parameter
  assumptions.yaml   per-player injury / role / rookie overrides (edit as news breaks)
  sources.yaml       data source registry + ADP provenance
src/
  config.py          config loading and validation
  schemas.py         canonical dataframe schemas
  scoring/           deterministic scoring engine
  ingestion/         nba_api, Sleeper, CSV, synthetic adapters
  distributions/     player profiles, percentiles, bonus rates, archetypes
  projections/       per-36 blend, age curve, games-played, rookie priors
  lockin/            Lock-In strategies and simulator
  schedule/          fantasy weeks, games-per-week
  adp/               multi-source ADP, name matching, value gaps
  draft/             board, tiers, VOR, Monte Carlo simulator, live assistant
  reporting/         interactive HTML board
  valuation.py       combines everything into a player valuation
  backtest.py        projection + Lock-In backtests, weight tuning
  pipeline.py        the one reproducible path from raw data to board
tests/               260 tests
docs/
  lock_in_mechanics.md   what was verified vs assumed, with sources
  lockin_strategy.md     how to play Lock-In, measured (incl. the 37% rule)
  running.md             per-OS setup: PowerShell vs Terminal, Docker vs Python
  data_sources.md        how to get real data in
  assumptions.md         full assumption register + known gaps
  schemas.md             data contracts
outputs/               draft_board.csv / .html, player_values.csv
```

---

## Engineering rules (from the handoff)

1. **Everything configurable.** No hardcoded scoring values — enforced by tests.
2. **Raw data immutable.** `data/raw/` is written once, never edited.
3. **Every transformation reproducible.** `src/pipeline.py` is the only path to `outputs/`.
4. **Test scoring before projections.** A perfect projection with wrong scoring is worthless.
5. **No ML before a baseline.** Transparent weighted model; no neural networks.
6. **Track timestamps.** Every ingest writes a provenance manifest.
7. **Separate facts from assumptions.** Enforced by module boundaries and surfaced per player.

---

## Honest limitations

- **No real data has been through this yet.** Ingestion is untested against live APIs.
- **The projection model has not yet beaten a naive baseline.** Backtesting is now
  implemented, and on synthetic data the model ranks players about as well as
  "last season's FP/game" and no better. That is the honest current state — and
  it is what handoff Rule 5 asks you to confront rather than paper over. Re-run
  `backtest --tune` on real data before trusting model rank over the baseline.
- **Rookie projections are pure prior.** Four of the eight configured 2026
  rookies have landing spots from single or unconfirmed sources and are flagged
  `VERIFY` in the pipeline warnings.
- **No ADP is loaded**, so market-inefficiency analysis is unavailable until you
  add sources — the board currently ranks on model value alone.
- **The real 2026-27 schedule is not loaded**, so Lock-In values use historical
  weekly cadence. This is the highest-leverage missing input.

Full detail in [`docs/assumptions.md`](docs/assumptions.md).
