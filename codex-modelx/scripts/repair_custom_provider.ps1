param(
    [switch]$SetTopLevelCustom
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillRoot = Split-Path -Parent $ScriptDir
$ConfigPath = Join-Path $env:USERPROFILE ".codex\config.toml"
$BackupDir = Join-Path $env:USERPROFILE ".codex\backups"
$StateDir = Join-Path $SkillRoot "state"
$FragmentPath = Join-Path $StateDir "codex-config-fragment-router-custom.toml"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Codex config not found: $ConfigPath"
}

New-Item -ItemType Directory -Force -Path $BackupDir, $StateDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupPath = Join-Path $BackupDir "config.toml.codex-modelx-repair-$timestamp.bak"
Copy-Item -LiteralPath $ConfigPath -Destination $BackupPath -Force

$text = Get-Content -LiteralPath $ConfigPath -Raw

function Set-OrAppendLine {
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

if ($SetTopLevelCustom) {
    if ($text -match '(?m)^\s*model_provider\s*=') {
        $text = [regex]::Replace($text, '(?m)^\s*model_provider\s*=\s*"[^"]*"', 'model_provider = "custom"', 1)
    } else {
        $text = "model_provider = `"custom`"`n" + $text
    }
} else {
    $currentProvider = [regex]::Match($text, '(?m)^\s*model_provider\s*=\s*"([^"]+)"')
    if ($currentProvider.Success -and $currentProvider.Groups[1].Value -ne "custom") {
        Write-Warning "Top-level model_provider is '$($currentProvider.Groups[1].Value)'. Not changing it. Use -SetTopLevelCustom only if you intentionally want Desktop to use the custom provider."
    }
}

$sectionPattern = '(?ms)(\[model_providers\.custom\]\s*)(.*?)(?=\n\[|\z)'
$match = [regex]::Match($text, $sectionPattern)
if ($match.Success) {
    $header = $match.Groups[1].Value
    $section = $match.Groups[2].Value
    $section = Set-OrAppendLine -Section $section -Key "name" -Line 'name = "custom"'
    $section = Set-OrAppendLine -Section $section -Key "base_url" -Line 'base_url = "http://127.0.0.1:17891/v1"'
    $section = Set-OrAppendLine -Section $section -Key "wire_api" -Line 'wire_api = "responses"'
    $section = Set-OrAppendLine -Section $section -Key "requires_openai_auth" -Line 'requires_openai_auth = true'
    $section = Set-OrAppendLine -Section $section -Key "experimental_bearer_token" -Line 'experimental_bearer_token = "dummy-key"'
    $text = $text.Substring(0, $match.Index) + $header + $section + $text.Substring($match.Index + $match.Length)
} else {
    if (-not $text.EndsWith("`n")) { $text += "`n" }
    $text += @"

[model_providers.custom]
name = "custom"
base_url = "http://127.0.0.1:17891/v1"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "dummy-key"
"@
}

Set-Content -LiteralPath $ConfigPath -Value $text -Encoding UTF8

$fragment = @"
# Recommended Codex ModelX smart-router config.
# Keep provider name custom to avoid creating a new provider identity/session bucket.
model_provider = "custom"

[model_providers.custom]
name = "custom"
base_url = "http://127.0.0.1:17891/v1"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "dummy-key"
"@
$fragment | Set-Content -LiteralPath $FragmentPath -Encoding UTF8

Write-Host "Backed up config.toml to: $BackupPath"
Write-Host "Repaired [model_providers.custom] to use: http://127.0.0.1:17891/v1"
Write-Host "Provider name remains custom. Real upstream URL/API key stay in the Skill config."
Write-Host "Next: run scripts\start_proxy.ps1, then fully restart Codex Desktop if the model picker is stale."
