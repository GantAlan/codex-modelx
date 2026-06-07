param(
    [switch]$RepairCodexConfig
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillRoot = Split-Path -Parent $ScriptDir
$ConfigPath = Join-Path $SkillRoot "assets\config\modelx.config.json"
$ToolsConfigPath = Join-Path $SkillRoot "assets\config\tools.common.json"
$ProxyScript = Join-Path $ScriptDir "proxy.py"
$LogDir = Join-Path $SkillRoot "logs"
$StateDir = Join-Path $SkillRoot "state"
$PidPath = Join-Path $StateDir "proxy.pid"
$StatePath = Join-Path $StateDir "proxy-state.json"
$ProxyLog = Join-Path $LogDir "proxy.log"
$StdoutLog = Join-Path $LogDir "proxy.stdout.log"
$StderrLog = Join-Path $LogDir "proxy.stderr.log"
$CodexConfigPath = Join-Path $env:USERPROFILE ".codex\config.toml"
$CodexBackupDir = Join-Path $env:USERPROFILE ".codex\backups"

New-Item -ItemType Directory -Force -Path $LogDir, $StateDir | Out-Null

function Test-ProcessAlive {
    param([int]$ProcessId)
    try { $null = Get-Process -Id $ProcessId -ErrorAction Stop; return $true } catch { return $false }
}

function Test-PortOpen {
    param([string]$HostName, [int]$Port)
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $success = $iar.AsyncWaitHandle.WaitOne(300, $false)
        if (-not $success) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch { return $false } finally { if ($client) { $client.Close() } }
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        return [string]$cim.CommandLine
    } catch {
        return ""
    }
}

function Test-IsModelXProxyProcess {
    param([int]$ProcessId)
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if (-not $commandLine) { return $false }
    return (($commandLine -like "*codex-modelx*") -and ($commandLine -like "*proxy.py*"))
}

function Stop-ExistingModelXProxy {
    param([int]$ProcessId, [string]$Reason)
    if (-not (Test-IsModelXProxyProcess -ProcessId $ProcessId)) {
        $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
        throw "Refusing to stop PID $ProcessId because it does not look like Codex ModelX proxy. CommandLine: $commandLine"
    }
    Write-Host "Stopping stale Codex ModelX proxy PID $ProcessId ($Reason)..."
    Stop-Process -Id $ProcessId -Force
    Start-Sleep -Milliseconds 500
}


function Set-OrAppendModelXLine {
    param(
        [string]$Section,
        [string]$Key,
        [string]$Line
    )
    $pattern = "(?m)^\s*" + [regex]::Escape($Key) + "\s*=.*$"
    if ($Section -match $pattern) {
        return [regex]::Replace($Section, $pattern, $Line, 1)
    }
    if (-not $Section.EndsWith("`n")) { $Section += "`n" }
    return $Section + $Line + "`n"
}

