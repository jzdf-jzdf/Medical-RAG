<#
.SYNOPSIS
    安全关闭 Medical-RAG 平台在本机启动的开发服务进程。

.DESCRIPTION
    只关闭「属于 F:\Medical-RAG 项目」的服务进程（uvicorn / vite / esbuild）。
    采用多重归属确认 + 白名单保护，避免误杀编辑器、Agent 运行时、代理与其他项目。

    归属判定分三档：
      [确认]   自身命令行含本项目路径                     -> 默认关闭
      [确认]   自身命令行不含路径，但子进程含本项目路径   -> 默认关闭（如 vite + esbuild）
      [未确认] 占用了本项目端口且无法证明归属             -> 仅告警；需 -Force 才关闭

    另外，已确认进程的整棵子树会被 /T 一并终止，不会重复出现在候选里。

.PARAMETER Root
    项目根目录，默认 F:\Medical-RAG。

.PARAMETER DryRun
    只列出将要关闭的进程，不实际执行。

.PARAMETER Force
    连「未确认」一档也一起关闭（谨慎使用）。

.PARAMETER IncludeDocker
    额外执行 docker compose down 停止部署栈。

.EXAMPLE
    powershell -NoProfile -File .\stop-medical-rag.ps1 -DryRun
    预览将要关闭的进程。

.EXAMPLE
    powershell -NoProfile -File .\stop-medical-rag.ps1
    关闭本项目全部服务进程。

.EXAMPLE
    powershell -NoProfile -File .\stop-medical-rag.ps1 -IncludeDocker
    同时停止 Docker 部署栈。

.NOTES
    本机只装了 Windows PowerShell 5.1，无 pwsh。请用 powershell 调用。
#>

