[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compose = @(
    '-f', (Join-Path $root 'infra/platform/compose.e2e.yaml')
)

$env:E2E_BASE_URL = 'http://localhost:18080'
$e2eTemp = Join-Path $root '.tmp/e2e-playwright'
New-Item -ItemType Directory -Force -Path $e2eTemp | Out-Null
$env:TEMP = $e2eTemp
$env:TMP = $e2eTemp
$env:E2E_OUTPUT_DIR = Join-Path $root '.tmp/e2e-artifacts'
New-Item -ItemType Directory -Force -Path $env:E2E_OUTPUT_DIR | Out-Null
$systemChrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
if (Test-Path -LiteralPath $systemChrome) {
    $env:E2E_BROWSER_CHANNEL = 'chrome'
}

try {
    $upArgs = @('compose') + $compose + @('up', '-d')
    if (-not $SkipBuild) { $upArgs += '--build' }
    $upArgs += '--wait'
    & docker @upArgs
    if ($LASTEXITCODE -ne 0) { throw "E2E Compose 启动失败：$LASTEXITCODE" }

    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$env:E2E_BASE_URL/evaluation/" -TimeoutSec 5
            if ($response.StatusCode -eq 200) { break }
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $response -or $response.StatusCode -ne 200) { throw 'E2E 网关未在 3 分钟内就绪' }

    & uv run python tests/platform_navigation_smoke.py
    if ($LASTEXITCODE -ne 0) { throw "平台导航验收失败：$LASTEXITCODE" }
    & uv run python tests/evaluation_async_run_smoke.py
    if ($LASTEXITCODE -ne 0) { throw "异步评测界面验收失败：$LASTEXITCODE" }
} finally {
    & docker @(@('compose') + $compose + @('down', '-v', '--remove-orphans'))
}
