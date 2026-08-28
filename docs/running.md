# How to actually run this

## First: the code is not "in the cloud"

The session that built this ran in a temporary container that gets wiped. **None
of it matters for using the tool.** The code lives on GitHub, and everything runs
on your own laptop — offline, at the draft table.

You need to get it onto your laptop **before** draft day, because ingesting NBA
data needs internet and you may not have reliable wifi at the draft.

---

## Which shell am I in?

| You're on | Shell | Do the commands work as written? |
|---|---|---|
| macOS | Terminal (zsh) | ✅ yes |
| Linux | Terminal (bash) | ✅ yes |
| Windows | **PowerShell** | ⚠️ mostly — see the Windows column below |
| Windows | Git Bash or WSL | ✅ yes, same as macOS/Linux |

If you're on Windows and installed Git, you already have **Git Bash** — right-click
in a folder → "Git Bash Here". Using that makes every command in this repo work
exactly as written, and is the path of least resistance.

---

## Setup (once, before draft day)

### Step 1 — get the code

Same on every OS:

```bash
git clone https://github.com/Limekana/NBA-fantasy-analytics-engine.git
cd NBA-fantasy-analytics-engine
```

### Step 2 — pick ONE of these two ways to run it

---

## Option A: Plain Python (simplest — recommended)

Honestly, this is easier than Docker for a single-user tool. No daemon, no
images, no volume mounts.

**macOS / Linux / Git Bash:**
```bash
pip install -r requirements.txt
python -m src.cli check-config
```

**Windows PowerShell:**
```powershell
pip install -r requirements.txt
python -m src.cli check-config
```

That's it — the commands are identical, because they're all `python -m src.cli ...`.
Everything in this repo's docs that starts with `python -m src.cli` works
unchanged in PowerShell.

If `python` isn't found on Windows, try `py -3` instead:
```powershell
py -3 -m pip install -r requirements.txt
py -3 -m src.cli check-config
```

---

## Option B: Docker

Needs Docker Desktop installed and running. Use `docker compose`, which is
identical on every OS and needs no shell aliases:

```bash
docker compose run --rm engine check-config
docker compose run --rm engine data-help
docker compose run --rm engine demo
docker compose run --rm engine build-board
docker compose run --rm engine draft --pick 17 --slot 4
```

**This works verbatim in PowerShell, Terminal, and Git Bash.** That's why it's
the recommended Docker path — the `alias nba='docker run -v ...'` form I showed
earlier is bash-only and does not work in PowerShell.

The first `docker compose run` builds the image (a few minutes). After that it's
instant. `config/`, `data/` and `outputs/` are shared with your laptop, so
editing `config/assumptions.yaml` when news breaks takes effect on the next
command with no rebuild.

<details>
<summary>If you'd rather use the published image than build locally</summary>

```bash
docker pull ghcr.io/limekana/nba-fantasy-analytics-engine:latest
```

Then, PowerShell:
```powershell
function nba { docker run --rm -v "${PWD}/config:/app/config" -v "${PWD}/data:/app/data" -v "${PWD}/outputs:/app/outputs" ghcr.io/limekana/nba-fantasy-analytics-engine:latest @args }
nba check-config
```

macOS / Linux / Git Bash:
```bash
alias nba='docker run --rm -v "$PWD/config:/app/config" -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" ghcr.io/limekana/nba-fantasy-analytics-engine:latest'
nba check-config
```

Note that a PowerShell **function** with `@args` is the equivalent of a bash
alias here — `Set-Alias` cannot carry arguments, so it will not work.
</details>

---

## Before draft day (needs internet)

Run these at home, not at the venue:

```bash
pip install nba_api                                  # or: docker compose handles it
python -m src.cli ingest --season 2025-26
python -m src.cli ingest --season 2024-25
python -m src.cli ingest --season 2023-24
python -m src.cli backtest
python -m src.cli build-board
```

Now `outputs/draft_board.html` exists on your laptop. **Open it in a browser and
bookmark it** — it's a single self-contained file that works with no internet, no
Python and no Docker. If everything else fails at the draft, that file alone is
still a full sortable draft board.

## At the draft (no internet needed)

```bash
python -m src.cli availability --slot 4
python -m src.cli draft --pick 17 --slot 4 --drafted drafted.txt
```

Keep `drafted.txt` as a plain list of names taken so far, one per line, and add
to it as the draft goes.

---

## Cutting a release

You only need this if you want the downloadable container.

| Where | Command |
|---|---|
| macOS / Linux / Git Bash | `./scripts/release.sh v0.1.0` |
| Windows PowerShell | `.\scripts\release.ps1 v0.1.0` |
| **Any browser, incl. phone** | github.com → **Actions** → **Release** → *Run workflow* → type `v0.1.0` |

The browser option needs no terminal at all and is the easiest if you're away
from your laptop.

---

## Common snags

**`python: command not found` (Windows)** — use `py -3` instead of `python`, or
reinstall Python with "Add to PATH" ticked.

**`./scripts/release.sh` fails on Windows PowerShell** — that's a bash script.
Use `.\scripts\release.ps1`, or run it from Git Bash.

**`docker: command not found`** — Docker Desktop isn't installed or isn't
running. Use Option A (plain Python) instead; it does exactly the same thing.

**Docker pull asks for a login** — GHCR packages start private even on a public
repo. Either build locally with `docker compose run --rm engine ...`, or make the
package public in your GitHub profile → Packages → Package settings.

**`No module named 'src'`** — you're not in the repo root. `cd` into the
`NBA-fantasy-analytics-engine` folder first.