function Ensure-CodexCustomProviderRoute {
    param([string]$ExpectedBaseUrl)

    if (-not (Test-Path -LiteralPath $CodexConfigPath)) { return }

    $text = Get-Content -LiteralPath $CodexConfigPath -Raw
    $originalText = $text
    $sectionPattern = '(?ms)(\[model_providers\.custom\]\s*)(.*?)(?=\r?\n\[|\z)'
    $match = [regex]::Match($text, $sectionPattern)

    if ($match.Success) {
        $header = $match.Groups[1].Value
        $section = $match.Groups[2].Value
        $section = Set-OrAppendModelXLine -Section $section -Key "name" -Line 'name = "custom"'
        $section = Set-OrAppendModelXLine -Section $section -Key "base_url" -Line "base_url = `"$ExpectedBaseUrl`""
        $section = Set-OrAppendModelXLine -Section $section -Key "wire_api" -Line 'wire_api = "responses"'
        $section = Set-OrAppendModelXLine -Section $section -Key "requires_openai_auth" -Line 'requires_openai_auth = true'
        $section = Set-OrAppendModelXLine -Section $section -Key "experimental_bearer_token" -Line 'experimental_bearer_token = "dummy-key"'
        $text = $text.Substring(0, $match.Index) + $header + $section + $text.Substring($match.Index + $match.Length)
    } else {
        if (-not $text.EndsWith("`n")) { $text += "`n" }
        $text += @"

[model_providers.custom]
name = "custom"
base_url = "$ExpectedBaseUrl"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "dummy-key"
"@
    }

    if ($text -ne $originalText) {
        New-Item -ItemType Directory -Force -Path $CodexBackupDir | Out-Null
        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backupPath = Join-Path $CodexBackupDir "config.toml.codex-modelx-start-repair-$timestamp.bak"
        Copy-Item -LiteralPath $CodexConfigPath -Destination $backupPath -Force
        [System.IO.File]::WriteAllText($CodexConfigPath, $text, [System.Text.UTF8Encoding]::new($false))
        Write-Host "Repaired Codex custom provider route to $ExpectedBaseUrl"
        Write-Host "Backed up config.toml to: $backupPath"
    }
}

function Test-CodexCustomProviderRoute {
    param([string]$ExpectedBaseUrl)

    if (-not (Test-Path -LiteralPath $CodexConfigPath)) {
        Write-Warning "Codex config not found: $CodexConfigPath"
        return
    }

    $text = Get-Content -LiteralPath $CodexConfigPath -Raw
    $providerMatch = [regex]::Match($text, '(?m)^\s*model_provider\s*=\s*"([^"]*)"')
    $provider = if ($providerMatch.Success) { $providerMatch.Groups[1].Value } else { "<not set>" }
    $sectionMatch = [regex]::Match($text, '(?ms)(\[model_providers\.custom\]\s*)(.*?)(?=\r?\n\[|\z)')
    $baseUrl = "<missing custom provider>"
    if ($sectionMatch.Success) {
        $baseMatch = [regex]::Match($sectionMatch.Groups[2].Value, '(?m)^\s*base_url\s*=\s*"([^"]*)"')
        if ($baseMatch.Success) { $baseUrl = $baseMatch.Groups[1].Value }
    }

    if ($provider -ne "custom" -or $baseUrl -ne $ExpectedBaseUrl) {
        Write-Warning "Codex config is not currently routed to Codex ModelX. model_provider=$provider custom.base_url=$baseUrl expected=$ExpectedBaseUrl"
        Write-Warning "Not modifying config.toml. To repair intentionally, run: .\scripts\start_proxy.ps1 -RepairCodexConfig"
    }
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @{ File = $python.Source; Prefix = @("-u") } }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @{ File = $py.Source; Prefix = @("-3", "-u") } }
    throw "Neither python nor py was found on PATH. Please install Python 3."
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Config not found: $ConfigPath. Run: python `"$ScriptDir\configure.py`""
}
if (-not (Test-Path -LiteralPath $ProxyScript)) { throw "Proxy script not found: $ProxyScript" }

$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$hostName = if ($config.proxy.host) { [string]$config.proxy.host } else { "127.0.0.1" }
$port = if ($config.proxy.port) { [int]$config.proxy.port } else { 17891 }
$health = "http://${hostName}:$port/__health"
$expectedBaseUrl = "http://${hostName}:$port/v1"
if ($RepairCodexConfig) {
    Ensure-CodexCustomProviderRoute -ExpectedBaseUrl $expectedBaseUrl
} else {
    Test-CodexCustomProviderRoute -ExpectedBaseUrl $expectedBaseUrl
}

if (Test-Path -LiteralPath $PidPath) {
    $pidText = (Get-Content -LiteralPath $PidPath -Raw).Trim()
    if ($pidText -match '^\d+$') {
        $oldPid = [int]$pidText
        if (Test-ProcessAlive -ProcessId $oldPid) {
            try {
                Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 3 | Out-Null
                Write-Host "Codex ModelX proxy already running: PID $oldPid at http://${hostName}:$port/v1"
                exit 0
            } catch {
                Stop-ExistingModelXProxy -ProcessId $oldPid -Reason "PID file exists but health check failed"
                Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

if (Test-PortOpen -HostName $hostName -Port $port) {
    $owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
    if ($owner -and (Test-IsModelXProxyProcess -ProcessId ([int]$owner))) {
        Stop-ExistingModelXProxy -ProcessId ([int]$owner) -Reason "port $port was occupied by an old ModelX proxy without a healthy PID state"
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    } else {
        throw "Port $port is already in use. Edit $ConfigPath and choose another proxy.port, then update the Codex config fragment."
    }
}

$py = Get-PythonCommand
$args = @() + $py.Prefix + @($ProxyScript, "--config", $ConfigPath, "--tools-config", $ToolsConfigPath, "--log", $ProxyLog)
$process = Start-Process -FilePath $py.File -ArgumentList $args -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
Set-Content -LiteralPath $PidPath -Value ([string]$process.Id) -Encoding ASCII
$healthy = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 700
    try {
        Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2 | Out-Null
        $healthy = $true
        break
    } catch {}
}
if (-not $healthy) {
    throw "Proxy did not answer health check. See $StdoutLog and $StderrLog"
}

$state = [ordered]@{
    timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    pid = $process.Id
    base_url = "http://${hostName}:$port/v1"
    config = $ConfigPath
    proxy_log = $ProxyLog
    stdout_log = $StdoutLog
    stderr_log = $StderrLog
}
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatePath -Encoding UTF8
Write-Host "Started Codex ModelX proxy: PID $($process.Id) at http://${hostName}:$port/v1"
Write-Host "Log: $ProxyLog"
