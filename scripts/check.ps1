[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipE2E
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$services = @(
    'services/knowledge-api',
    'services/agent-api',
    'services/evaluation-api'
)

function Invoke-Checked {
    param([string]$Label, [scriptblock]$Command)
    Write-Host "`n== $Label ==" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Label 失败：$LASTEXITCODE" }
}

Push-Location $root
try {
    $taskTemp = Join-Path $root '.tmp'
    New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp

    if (-not $SkipInstall) {
        Invoke-Checked 'Python workspace 同步' { uv sync --all-packages --all-extras }
        Invoke-Checked 'Node workspace 同步' { npm ci }
    }

    foreach ($service in $services) {
        Push-Location (Join-Path $root $service)
        try {
            Invoke-Checked "$service 测试" { uv run pytest }
        } finally {
            Pop-Location
        }
    }
    Invoke-Checked '前端生产构建' { npm run build }
    Invoke-Checked '前端单元测试' { npm run test:web }
    Invoke-Checked 'Compose 配置校验' { docker compose -f infra/platform/compose.yaml config --quiet }
    Invoke-Checked 'E2E Compose 配置校验' { docker compose -f infra/platform/compose.e2e.yaml config --quiet }
    if (-not $SkipE2E) {
        Invoke-Checked 'Playwright 验收测试' { & (Join-Path $root 'scripts/e2e.ps1') }
    }

    Write-Host "`n全部单仓校验通过。" -ForegroundColor Green
} finally {
    Pop-Location
}
