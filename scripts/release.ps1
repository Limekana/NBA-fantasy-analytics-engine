# Cut a release from Windows PowerShell.
#
#   .\scripts\release.ps1 v0.1.0
#
# CI does the rest: builds the container for amd64 + arm64, publishes it to
# ghcr.io, and attaches a loadable tarball to the release.
#
# You can also skip this entirely and trigger it from a browser (phone included):
# github.com -> Actions -> Release -> "Run workflow" -> type the tag.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Tag
)

$ErrorActionPreference = "Stop"

if ($Tag -notmatch '^v\d+\.\d+\.\d+') {
    Write-Error "Tag must look like v1.2.3 (the release workflow triggers on 'v*')"
    exit 1
}

Write-Host "==> Running tests before tagging" -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Error "Tests failed - not tagging."; exit 1 }

Write-Host "==> Validating configuration" -ForegroundColor Cyan
python -m src.cli check-config
if ($LASTEXITCODE -ne 0) { Write-Error "Config invalid - not tagging."; exit 1 }

if (git status --porcelain) {
    Write-Error "Working tree is dirty. Commit or stash first."
    exit 1
}

Write-Host "==> Tagging $Tag" -ForegroundColor Cyan
git tag -a $Tag -m "Release $Tag"
git push origin $Tag

$repo = (git remote get-url origin) -replace '.*github\.com[:/]', '' -replace '\.git$', ''
Write-Host ""
Write-Host "Tag pushed. CI is now building the container."
Write-Host "  Watch:   https://github.com/$repo/actions"
Write-Host "  Release: https://github.com/$repo/releases/tag/$Tag"
Write-Host ""
Write-Host "Once it finishes:"
Write-Host "  docker pull ghcr.io/$($repo.ToLower()):$Tag"
