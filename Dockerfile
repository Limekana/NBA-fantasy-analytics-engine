# =============================================================================
# NBA Fantasy Analytics Engine
# =============================================================================
# Built to be useful on a laptop at a draft table: everything needed to compute
# and serve a board is baked in, and no command requires network access except
# the ingest steps you run deliberately beforehand.
#
#   docker build -t nba-fantasy .
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" \
#              nba-fantasy demo
#
# Or pull the published image (see the GitHub release):
#   docker run --rm ghcr.io/limekana/nba-fantasy-analytics-engine:latest scoring-check
# =============================================================================

FROM python:3.11-slim AS base

# Fail fast and loud rather than buffering output into a void mid-draft.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so editing source does not invalidate the wheel cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # nba_api is optional at runtime but baked in here: the container is meant
    # to be able to fetch real data on a machine that has network, and adding it
    # later inside a running container is exactly the kind of thing that fails
    # ten minutes before a draft.
    && pip install --no-cache-dir "nba_api>=1.4" "requests>=2.31"

COPY config/ ./config/
COPY src/ ./src/
COPY tests/ ./tests/
COPY docs/ ./docs/
COPY README.md ./

# Bind-mount points. Declared so a plain `docker run` still works, and so the
# image never ships someone else's data.
RUN mkdir -p data/raw data/processed data/external/adp outputs/reports

# Prove the build is sound at build time rather than discovering it at the draft.
RUN python -m pytest tests/ -q --tb=no \
    && python -m src.cli check-config \
    && rm -rf .pytest_cache

# Non-root: nothing here needs privileges, and bind-mounted output should not
# come back owned by root.
RUN useradd --create-home --uid 1000 analyst \
    && chown -R analyst:analyst /app
USER analyst

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]
