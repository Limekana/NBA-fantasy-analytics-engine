## NBA Fantasy Analytics Engine

Quantitative draft system for a 10-team Sleeper **Lock-In** league.

### Run it with Docker

```bash
# pull (works on Apple Silicon and x86)
docker pull ghcr.io/limekana/nba-fantasy-analytics-engine:latest

# or load the tarball attached below, if you want it fully offline
gunzip -c nba-fantasy-*-docker.tar.gz | docker load
```

Then, from a folder where you want the data and outputs to live:

```bash
mkdir -p config data outputs

# copy the configs out of the image so you can edit them on the host
docker run --rm ghcr.io/limekana/nba-fantasy-analytics-engine:latest --help
docker create --name tmp ghcr.io/limekana/nba-fantasy-analytics-engine:latest \
  && docker cp tmp:/app/config ./ && docker rm tmp

# from here on, this alias is all you need
alias nba='docker run --rm -v "$PWD/config:/app/config" -v "$PWD/data:/app/data" \
  -v "$PWD/outputs:/app/outputs" ghcr.io/limekana/nba-fantasy-analytics-engine:latest'
```

### Draft-day sequence

```bash
nba check-config                 # confirm scoring matches your league
nba scoring-check                # verify bonus rules against Sleeper
nba data-help                    # exactly where to get data and in what format
nba ingest --season 2025-26      # needs internet; run this BEFORE the draft
nba backtest                     # validate the model - do not skip
nba build-board                  # writes outputs/draft_board.csv and .html
nba availability --slot 4        # who reaches each of your picks
nba draft --pick 17 --slot 4     # on the clock
```

`config/` is bind-mounted, so editing `config/assumptions.yaml` when news breaks
takes effect on the next command with no rebuild.

### Before you trust the board

1. **Confirm the bonus rules.** `nba scoring-check` prints reference stat lines.
   Whether a 50-point game also pays the 40+ bonus is still an unverified
   assumption — compare against your league's Sleeper scoring and flip
   `bonus_rules.points_thresholds_stack` if it disagrees.
2. **Run the backtest.** The projection model is unvalidated until it has beaten
   naive baselines on real data. `nba backtest` reports the verdict honestly,
   including when the model does not earn its complexity.
3. **Verify the rookies.** Four of the eight configured rookies have landing
   spots from single or unconfirmed sources and are flagged `VERIFY` in the
   pipeline warnings.

Full detail in `README.md` and `docs/assumptions.md`.
