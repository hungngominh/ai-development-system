<#
.SYNOPSIS
  Build + run the ai-dev Telegram gateway in Docker with a correct, complete .env.

.DESCRIPTION
  docker-compose.yml mounts both ${CLAUDE_AUTH_DIR} and ${GH_CONFIG_DIR} as volume
  sources. If either is empty, `docker compose up` fails on an invalid mount. This
  script makes the config correct before running:
    - ensures .env exists (copies from .env.example if missing)
    - ensures CLAUDE_AUTH_DIR points at an existing folder
    - resolves GH_CONFIG_DIR (host GitHub CLI config) and creates a placeholder if
      gh isn't set up yet, so the mount is always valid
    - fills GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL from your global git config (or defaults)
    - then: docker compose up -d --build, shows status, and tails logs.

  Re-runnable: it only appends MISSING keys to .env; it never rewrites your bot tokens.

.PARAMETER NoFollow
  Don't tail logs after starting (just build + up + status).

.EXAMPLE
  ./run-gateway.ps1
  ./run-gateway.ps1 -NoFollow
#>
[CmdletBinding()]
param(
  [switch]$NoFollow
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot   # repo root (where docker-compose.yml + .env live)

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  WARN $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  ERR  $m" -ForegroundColor Red; exit 1 }

# Forward-slash a Windows path for Docker Desktop bind mounts.
function ToDockerPath([string]$p) { return ($p -replace '\\', '/') }

Write-Host "`n=== ai-dev gateway: docker run ===`n"

# 1. Docker running? (native exit codes don't throw in PowerShell — check $LASTEXITCODE)
$dockerOk = $false
try { docker info *> $null; $dockerOk = ($LASTEXITCODE -eq 0) } catch { $dockerOk = $false }
if (-not $dockerOk) { Die "Docker khong chay (hoac chua cai). Mo Docker Desktop roi chay lai." }
Ok "Docker dang chay"

# 2. .env exists?
if (-not (Test-Path ".env")) {
  if (Test-Path ".env.example") { Copy-Item ".env.example" ".env"; Warn ".env chua co -> da copy tu .env.example. Sua CLAUDE_AUTH_DIR roi chay lai." }
  else { Die ".env va .env.example deu khong co." }
}

# Read .env once; helper to test/append keys without touching existing lines.
$envLines = Get-Content ".env"
function HasKey([string]$k) { return [bool]($envLines | Where-Object { $_ -match "^\s*$([regex]::Escape($k))\s*=" }) }
function GetVal([string]$k) {
  $line = $envLines | Where-Object { $_ -match "^\s*$([regex]::Escape($k))\s*=" } | Select-Object -First 1
  if ($line) { return ($line -split '=', 2)[1].Trim() } else { return $null }
}
$appended = @()
function AppendKey([string]$k, [string]$v) {
  Add-Content ".env" "$k=$v"
  $script:envLines += "$k=$v"
  $script:appended += $k
}

# 3. CLAUDE_AUTH_DIR — required, must exist.
if (-not (HasKey "CLAUDE_AUTH_DIR")) {
  $guess = ToDockerPath (Join-Path $env:USERPROFILE ".claude")
  AppendKey "CLAUDE_AUTH_DIR" $guess
  Warn "CLAUDE_AUTH_DIR chua co -> dat mac dinh $guess"
}
$claudeDir = GetVal "CLAUDE_AUTH_DIR"
if (-not (Test-Path $claudeDir)) { Die "CLAUDE_AUTH_DIR='$claudeDir' khong ton tai. Chay 'claude' de dang nhap Claude Max truoc." }
Ok "CLAUDE_AUTH_DIR -> $claudeDir"

# 4. GH_CONFIG_DIR — required by the compose mount. Resolve host gh config or placeholder.
if (-not (HasKey "GH_CONFIG_DIR")) {
  $ghCandidates = @((Join-Path $env:APPDATA "GitHub CLI"), (Join-Path $env:USERPROFILE ".config/gh"))
  $ghDir = $null
  foreach ($c in $ghCandidates) { if (Test-Path (Join-Path $c "hosts.yml")) { $ghDir = $c; break } }
  if ($ghDir) {
    Ok "Phat hien GitHub CLI auth: $ghDir"
  } else {
    $ghDir = Join-Path $env:USERPROFILE ".config/gh"
    if (-not (Test-Path $ghDir)) { New-Item -ItemType Directory -Force -Path $ghDir | Out-Null }
    Warn "Chua co GitHub CLI auth -> dung placeholder $ghDir (luong tao PR se KHONG chay den khi ban 'gh auth login')"
  }
  AppendKey "GH_CONFIG_DIR" (ToDockerPath $ghDir)
}
$ghVal = GetVal "GH_CONFIG_DIR"
if (-not (Test-Path $ghVal)) { New-Item -ItemType Directory -Force -Path $ghVal | Out-Null }
Ok "GH_CONFIG_DIR -> $ghVal"

# 5. GIT_AUTHOR_* — fill from global git config or defaults (only used by the PR flow).
if (-not (HasKey "GIT_AUTHOR_NAME")) {
  $n = (git config --global user.name) 2>$null; if (-not $n) { $n = "ai-dev bot" }
  AppendKey "GIT_AUTHOR_NAME" $n
}
if (-not (HasKey "GIT_AUTHOR_EMAIL")) {
  $e = (git config --global user.email) 2>$null; if (-not $e) { $e = "ai-dev@example.com" }
  AppendKey "GIT_AUTHOR_EMAIL" $e
}

if ($appended.Count -gt 0) { Info ("Da them vao .env: " + ($appended -join ", ")) }

# 6. Build + run.
Write-Host ""
Info "docker compose up -d --build (lan dau build ~5-10 phut)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Die "docker compose up that bai (xem loi o tren)." }

Write-Host ""
Ok "Gateway dang chay."
docker compose ps

# Quick health check: is the daemon polling / any error in the first logs?
Write-Host ""
Info "Log gan day:"
docker compose logs --tail 25 gateway

if (-not $NoFollow) {
  Write-Host ""
  Info "Theo doi log (Ctrl+C de thoat, container van chay)..."
  docker compose logs -f gateway
}
