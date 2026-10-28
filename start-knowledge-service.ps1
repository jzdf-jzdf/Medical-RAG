[CmdletBinding()]
param(
    [int]$ApiPort = 8001,
    [int]$WebPort = 5174,
    [string]$BindAddress = '127.0.0.1',
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$apiRoot = Join-Path $projectRoot 'services\knowledge-api'
$webRoot = Join-Path $projectRoot 'apps\knowledge-web'
$runtimeRoot = Join-Path $projectRoot '.runtime'
$logRoot = Join-Path $runtimeRoot 'logs'
$pidFile = Join-Path $runtimeRoot 'knowledge-service.pids.json'

function Wait-HttpReady {
    param(
        [Parameter(Mandatory)] [string]$Uri,
        [int]$TimeoutSeconds = 30
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $response
            }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "服务未在 $TimeoutSeconds 秒内就绪：$Uri"
}

function Test-KnowledgeApi {
    param([Parameter(Mandatory)] [string]$Uri)
    try {
        $health = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
        return $health.service -eq 'medical-knowledge-api'
    } catch {
        return $false
    }
}

function Test-KnowledgeWeb {
    param(
        [Parameter(Mandatory)] [string]$Uri,
        [int]$Attempts = 3
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
            # Use an ASCII marker so Windows PowerShell's script-file encoding
            # cannot turn a valid Chinese title into a false negative.
            if ($response.Content -match 'content="medical-knowledge-web"') {
                return $true
            }
        } catch {
            # The existing Vite process may still be warming up; retry below.
        }

        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds 400
        }
    }

    return $false
}

function Test-PortInUse {
    param([Parameter(Mandatory)] [int]$Port)
    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
}

if (-not (Test-Path -LiteralPath $apiRoot -PathType Container)) {
    throw "找不到后端目录：$apiRoot"
}
if (-not (Test-Path -LiteralPath $webRoot -PathType Container)) {
    throw "找不到前端目录：$webRoot"
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

$postgresService = Get-Service -Name 'postgresql-x64-17' -ErrorAction SilentlyContinue
if (-not $postgresService) {
    throw '未找到本机 PostgreSQL 17 服务 postgresql-x64-17。'
}
if ($postgresService.Status -ne 'Running') {
    Write-Host '正在启动 PostgreSQL 17...'
    try {
        Start-Service -Name $postgresService.Name
        $postgresService.WaitForStatus('Running', [TimeSpan]::FromSeconds(20))
    } catch {
        throw 'PostgreSQL 启动失败，请以管理员身份运行此脚本或先手动启动 postgresql-x64-17。'
    }
}
Write-Host '✓ PostgreSQL 17 已运行' -ForegroundColor Green

$pythonPath = Join-Path $apiRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "找不到后端虚拟环境：$pythonPath。请先在 services\knowledge-api 中运行 uv sync --extra dev。"
}

# Apply schema changes before starting or reusing the API. Alembic migrations are
# idempotent, so this also keeps an already-running local service database current.
Write-Host '正在检查数据库迁移...'
$previousLocation = Get-Location
try {
    Set-Location -LiteralPath $apiRoot
    & $pythonPath -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "数据库迁移失败，退出代码：$LASTEXITCODE"
    }
} finally {
    Set-Location -LiteralPath $previousLocation
}
Write-Host '✓ 数据库结构已更新' -ForegroundColor Green

$apiHealthUrl = "http://${BindAddress}:$ApiPort/health"
$apiReadyUrl = "http://${BindAddress}:$ApiPort/ready"
$webUrl = "http://${BindAddress}:$WebPort/knowledge/"
$processState = [ordered]@{
    started_at = (Get-Date).ToString('o')
    api = $null
    web = $null
}

if (Test-KnowledgeApi -Uri $apiHealthUrl) {
    Write-Host "✓ 知识服务后端已在运行：$apiHealthUrl" -ForegroundColor Green
    $processState.api = [ordered]@{ managed = $false; pid = $null; url = $apiHealthUrl }
} else {
    $apiOut = Join-Path $logRoot 'knowledge-api.log'
    $apiErr = Join-Path $logRoot 'knowledge-api.error.log'
    $apiProcess = Start-Process -FilePath $pythonPath `
        -ArgumentList @('-m', 'uvicorn', 'knowledge_api.main:app', '--host', $BindAddress, '--port', "$ApiPort", '--reload') `
        -WorkingDirectory $apiRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $apiOut `
        -RedirectStandardError $apiErr `
        -PassThru
    Wait-HttpReady -Uri $apiHealthUrl | Out-Null
    Write-Host "✓ 知识服务后端已启动：$apiHealthUrl" -ForegroundColor Green
    $processState.api = [ordered]@{ managed = $true; pid = $apiProcess.Id; url = $apiHealthUrl }
}

try {
    $ready = Invoke-RestMethod -Uri $apiReadyUrl -TimeoutSec 5
    if ($ready.status -ne 'ready') { throw 'unexpected readiness response' }
    Write-Host '✓ PostgreSQL 数据库连接正常' -ForegroundColor Green
} catch {
    throw "后端已启动，但数据库尚未就绪。请查看：$(Join-Path $logRoot 'knowledge-api.error.log')"
}

if (Test-KnowledgeWeb -Uri $webUrl) {
    Write-Host "✓ 知识服务前端已在运行：$webUrl" -ForegroundColor Green
    $processState.web = [ordered]@{ managed = $false; pid = $null; url = $webUrl }
} else {
    if (Test-PortInUse -Port $WebPort) {
        throw "端口 $WebPort 已被其他服务占用，未启动知识服务前端：$webUrl"
    }

    $nodePath = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
    $vitePath = Join-Path $webRoot 'node_modules\vite\bin\vite.js'
    if (-not $nodePath) {
        throw '找不到 Node.js，请先安装或配置 node.exe。'
    }
    if (-not (Test-Path -LiteralPath $vitePath -PathType Leaf)) {
        throw "前端依赖尚未安装。请先在 $webRoot 中运行 npm install。"
    }
    $webOut = Join-Path $logRoot 'knowledge-web.log'
    $webErr = Join-Path $logRoot 'knowledge-web.error.log'
    $webProcess = Start-Process -FilePath $nodePath `
        -ArgumentList @($vitePath, '--host', $BindAddress, '--port', "$WebPort", '--strictPort') `
        -WorkingDirectory $webRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $webOut `
        -RedirectStandardError $webErr `
        -PassThru
    Wait-HttpReady -Uri $webUrl | Out-Null
    if (-not (Test-KnowledgeWeb -Uri $webUrl)) {
        throw "端口 $WebPort 上启动的不是知识服务前端，请查看：$webErr"
    }
    Write-Host "✓ 知识服务前端已启动：$webUrl" -ForegroundColor Green
    $processState.web = [ordered]@{ managed = $true; pid = $webProcess.Id; url = $webUrl }
}

$processState | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding utf8

Write-Host ''
Write-Host "知识服务已就绪：$webUrl" -ForegroundColor Cyan
Write-Host "日志目录：$logRoot"
Write-Host "进程记录：$pidFile"

if ($OpenBrowser) {
    Start-Process $webUrl
}
