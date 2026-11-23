param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$leagueRepo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Output 'Opening your NBA league dashboard...'
Write-Output ('Project: ' + $leagueRepo)
function Get-LeagueService([int]$Port) {
    try { return Invoke-RestMethod -Uri ("http://127.0.0.1:${Port}/api/session") -TimeoutSec 2 } catch { return $null }
}
function Test-LeaguePortFree([int]$Port) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse('127.0.0.1'), $Port)
    try { $listener.Start(); return $true } catch { return $false } finally { $listener.Stop() }
}
$leagueService = $null
$leaguePort = $null
$availablePort = $null
foreach ($candidatePort in 8765..8775) {
    $candidateService = Get-LeagueService $candidatePort
    if ($candidateService -and $candidateService.app -eq 'nba-fantasy-league-desk') {
        # Services before this check report no repo; treat them as this checkout's.
        if ($candidateService.repo -and ([IO.Path]::GetFullPath([string]$candidateService.repo).TrimEnd('') -ne $leagueRepo.TrimEnd(''))) { continue }
        # A running service keeps the code and settings it started with; restart it after edits.
        $leagueChanged = (Get-ChildItem -Path (Join-Path $leagueRepo 'src'), (Join-Path $leagueRepo 'config') -Recurse -File -Include *.py, *.js, *.html, *.yaml, *.json | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
        $leagueStarted = if ($candidateService.started_at) { [DateTimeOffset]::FromUnixTimeMilliseconds([long]([double]$candidateService.started_at * 1000)).UtcDateTime } else { [DateTime]::MinValue }
        if ($leagueStarted -lt $leagueChanged) {
            Write-Output ('Restarting the dashboard service on port ' + $candidatePort + ': code or settings changed since it started.')
            $leagueOwners = (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $candidatePort -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
            foreach ($leagueOwner in $leagueOwners) { Stop-Process -Id $leagueOwner -Force -ErrorAction SilentlyContinue }
            for ($leagueWait = 0; $leagueWait -lt 20 -and -not (Test-LeaguePortFree $candidatePort); $leagueWait++) { Start-Sleep -Milliseconds 250 }
            $leaguePort = $candidatePort
            break
        }
        $leagueService = $candidateService
        $leaguePort = $candidatePort
        break
    }
    if (-not $availablePort -and (Test-LeaguePortFree $candidatePort)) { $availablePort = $candidatePort }
}
if (-not $leaguePort) { $leaguePort = $availablePort }
if (-not $leaguePort) { throw 'No free dashboard port was found between 8765 and 8775.' }
$leagueUrl = "http://127.0.0.1:${leaguePort}/"
if (-not $leagueService) {
    $leagueCandidates = @(
        (Join-Path $leagueRepo '.venv/Scripts/python.exe'),
        (Join-Path $env:LOCALAPPDATA 'hermes/hermes-agent/venv/Scripts/python.exe')
    )
    $leagueCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($leagueCommand) { $leagueCandidates += $leagueCommand.Source }
    $leaguePython = $null
    Push-Location $leagueRepo
    try {
        foreach ($leagueCandidate in ($leagueCandidates | Select-Object -Unique)) {
            if (-not (Test-Path -LiteralPath $leagueCandidate) -or $leagueCandidate -like '*WindowsApps*') { continue }
            try {
                & $leagueCandidate -X utf8 -c 'import src.cli' 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { $leaguePython = $leagueCandidate; break }
            } catch { Write-Output ('Skipping Python without working project dependencies: ' + $leagueCandidate) }
        }
    } finally { Pop-Location }
    if (-not $leaguePython) { throw 'No working Python with the project dependencies was found. Please copy this message into Codex.' }
    $leagueOutput = Join-Path $leagueRepo 'outputs'
    New-Item -ItemType Directory -Path $leagueOutput -Force | Out-Null
    Write-Output 'Starting the local service. This can take up to 30 seconds.'
    $leagueProcess = Start-Process -FilePath $leaguePython -ArgumentList "-X utf8 -m src.cli league-serve --port $leaguePort" -WorkingDirectory $leagueRepo -WindowStyle Hidden -RedirectStandardOutput (Join-Path $leagueOutput "refresh-service-$leaguePort.log") -RedirectStandardError (Join-Path $leagueOutput "refresh-service-$leaguePort-errors.log") -PassThru
    for ($leagueAttempt = 0; $leagueAttempt -lt 60; $leagueAttempt++) {
        Start-Sleep -Milliseconds 500
        $leagueService = Get-LeagueService $leaguePort
        if ($leagueService) { break }
        if ($leagueProcess.HasExited) { break }
    }
    if (-not $leagueService -or $leagueService.app -ne 'nba-fantasy-league-desk') {
        Get-Content -LiteralPath (Join-Path $leagueOutput "refresh-service-$leaguePort-errors.log") -Tail 15 -ErrorAction SilentlyContinue
        throw "Cannot start the local dashboard on port $leaguePort. Copy the error above into Codex."
    }
}
if (-not $NoBrowser) { Start-Process $leagueUrl }
Write-Output ('League dashboard ready: ' + $leagueUrl)
