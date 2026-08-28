#!/usr/bin/env bash
# Cut a release. CI does the rest: builds the container for amd64 + arm64,
# publishes it to ghcr.io, and attaches a loadable tarball to the release.
#
#   ./scripts/release.sh v0.1.0
#
# You can also do this entirely from a phone: on github.com go to
# Actions -> Release -> "Run workflow", and type the tag. No terminal needed.

set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
    echo "usage: $0 <tag>    e.g. $0 v0.1.0" >&2
    exit 1
fi
if ! [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
    echo "error: tag must look like v1.2.3 (the release workflow triggers on 'v*')" >&2
    exit 1
fi

echo "==> Running tests before tagging"
python -m pytest -q

echo "==> Validating configuration"
python -m src.cli check-config

if [ -n "$(git status --porcelain)" ]; then
    echo "error: working tree is dirty. Commit or stash first." >&2
    exit 1
fi

echo "==> Tagging $TAG"
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"

REPO="$(git remote get-url origin | sed -E 's#.*github.com[:/]##; s#\.git$##')"
echo
echo "Tag pushed. CI is now building the container."
echo "  Watch:   https://github.com/$REPO/actions"
echo "  Release: https://github.com/$REPO/releases/tag/$TAG"
echo
echo "Once it finishes:"
echo "  docker pull ghcr.io/$(echo "$REPO" | tr '[:upper:]' '[:lower:]'):$TAG"
