# =============================================================================
# install.ps1 — Install the catalog-input-files skill into one or more agent
# skill homes (OpenAI Codex, Claude Code, DeepSeek Harness, shared ~/.agents).
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1            # all homes
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target codex
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target claude
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target dsh,agents
#
# Targets: codex | claude | dsh | agents | all
# =============================================================================
param(
  [ValidateSet("codex", "claude", "dsh", "agents", "all")]
  [string]$Target = "all"
)

$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $repositoryRoot "skills\catalog-input-files"
if (-not (Test-Path -LiteralPath (Join-Path $source "SKILL.md"))) {
  Write-Error "Skill source not found at $source. Run this script from the repository root."
  exit 1
}

function Install-Skill($destinationRoot, [string]$label) {
  $destination = Join-Path $destinationRoot "catalog-input-files"
  New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
  if (Test-Path -LiteralPath $destination) {
    Remove-Item -LiteralPath $destination -Recurse -Force
  }
  Copy-Item -LiteralPath $source -Destination $destination -Recurse
  Write-Host "installed -> $destination ($label)"
}

$targets = @()
if ($Target -eq "all") {
  $targets = @("codex", "claude", "dsh", "agents")
} else {
  $targets = $Target -split ","
}

$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$claudeHome = Join-Path $HOME ".claude"
$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $HOME ".dsh" }
$agentsHome = if ($env:DSH_AGENTS_HOME) { $env:DSH_AGENTS_HOME } else { Join-Path $HOME ".agents" }

foreach ($target in $targets) {
  switch ($target) {
    "codex"  { Install-Skill (Join-Path $codexHome "skills") "OpenAI Codex" }
    "claude" { Install-Skill (Join-Path $claudeHome "skills") "Claude Code" }
    "dsh"    { Install-Skill (Join-Path $dshHome "skills") "DeepSeek Harness" }
    "agents" { Install-Skill (Join-Path $agentsHome "skills") "shared ~/.agents" }
  }
}

Write-Host ""
Write-Host "Done. Start a new agent session so the skill list reloads."