[CmdletBinding()]
param(
    [string]$Root = 'F:\Medical-RAG',
    [switch]$DryRun,
    [switch]$Force,
    [switch]$IncludeDocker
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- 配置区

# 端口 -> 项目（用对象数组，不用 OrderedDictionary：
# PowerShell 5.1 下 [ordered]@{} 的整数键会被索引器当成「位置」而非「键」，取值恒为空）
$PortMap = @(
    [pscustomobject]@{ Port = 8001; Project = 'medical-knowledge-api' }
    [pscustomobject]@{ Port = 5173; Project = 'medical-knowledge-web' }
    [pscustomobject]@{ Port = 8002; Project = 'medical-agent-api' }
    [pscustomobject]@{ Port = 5174; Project = 'medical-agent-web' }
    [pscustomobject]@{ Port = 8003; Project = 'medical-evaluation-api' }
    [pscustomobject]@{ Port = 5175; Project = 'medical-evaluation-web' }
    [pscustomobject]@{ Port = 8080; Project = 'medical-platform-deploy' }
)

# 只考虑这些可执行文件，其余一律不碰
$TargetNames = @('node.exe', 'python.exe', 'pythonw.exe', 'py.exe', 'esbuild.exe', 'uvicorn.exe')

# 黑名单：即使命令行含项目路径也绝不关闭（编辑器 / WSL 转发 / 代理 / Agent 运行时）
$NeverKillNames = @(
    'Code.exe', 'Code - Insiders.exe', 'cursor.exe',
    'wslrelay.exe', 'wslservice.exe', 'wsl.exe',
    'clash-verge.exe', 'verge-mihomo.exe', 'clash.exe',
    'DeepSeek Harness.exe', 'explorer.exe'
)

# 黑名单：命令行匹配这些片段的属于 Agent / 工具运行时，不是项目服务
$NeverKillCmdPattern = 'cua_node|node_repl|dsh-subprocess-local|dsh-acl-skill|trusted-worker|kernel\.js|--eval|WindowsPowerShell|pwsh\.exe'

# ---------------------------------------------------------------- 建索引

$all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
$byId = @{}
$ppidIndex = @{}
$childIndex = @{}

foreach ($p in $all) {
    $id = [int]$p.ProcessId
    $pp = [int]$p.ParentProcessId
    $byId[$id] = $p
    $ppidIndex[$id] = $pp
    if (-not $childIndex.ContainsKey($pp)) {
        $childIndex[$pp] = New-Object System.Collections.Generic.List[int]
    }
    $childIndex[$pp].Add($id)
}

function Get-AncestorIds {
    param([int]$ProcessId, [hashtable]$Index, [int]$MaxDepth = 64)
    $chain = New-Object System.Collections.Generic.List[int]
    $cur = $ProcessId
    $guard = 0
    while ($guard -lt $MaxDepth) {
        $guard++
        if (-not $Index.ContainsKey($cur)) { break }
        $parent = [int]$Index[$cur]
        if ($parent -le 0 -or $parent -eq $cur) { break }
        $chain.Add($parent)
        $cur = $parent
    }
    return $chain
}

function Get-DescendantIds {
    param([int]$ProcessId, [hashtable]$ChildIndex, [int]$MaxDepth = 6)
    $result = New-Object System.Collections.Generic.List[int]
    $seen = New-Object System.Collections.Generic.HashSet[int]
    $queue = New-Object System.Collections.Generic.Queue[object]
    $queue.Enqueue(@($ProcessId, 0))
    while ($queue.Count -gt 0) {
        $item = $queue.Dequeue()
        $cur = [int]$item[0]
        $depth = [int]$item[1]
        if ($depth -ge $MaxDepth) { continue }
        if (-not $ChildIndex.ContainsKey($cur)) { continue }
        foreach ($c in $ChildIndex[$cur]) {
            if ($seen.Add($c)) {
                $result.Add($c)
                $queue.Enqueue(@($c, $depth + 1))
            }
        }
    }
    return $result
}

function Test-Alive {
    param([int]$ProcessId)
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

# ---------------------------------------------------------------- 自保：收集自身祖先

$selfId = $PID
$selfChain = New-Object System.Collections.Generic.HashSet[int]
[void]$selfChain.Add($selfId)
foreach ($a in (Get-AncestorIds -ProcessId $selfId -Index $ppidIndex)) { [void]$selfChain.Add($a) }

Write-Host ''
Write-Host '===== Medical-RAG 服务进程清理 =====' -ForegroundColor Cyan
Write-Host ("项目根目录 : {0}" -f $Root)
Write-Host ("模式       : {0}" -f $(if ($DryRun) { '预览 (DryRun)' } else { '执行' }))
Write-Host ("自身进程链 : {0}（保护，绝不关闭）" -f (($selfChain | Sort-Object) -join ', '))

# ---------------------------------------------------------------- 第一档：命令行直接含项目路径

$direct = @()
foreach ($p in $all) {
    if (-not $p.CommandLine) { continue }
    if ($TargetNames -notcontains $p.Name) { continue }
    if ($NeverKillNames -contains $p.Name) { continue }
    if ($selfChain.Contains([int]$p.ProcessId)) { continue }
    if ($p.CommandLine -match $NeverKillCmdPattern) { continue }
    if ($p.CommandLine -notmatch [regex]::Escape($Root)) { continue }
    $direct += $p
}

$directIds = @{}
foreach ($p in $direct) { $directIds[[int]$p.ProcessId] = $true }

# 取「根」：祖先也在名单里的，会被父进程的 /T 覆盖，不作为独立根
$roots = New-Object System.Collections.Generic.List[object]
foreach ($p in $direct) {
    $coveredByParent = $false
    foreach ($a in (Get-AncestorIds -ProcessId ([int]$p.ProcessId) -Index $ppidIndex)) {
        if ($directIds.ContainsKey($a)) { $coveredByParent = $true; break }
    }
    if (-not $coveredByParent) { $roots.Add($p) }
}

# 这些根的整棵子树都会被 /T 覆盖
$covered = New-Object System.Collections.Generic.HashSet[int]
foreach ($r in $roots) {
    foreach ($d in (Get-DescendantIds -ProcessId ([int]$r.ProcessId) -ChildIndex $childIndex)) {
        [void]$covered.Add($d)
    }
}

# ---------------------------------------------------------------- 第二档：端口占用者

$promoted = New-Object System.Collections.Generic.List[object]   # 靠子进程证明归属 -> 默认关闭
$unconfirmed = New-Object System.Collections.Generic.List[object] # 无法证明 -> 需 -Force
$protected = New-Object System.Collections.Generic.List[object]   # 白名单 -> 永不关闭

$rootIds = @{}
foreach ($r in $roots) { $rootIds[[int]$r.ProcessId] = $true }

foreach ($entry in $PortMap) {
    $port = $entry.Port
    $projName = $entry.Project
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($cn in $conns) {
        $targetId = [int]$cn.OwningProcess
        if ($rootIds.ContainsKey($targetId)) { continue }      # 已是根
        if ($covered.Contains($targetId)) { continue }          # 会被父进程树收掉
        if ($selfChain.Contains($targetId)) { continue }        # 自身
        $proc = $byId[$targetId]
        if (-not $proc) { continue }

        if ($NeverKillNames -contains $proc.Name) {
            $protected.Add([pscustomobject]@{ PID = $targetId; Port = $port; Project = $projName; Name = $proc.Name; Command = $proc.CommandLine })
            continue
        }
        if ($TargetNames -notcontains $proc.Name) { continue }

        # 关键启发式：自身命令行没路径，但子进程有 -> 可证明归属
        # 例：vite 以相对路径启动时 cmdline 不含项目路径，但它拉起的 esbuild 含完整路径
        $proofChild = $null
        foreach ($d in (Get-DescendantIds -ProcessId $targetId -ChildIndex $childIndex)) {
            $dp = $byId[$d]
            if ($dp -and $dp.CommandLine -and $dp.CommandLine -match [regex]::Escape($Root)) { $proofChild = $dp; break }
        }

        $row = [pscustomobject]@{ PID = $targetId; Port = $port; Project = $projName; Name = $proc.Name; Command = $proc.CommandLine }
        if ($proofChild) {
            $row | Add-Member -NotePropertyName ProofChild -NotePropertyValue ("PID {0} {1}" -f $proofChild.ProcessId, $proofChild.Name)
            $promoted.Add($row)
        } else {
            $unconfirmed.Add($row)
        }
    }
}

# ---------------------------------------------------------------- 汇报

Write-Host ''
Write-Host '----- [确认] 属于本项目的服务进程（默认关闭） -----' -ForegroundColor Yellow
if ($roots.Count -eq 0 -and $promoted.Count -eq 0) {
    Write-Host '  无'
} else {
    foreach ($r in $roots) {
        Write-Host ("  根 PID {0,-7} {1,-12} {2}" -f $r.ProcessId, $r.Name, $r.CommandLine)
    }
    foreach ($r in $promoted) {
        Write-Host ("  端口 {0,-6} ({1})  PID {2,-7} {3}" -f $r.Port, $r.Project, $r.PID, $r.Name) -ForegroundColor Green
        Write-Host ("      归属证据: 子进程 {0} 命令行含项目路径" -f $r.ProofChild) -ForegroundColor DarkGray
        Write-Host ("      {0}" -f $r.Command) -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '----- [未确认] 占用本项目端口但无法证明归属（默认不动） -----' -ForegroundColor Yellow
if ($unconfirmed.Count -eq 0) {
    Write-Host '  无'
} else {
    foreach ($u in $unconfirmed) {
        Write-Host ("  端口 {0,-6} ({1})  PID {2,-7} {3}" -f $u.Port, $u.Project, $u.PID, $u.Name)
        Write-Host ("      {0}" -f $u.Command)
    }
    Write-Host '  提示：可能是 WSL 转发或其他项目。确认属实后可用 -Force 关闭。' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '----- [保护] 占用本项目端口但按白名单永不关闭 -----' -ForegroundColor Yellow
if ($protected.Count -eq 0) {
    Write-Host '  无'
} else {
    foreach ($g in $protected) {
        Write-Host ("  端口 {0,-6} ({1})  PID {2,-7} {3}" -f $g.Port, $g.Project, $g.PID, $g.Name)
    }
    Write-Host '  这类通常是 WSL2 端口转发（wslrelay），真正的监听者在 WSL 内部。' -ForegroundColor DarkGray
    Write-Host '  在 WSL 内排查:  wsl -e bash -lc "ss -ltnp | grep :5173"' -ForegroundColor DarkGray
    Write-Host '  在 WSL 内关闭:  在 WSL 里 kill 对应 PID（见文档第 2.6 / 3.6 节）' -ForegroundColor DarkGray
}

if ($DryRun) {
    Write-Host ''
    Write-Host '[DryRun] 未执行任何关闭操作。' -ForegroundColor Cyan
    if ($IncludeDocker) { Write-Host '[DryRun] 将执行: docker compose down' -ForegroundColor Cyan }
    return
}

# ---------------------------------------------------------------- 执行关闭

$targets = New-Object System.Collections.Generic.List[object]
foreach ($r in $roots) { $targets.Add([pscustomobject]@{ PID = [int]$r.ProcessId; Desc = '根进程' }) }
foreach ($r in $promoted) { $targets.Add([pscustomobject]@{ PID = [int]$r.PID; Desc = ("端口 {0} 归属已证明" -f $r.Port) }) }

# 去重：若某目标的后代也是目标，交给 /T 统一收掉，避免重复下单
# 例：vite(相对路径启动) 与其子进程 esbuild 都命中时，只需关 vite
$targetIds = New-Object System.Collections.Generic.HashSet[int]
foreach ($t in $targets) { [void]$targetIds.Add($t.PID) }

$effective = New-Object System.Collections.Generic.List[object]
foreach ($t in $targets) {
    $coveredByTarget = $false
    foreach ($a in (Get-AncestorIds -ProcessId $t.PID -Index $ppidIndex)) {
        if ($targetIds.Contains($a)) { $coveredByTarget = $true; break }
    }
    if (-not $coveredByTarget) { $effective.Add($t) }
    else { Write-Host ("  (跳过 PID {0}：其祖先也是目标，将由祖先的进程树一并终止)" -f $t.PID) -ForegroundColor DarkGray }
}
$targets = $effective

$okCount = 0
$failCount = 0
foreach ($t in $targets) {
    Write-Host ("关闭进程树 PID {0}（{1}）..." -f $t.PID, $t.Desc)
    $out = taskkill /PID $t.PID /T /F 2>&1
    if ($LASTEXITCODE -eq 0) {
        $okCount++
        $out | Where-Object { $_ -match 'SUCCESS' } | ForEach-Object { Write-Host ("  " + $_.ToString().Trim()) -ForegroundColor Green }
    } else {
        $failCount++
        Write-Host ("  失败: {0}" -f (($out | Out-String).Trim())) -ForegroundColor Red
    }
}

if ($Force -and $unconfirmed.Count -gt 0) {
    foreach ($u in $unconfirmed) {
        Write-Host ("[-Force] 关闭未确认进程树 PID {0}（端口 {1}）..." -f $u.PID, $u.Port) -ForegroundColor Magenta
        taskkill /PID $u.PID /T /F 2>&1 | Where-Object { $_ -match 'SUCCESS' } | ForEach-Object { Write-Host ("  " + $_.ToString().Trim()) }
    }
}

if ($IncludeDocker) {
    $compose = Join-Path $Root 'infra\platform\compose.yaml'
    if (Test-Path $compose) {
        Write-Host '停止 Docker 部署栈 ...'
        docker compose -f $compose down 2>&1 | ForEach-Object { Write-Host ("  " + $_) }
    } else {
        Write-Host ("未找到 compose 文件: {0}" -f $compose) -ForegroundColor Red
    }
}

# ---------------------------------------------------------------- 复查

Start-Sleep -Seconds 2
Write-Host ''
Write-Host '===== 复查 =====' -ForegroundColor Cyan

Write-Host '目标进程:'
$stillAlive = @()
foreach ($t in $targets) { if (Test-Alive -ProcessId $t.PID) { $stillAlive += $t.PID } }
if ($stillAlive.Count -eq 0) {
    Write-Host ("  全部已终止（成功 {0} 个根，失败 {1} 个）" -f $okCount, $failCount) -ForegroundColor Green
} else {
    Write-Host ("  仍存活: {0}（可能需要管理员权限）" -f ($stillAlive -join ', ')) -ForegroundColor Red
}

Write-Host '项目端口:'
$anyBusy = $false
foreach ($entry in $PortMap) {
    $port = $entry.Port
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($c) {
        $anyBusy = $true
        $pr = Get-Process -Id $c[0].OwningProcess -ErrorAction SilentlyContinue
        Write-Host ("  {0,-6} {1,-28} 占用中 PID={2} ({3})" -f $port, $entry.Project, $c[0].OwningProcess, $pr.ProcessName) -ForegroundColor Yellow
    } else {
        Write-Host ("  {0,-6} {1,-28} 空闲" -f $port, $entry.Project) -ForegroundColor Green
    }
}

Write-Host ''
if (-not $anyBusy) {
    Write-Host '本项目所有端口已释放。' -ForegroundColor Green
} else {
    Write-Host '仍有端口被占用，请对照上方提示确认归属。' -ForegroundColor Yellow
}
