$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillRoot = Split-Path -Parent $ScriptDir
$StateDir = Join-Path $SkillRoot "state"
$PidPath = Join-Path $StateDir "proxy.pid"
$ProxyScript = Join-Path $ScriptDir "proxy.py"

if (-not (Test-Path -LiteralPath $PidPath)) {
    Write-Host "No Codex ModelX PID file found."
    exit 0
}

$pidText = (Get-Content -LiteralPath $PidPath -Raw).Trim()
if ($pidText -notmatch '^\d+$') {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Host "Removed invalid PID file."
    exit 0
}

$targetPid = [int]$pidText
$process = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Host "Codex ModelX proxy was not running. Removed stale PID file."
    exit 0
}

$commandLine = ""
try {
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction Stop
    $commandLine = [string]$cim.CommandLine
} catch {}

if ($commandLine -and ($commandLine -notlike "*$ProxyScript*")) {
    throw "PID $targetPid does not look like the Codex ModelX proxy. Refusing to stop it. CommandLine: $commandLine"
}

Stop-Process -Id $targetPid -Force
Remove-Item -LiteralPath $PidPath -Force
Write-Host "Stopped Codex ModelX proxy PID $targetPid"
