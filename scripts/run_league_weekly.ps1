# Run by the "NBA League Weekly Forecast" scheduled task; safe to run by hand.
# Results and errors go to outputs/league-weekly.log.
$ErrorActionPreference = 'Stop'
$leagueRepo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$leagueLog = Join-Path $leagueRepo 'outputs/league-weekly.log'
Set-Location $leagueRepo
function Write-LeagueLog([string]$Text) {
    Add-Content -LiteralPath $leagueLog -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Text) -Encoding UTF8
}
$leagueCandidates = @(
    (Join-Path $leagueRepo '.venv/Scripts/python.exe'),
    (Join-Path $env:LOCALAPPDATA 'hermes/hermes-agent/venv/Scripts/python.exe')
)
$leagueCommand = Get-Command python -ErrorAction SilentlyContinue
if ($leagueCommand) { $leagueCandidates += $leagueCommand.Source }
$leaguePython = $null
foreach ($leagueCandidate in ($leagueCandidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $leagueCandidate) -or $leagueCandidate -like '*WindowsApps*') { continue }
    & $leagueCandidate -X utf8 -c 'import src.cli' 2>$null
    if ($LASTEXITCODE -eq 0) { $leaguePython = $leagueCandidate; break }
}
if (-not $leaguePython) {
    Write-LeagueLog 'FAILED: no working Python with the project dependencies was found.'
    exit 1
}
Write-LeagueLog ('Starting league-weekly with ' + $leaguePython)
$leagueOutput = & $leaguePython -X utf8 -m src.cli league-weekly 2>&1 | Out-String
$leagueExit = $LASTEXITCODE
Write-LeagueLog $leagueOutput.Trim()
Write-LeagueLog ('Finished with exit code ' + $leagueExit)
exit $leagueExit
