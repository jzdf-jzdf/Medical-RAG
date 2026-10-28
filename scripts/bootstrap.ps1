[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Push-Location $root
try {
    Write-Host '同步 Python workspace...' -ForegroundColor Cyan
    & uv sync --all-packages --all-extras
    if ($LASTEXITCODE -ne 0) { throw "uv sync 失败：$LASTEXITCODE" }

    Write-Host '同步 Node workspace...' -ForegroundColor Cyan
    & npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci 失败：$LASTEXITCODE" }

    Write-Host '单仓依赖初始化完成。' -ForegroundColor Green
} finally {
    Pop-Location
}
