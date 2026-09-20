<#
.SYNOPSIS
    A24 合同智能审核系统 —— Docker 交付验收脚本（只用于交付验收，不属于生产业务代码）

.DESCRIPTION
    在一台装有 Docker 的机器上执行一次，尽可能自动完成「Docker 交付」的真机验证，
    并输出结构化 A–I 验收报告。

    严格边界（本脚本自我约束）：
      · 不修改任何业务代码 / Dockerfile / docker-compose.yml / README / .env.example
      · 不修改 Gold / 正式测试集 / 风险规则 / evidence_adjudicator / rule_engine / 分类口径
      · 不写任何文件到项目仓库（compose 与 .env 都只在临时目录里使用副本）
      · 不内置、不打印任何真实 API Key
      · 不执行 docker system prune / docker volume prune / docker image prune
      · 只清理本脚本自己创建的容器 / 卷 / 镜像 tag / tar / 临时目录
      · 不使用 mock / stub / 假响应冒充真实 E2E；无法验证的一律标 BLOCKED 或 MANUAL_REQUIRED

    真伪原则：Docker build 是真 build、load 是真 load、named volume 是真 volume、
    RAG 走容器内真实生产函数、LLM E2E 走真实 DeepSeek API。宁可输出 BLOCKED / FAIL /
    MANUAL_REQUIRED，也不制造全绿报告。

.PARAMETER DeepSeekKey
    用于真实 LLM E2E 的 DeepSeek API Key。**不会写盘、不会回显**。
    也可用环境变量 A24_ACCEPTANCE_DEEPSEEK_KEY 传入；两者都没有时，
    脚本会自动尝试从（仓库根或临时目录的）.env 读取；仍没有则相关项标 BLOCKED。

.PARAMETER SkipBuild
    跳过 build，直接使用已存在的 a24-api:acceptance / a24-web:acceptance。

.PARAMETER SkipSaveLoad
    跳过 docker save / load 复验环节。

.PARAMETER KeepResources
    结束后不清理容器 / 卷 / 镜像 tag / 临时目录（排障用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\docker_acceptance.ps1
.EXAMPLE
    $env:A24_ACCEPTANCE_DEEPSEEK_KEY = "sk-xxxx"
    powershell -ExecutionPolicy Bypass -File .\scripts\docker_acceptance.ps1 -TimeoutSec 1800
#>
[CmdletBinding()]
param(
    [string] $DeepSeekKey = $env:A24_ACCEPTANCE_DEEPSEEK_KEY,
    [int]    $TimeoutSec  = 900,
    [switch] $SkipBuild,
    [switch] $SkipSaveLoad,
    [switch] $KeepResources
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

# ═══════════════════════════════════════════════════════════════════════════
# 0. 常量与状态
# ═══════════════════════════════════════════════════════════════════════════
$RepoRoot      = Split-Path -Parent $PSScriptRoot          # scripts/ 的上一级 = 项目根
$Suffix        = (Get-Date -Format 'yyyyMMddHHmmss')
$ProjectName   = "a24acc$Suffix"                           # compose 项目名（隔离 named volume / 网络）
$ApiImage      = 'a24-api:acceptance'
$WebImage      = 'a24-web:acceptance'
$ApiImageSave  = 'a24-api:acceptance-saved'                # save→load 复验用的第二个 tag
$WebImageSave  = 'a24-web:acceptance-saved'
$TempDir       = Join-Path $env:TEMP "a24_acceptance_$Suffix"
$LogDir        = Join-Path $TempDir 'logs'
$TarDir        = Join-Path $TempDir 'tars'
$FixtureDir    = Join-Path $TempDir 'fixtures'
$ApiBase       = 'http://localhost:8080'
$WebBase       = 'http://localhost:5173'
$ApiContainer  = "a24-api-acc$Suffix"
$WebContainer  = "a24-web-acc$Suffix"
$ReportPath    = Join-Path $env:TEMP "A24_DOCKER_ACCEPTANCE_REPORT_$Suffix.md"

$script:R      = [ordered]@{}      # 结果登记表
$script:Notes  = New-Object System.Collections.ArrayList
$script:Token  = $null
$script:ContractId = $null

function Set-R {
    param([string]$Key, [string]$Status, [string]$Detail = '')
    $script:R[$Key] = [pscustomobject]@{ Status = $Status; Detail = $Detail }
    $color = switch ($Status) {
        'PASS'            { 'Green' }
        'FAIL'            { 'Red' }
        'BLOCKED'         { 'Yellow' }
        'MANUAL_REQUIRED' { 'Magenta' }
        'SKIP'            { 'DarkGray' }
        default           { 'Gray' }
    }
    Write-Host ("    [{0,-15}] {1}  {2}" -f $Status, $Key, $Detail) -ForegroundColor $color
}
function Add-Note { param([string]$T) ; [void]$script:Notes.Add($T) ; Write-Host "    · $T" -ForegroundColor DarkCyan }
function Head { param([string]$T) ; Write-Host '' ; Write-Host ("═" * 78) -ForegroundColor Cyan ; Write-Host "  $T" -ForegroundColor Cyan ; Write-Host ("═" * 78) -ForegroundColor Cyan }
function Sub  { param([string]$T) ; Write-Host '' ; Write-Host "-- $T" -ForegroundColor White }
function Log  { param([string]$T) ; Write-Host "    $T" -ForegroundColor Gray }

# ── curl 包装：跨 PowerShell 5.1 / 7 都可用，且能拿到 HTTP 状态码 ──────────
function Invoke-Curl {
    param(
        [string]   $Url,
        [string]   $Method = 'GET',
        [string]   $JsonFile,                 # 请求体（UTF-8 JSON 文件路径）
        [string[]] $Form,                     # multipart: @('file=C:\x\a.docx')
        [string]   $Token,
        [string]   $OutFile,                  # 下载到文件（二进制）
        [int]      $TimeoutSecLocal = 300
    )
    $bodyFile = Join-Path $TempDir ("resp_{0}.txt" -f ([guid]::NewGuid().ToString('N').Substring(0,8)))
    # 注意：不要用 $args（PowerShell 自动变量），故命名为 $curlArgs
    $curlArgs = @('-sS', '-o', $bodyFile, '-w', '%{http_code}', '--max-time', "$TimeoutSecLocal", '-X', $Method)
    if ($Token)    { $curlArgs += @('-H', "Authorization: Bearer $Token") }
    if ($JsonFile) { $curlArgs += @('-H', 'Content-Type: application/json; charset=utf-8', '--data-binary', "@$JsonFile") }
    if ($Form)     { foreach ($f in $Form) { $curlArgs += @('-F', $f) } }
    $curlArgs += $Url

    $statusTxt = & curl.exe @curlArgs 2>&1 | Select-Object -Last 1
    $status    = 0
    [void][int]::TryParse(($statusTxt -as [string]).Trim(), [ref]$status)

    $body = ''
    if (Test-Path $bodyFile) {
        if ($OutFile) {
            Move-Item -Force $bodyFile $OutFile
        } else {
            $body = Get-Content $bodyFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            Remove-Item -Force $bodyFile -ErrorAction SilentlyContinue
        }
    }
    return [pscustomobject]@{ Status = $status; Body = $body; StatusText = ($statusTxt -as [string]) }
}
function To-JsonFile {
    param([hashtable]$Obj, [string]$Name)
    $p = Join-Path $TempDir $Name
    $json = $Obj | ConvertTo-Json -Depth 12 -Compress
    [IO.File]::WriteAllText($p, $json, (New-Object Text.UTF8Encoding($false)))
    return $p
}
function Try-ParseJson { param([string]$T) ; if (-not $T) { return $null } ; try { return ($T | ConvertFrom-Json) } catch { return $null } }
function Docker-Exec { param([string]$Container, [string[]]$Cmd) ; return (& docker exec $Container @Cmd 2>&1 | Out-String) }

# ═══════════════════════════════════════════════════════════════════════════
# A. 环境检查
# ═══════════════════════════════════════════════════════════════════════════
Head 'A. 环境检查'

New-Item -ItemType Directory -Force -Path $TempDir, $LogDir, $TarDir, $FixtureDir | Out-Null

$blocked = $false

# A1 docker CLI
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Set-R 'A1.docker-cli' 'BLOCKED' '未找到 docker 命令；请在有 Docker Desktop / Engine 的机器上执行'
    $blocked = $true
} else {
    $dv = (& docker --version 2>&1 | Out-String).Trim()
    Set-R 'A1.docker-cli' 'PASS' $dv
}

# A2 compose
$composeOk = $false
if (-not $blocked) {
    $cv = (& docker compose version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $cv) { $composeOk = $true; Set-R 'A2.docker-compose' 'PASS' $cv }
    else { Set-R 'A2.docker-compose' 'BLOCKED' 'docker compose 不可用'; $blocked = $true }
}

# A3 daemon
if (-not $blocked) {
    $info = (& docker info --format '{{.ServerVersion}}|{{.OSType}}|{{.Architecture}}' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) { Set-R 'A3.daemon' 'PASS' $info }
    else { Set-R 'A3.daemon' 'BLOCKED' 'Docker daemon 未运行（请先启动 Docker Desktop）'; $blocked = $true }
}

# A4 必要文件
$needFiles = @('Dockerfile.api','Dockerfile.web','docker-compose.yml','.env.example','frontend\package-lock.json','backend\requirements.txt','backend\chroma_data\chroma.sqlite3')
$missing = @()
foreach ($f in $needFiles) { if (-not (Test-Path (Join-Path $RepoRoot $f))) { $missing += $f } }
if ($missing.Count -gt 0) {
    Set-R 'A4.project-files' 'BLOCKED' ("缺少必要文件: " + ($missing -join ', '))
    $blocked = $true
} else {
    Set-R 'A4.project-files' 'PASS' ("当前目录判定为项目根: " + $RepoRoot)
}

# A5 curl.exe（multipart 上传依赖）
if (Get-Command curl.exe -ErrorAction SilentlyContinue) { Set-R 'A5.curl' 'PASS' 'curl.exe 可用（用于 multipart 上传与二进制下载）' }
else { Set-R 'A5.curl' 'BLOCKED' '未找到 curl.exe（Windows 10+ 自带）'; $blocked = $true }

# A6 DeepSeek Key（不打印内容）
$keySource = ''
if ($DeepSeekKey) { $keySource = '参数/环境变量' }
else {
    $envCandidate = @((Join-Path $RepoRoot '.env'), (Join-Path $TempDir '.env'))
    foreach ($e in $envCandidate) {
        if (Test-Path $e) {
            $line = (Get-Content $e -Encoding UTF8 | Where-Object { $_ -match '^\s*DEEPSEEK_API_KEY\s*=' } | Select-Object -First 1)
            if ($line) {
                $v = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
                if ($v) { $DeepSeekKey = $v; $keySource = $e; break }
            }
        }
    }
}
$hasKey = [bool]$DeepSeekKey
if ($hasKey) {
    Set-R 'A6.deepseek-key' 'PASS' "已获得 Key（来源: $keySource；内容不落盘、不回显）"
} else {
    Set-R 'A6.deepseek-key' 'BLOCKED' '未提供 Key：依赖 LLM 的验收项将标 BLOCKED（Docker 基础设施仍会完整验证）'
}

if ($blocked) {
    Head '结果：BLOCKED —— 环境前置条件不满足，脚本停止（不会自行修复项目）'
    $script:R.GetEnumerator() | ForEach-Object { "    [{0,-15}] {1}  {2}" -f $_.Value.Status, $_.Key, $_.Value.Detail }
    if (-not $KeepResources) { Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue }
    exit 2
}

# ═══════════════════════════════════════════════════════════════════════════
# B. Docker build（真 build）
# ═══════════════════════════════════════════════════════════════════════════
Head 'B. Docker build'

Push-Location $RepoRoot
try {
    # ── 建库源：已随源码迁入仓库，构建不再需要任何仓库外 build context ──
    $srcResource = Join-Path $RepoRoot 'backend\ai\rag\resources\contract_templates_source.json'
    if (Test-Path $srcResource) {
        Set-R 'B0.rag-resource' 'PASS' '建库源在仓库内: backend\ai\rag\resources\contract_templates_source.json'
    } else {
        Set-R 'B0.rag-resource' 'BLOCKED' '缺少 backend\ai\rag\resources\contract_templates_source.json（contract_templates 将无法重建）'
    }

    if ($SkipBuild) {
        Set-R 'B1.api-build' 'SKIP' '按 -SkipBuild 跳过'
        Set-R 'B2.web-build' 'SKIP' '按 -SkipBuild 跳过'
    } else {
        # ── API ──
        Sub 'B1. 构建 a24-api:acceptance'
        $apiLog = Join-Path $LogDir 'build-api.log'
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $buildArgs = @('build','-f','Dockerfile.api','-t',$ApiImage)
        $buildArgs += '.'
        Log ("docker " + ($buildArgs -join ' '))
        & docker @buildArgs 2>&1 | Tee-Object -FilePath $apiLog | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        $apiBuildExit = $LASTEXITCODE
        $sw.Stop()
        $apiBuildSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        if ($apiBuildExit -eq 0) { Set-R 'B1.api-build' 'PASS' "耗时 ${apiBuildSec}s；日志: $apiLog" }
        else { Set-R 'B1.api-build' 'FAIL' "exit=$apiBuildExit；日志: $apiLog（脚本不会自动改 Dockerfile）" }

        # ── Web ──
        Sub 'B2. 构建 a24-web:acceptance'
        $webLog = Join-Path $LogDir 'build-web.log'
        $sw2 = [Diagnostics.Stopwatch]::StartNew()
        & docker build -f Dockerfile.web -t $WebImage . 2>&1 | Tee-Object -FilePath $webLog | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        $webBuildExit = $LASTEXITCODE
        $sw2.Stop()
        $webBuildSec = [math]::Round($sw2.Elapsed.TotalSeconds, 1)
        if ($webBuildExit -eq 0) { Set-R 'B2.web-build' 'PASS' "耗时 ${webBuildSec}s；日志: $webLog" }
        else { Set-R 'B2.web-build' 'FAIL' "exit=$webBuildExit；日志: $webLog" }
    }

    # ── 镜像大小 ──
    Sub 'B3. 镜像大小'
    $apiSizeTxt = (& docker images $ApiImage --format '{{.Size}}' 2>&1 | Out-String).Trim()
    $webSizeTxt = (& docker images $WebImage --format '{{.Size}}' 2>&1 | Out-String).Trim()
    Set-R 'B3.api-size' 'INFO' $apiSizeTxt
    Set-R 'B3.web-size' 'INFO' $webSizeTxt

    # ═══════════════════════════════════════════════════════════════════════
    # C. 镜像内容自检（真容器内检查）
    # ═══════════════════════════════════════════════════════════════════════
    Head 'C. 镜像内容自检'

    Sub 'C1. API 镜像：路径 / 依赖 / 模型 / 向量库 / 字体'
    $c1 = @'
import os, sqlite3, sys
backend = "/app/LLM-ContractAudit-WarnSystem/backend"
out = []
def chk(name, ok, extra=""):
    out.append(("PASS" if ok else "FAIL", name, extra))
chk("backend 目录", os.path.isdir(backend), backend)
chk("chroma_data 目录", os.path.isdir(os.path.join(backend, "chroma_data")))
chk("chroma.sqlite3", os.path.isfile(os.path.join(backend, "chroma_data", "chroma.sqlite3")))
chk("建库源 contract_templates_source.json", os.path.isfile(os.path.join(backend, "ai", "rag", "resources", "contract_templates_source.json")))
# collections
try:
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(backend,"chroma_data","chroma.sqlite3"), uri=True)
    names = sorted(r[0] for r in con.execute("SELECT name FROM collections"))
    chk("Chroma collections", True, str(names))
    for must in ("laws","standard_clauses","contract_templates"):
        chk("collection:"+must, must in names)
except Exception as e:
    chk("Chroma collections", False, repr(e))
# 依赖
for mod in ("fastapi","uvicorn","sqlalchemy","chromadb","sentence_transformers","rapidocr","onnxruntime","reportlab","pdfplumber","docx","PIL"):
    try:
        __import__(mod); chk("import "+mod, True)
    except Exception as e:
        chk("import "+mod, False, repr(e))
# HF 缓存里的模型
import glob
cands = glob.glob(os.path.join(os.environ.get("HF_HOME","/opt/hf-cache"), "hub", "models--*text2vec*"))
chk("HF cache 模型目录", len(cands) > 0, str(cands))
# 离线加载模型
try:
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer("shibing624/text2vec-base-chinese")
    v = m.encode(["offline selfcheck"])
    chk("离线加载 embedding", v.shape[-1] == 768, "dim=%d" % v.shape[-1])
except Exception as e:
    chk("离线加载 embedding", False, repr(e))
# OCR
try:
    from rapidocr import RapidOCR; RapidOCR(); chk("RapidOCR 初始化", True)
except Exception as e:
    chk("RapidOCR 初始化", False, repr(e))
# reportlab CJK
try:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light")); chk("reportlab CJK", True)
except Exception as e:
    chk("reportlab CJK", False, repr(e))
print("\n".join("%s|%s|%s" % t for t in out))
'@
    $c1Path = Join-Path $TempDir 'check_api.py'
    [IO.File]::WriteAllText($c1Path, $c1, (New-Object Text.UTF8Encoding($false)))
    $c1Out = (& docker run --rm -v "${c1Path}:/tmp/check_api.py:ro" --entrypoint python $ApiImage /tmp/check_api.py 2>&1 | Out-String)
    Write-Host $c1Out -ForegroundColor DarkGray
    $c1Lines = $c1Out -split "`r?`n" | Where-Object { $_ -match '^(PASS|FAIL)\|' }
    if ($c1Lines.Count -eq 0) {
        Set-R 'C1.image-selfcheck' 'FAIL' '镜像内自检脚本无输出（可能镜像不可用）'
    } else {
        $fails = $c1Lines | Where-Object { $_ -like 'FAIL|*' }
        if ($fails.Count -eq 0) { Set-R 'C1.image-selfcheck' 'PASS' ("全部通过（{0} 项）" -f $c1Lines.Count) }
        else { Set-R 'C1.image-selfcheck' 'FAIL' ("失败 {0} 项: {1}" -f $fails.Count, (($fails -replace '^FAIL\|','') -join '; ')) }
    }

    Sub 'C2. Web 镜像：静态产物'
    $c2 = (& docker run --rm --entrypoint sh $WebImage -c "test -f /site/index.html && echo OK && ls /site | head -20" 2>&1 | Out-String)
    if ($c2 -match 'OK') { Set-R 'C2.web-image' 'PASS' ($c2.Trim() -replace "`r?`n", ' / ') }
    else { Set-R 'C2.web-image' 'FAIL' "未找到 /site/index.html" }

    # ═══════════════════════════════════════════════════════════════════════
    # D. docker save / load（模拟评委拿到镜像）
    # ═══════════════════════════════════════════════════════════════════════
    Head 'D. docker save / load（模拟交付）'
    if ($SkipSaveLoad) {
        Set-R 'D.save' 'SKIP' '按 -SkipSaveLoad 跳过'
        Set-R 'D.load' 'SKIP' '按 -SkipSaveLoad 跳过'
    } else {
        $apiTar = Join-Path $TarDir 'a24-api.tar'
        $webTar = Join-Path $TarDir 'a24-web.tar'
        & docker tag $ApiImage $ApiImageSave 2>&1 | Out-Null
        & docker tag $WebImage $WebImageSave 2>&1 | Out-Null

        Sub 'D1. docker save'
        & docker save -o $apiTar $ApiImageSave 2>&1 | Out-Null
        $e1 = $LASTEXITCODE
        & docker save -o $webTar $WebImageSave 2>&1 | Out-Null
        $e2 = $LASTEXITCODE
        $apiTarMb = if (Test-Path $apiTar) { [math]::Round((Get-Item $apiTar).Length / 1MB, 1) } else { 0 }
        $webTarMb = if (Test-Path $webTar) { [math]::Round((Get-Item $webTar).Length / 1MB, 1) } else { 0 }
        if ($e1 -eq 0 -and $e2 -eq 0) { Set-R 'D.save' 'PASS' "api.tar=${apiTarMb} MB, web.tar=${webTarMb} MB, 总 $([math]::Round($apiTarMb+$webTarMb,1)) MB" }
        else { Set-R 'D.save' 'FAIL' "docker save 返回 $e1 / $e2" }

        Sub 'D2. 删除本地 tag 后 docker load（真实模拟评委）'
        & docker rmi $ApiImageSave $WebImageSave 2>&1 | Out-Null
        & docker load -i $apiTar 2>&1 | Out-Null ; $l1 = $LASTEXITCODE
        & docker load -i $webTar 2>&1 | Out-Null ; $l2 = $LASTEXITCODE
        $back1 = (& docker images $ApiImageSave --format '{{.ID}}' 2>&1 | Out-String).Trim()
        $back2 = (& docker images $WebImageSave --format '{{.ID}}' 2>&1 | Out-String).Trim()
        if ($l1 -eq 0 -and $l2 -eq 0 -and $back1 -and $back2) { Set-R 'D.load' 'PASS' "load 后镜像存在: $($back1.Substring(0,[Math]::Min(19,$back1.Length))) / $($back2.Substring(0,[Math]::Min(19,$back2.Length)))" }
        else { Set-R 'D.load' 'FAIL' "load 返回 $l1 / $l2" }
    }

    # ═══════════════════════════════════════════════════════════════════════
    # E. 全新 named volume + compose 启动
    # ═══════════════════════════════════════════════════════════════════════
    Head 'E. 全新 named volume + docker compose 启动'

    # 在临时目录里放一份 compose 副本（绝不改仓库里的文件）：
    #   · 镜像 tag → acceptance tag
    #   · container_name → 带后缀，避免与用户已有容器冲突
    #   · .env 也放临时目录，避免碰用户的 .env
    $composeSrc = Join-Path $RepoRoot 'docker-compose.yml'
    $composeDst = Join-Path $TempDir 'docker-compose.yml'
    $composeText = Get-Content $composeSrc -Raw -Encoding UTF8
    $composeText = $composeText -replace 'a24-api:1\.0', $ApiImage
    $composeText = $composeText -replace 'a24-web:1\.0', $WebImage
    $composeText = $composeText -replace 'container_name:\s*a24-api', "container_name: $ApiContainer"
    $composeText = $composeText -replace 'container_name:\s*a24-web', "container_name: $WebContainer"
    [IO.File]::WriteAllText($composeDst, $composeText, (New-Object Text.UTF8Encoding($false)))

    $secret = -join ((1..48) | ForEach-Object { 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'[(Get-Random -Max 62)] })
    $envLines = @(
        "# 本文件由验收脚本在临时目录生成，仅本次验收使用；不写入项目仓库。",
        "SECRET_KEY=$secret",
        "DEEPSEEK_API_KEY=$(if ($hasKey) { $DeepSeekKey } else { '' })",
        "CORS_ORIGINS=http://localhost:5173",
        "BOOTSTRAP_ADMIN_USERNAME="
    )
    [IO.File]::WriteAllLines((Join-Path $TempDir '.env'), $envLines, (New-Object Text.UTF8Encoding($false)))

    Push-Location $TempDir
    try {
        Sub 'E1. compose up（project 隔离，全新 named volume）'
        & docker compose -p $ProjectName up -d 2>&1 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        $upExit = $LASTEXITCODE
        if ($upExit -eq 0) { Set-R 'E1.compose-up' 'PASS' "project=$ProjectName" }
        else { Set-R 'E1.compose-up' 'FAIL' "docker compose up 返回 $upExit" }

        Sub 'E2. 容器状态'
        $ps = (& docker compose -p $ProjectName ps --format '{{.Name}}|{{.State}}|{{.Status}}' 2>&1 | Out-String).Trim()
        Write-Host $ps -ForegroundColor DarkGray
        Set-R 'E2.containers' $(if ($ps -match 'running') { 'PASS' } else { 'FAIL' }) ($ps -replace "`r?`n", ' / ')

        # 等 API
        Sub 'E3. 等待 API 就绪'
        $apiOk = $false
        for ($i = 0; $i -lt 60; $i++) {
            $r = Invoke-Curl -Url "$ApiBase/" -TimeoutSecLocal 5
            if ($r.Status -eq 200) { $apiOk = $true; break }
            Start-Sleep -Seconds 3
        }
        Set-R 'E3.api-up' $(if ($apiOk) { 'PASS' } else { 'FAIL' }) $(if ($apiOk) { 'GET / 返回 200' } else { '等待 180s 仍未就绪' })
    } finally { Pop-Location }

    # ═══════════════════════════════════════════════════════════════════════
    # F. Chroma：named volume 是否成功播种
    # ═══════════════════════════════════════════════════════════════════════
    Head 'F. Chroma named volume 播种检查'
    $volNames = (& docker volume ls --format '{{.Name}}' 2>&1 | Where-Object { $_ -like "*$ProjectName*" })
    Set-R 'F0.volumes' $(if ($volNames) { 'PASS' } else { 'FAIL' }) (($volNames -join ', '))
    $chromaVol = $volNames | Where-Object { $_ -like '*chroma*' } | Select-Object -First 1
    if ($chromaVol) {
        Sub "F1. 卷 $chromaVol 内容（应为镜像播种结果，非空）"
        $volList = (& docker run --rm -v "${chromaVol}:/v:ro" --entrypoint sh $ApiImage -c "ls -1 /v; echo BYTES; wc -c < /v/chroma.sqlite3" 2>&1 | Out-String)
        Write-Host $volList -ForegroundColor DarkGray
        [int64]$sqliteBytes = 0
        $m = [regex]::Match($volList, '(?s)BYTES\s*\r?\n\s*(\d+)')
        if ($m.Success) { try { $sqliteBytes = [int64]$m.Groups[1].Value } catch { $sqliteBytes = 0 } }
        if ($volList -match 'chroma\.sqlite3' -and $sqliteBytes -gt 0) {
            Set-R 'F1.chroma-seeded' 'PASS' "卷内 chroma.sqlite3 = $sqliteBytes 字节（>0 表示已从镜像播种；为空则卷遮蔽了镜像内容）"
        } else {
            Set-R 'F1.chroma-seeded' 'FAIL' "卷内未发现非空的 chroma.sqlite3（bytes=$sqliteBytes）—— 可能用了 bind mount 或卷播种失败"
        }
    } else { Set-R 'F1.chroma-seeded' 'FAIL' '未找到 chroma 卷' }

    Sub 'F2. 容器内实际 collection 数量'
    $f2code = @'
import sqlite3
con = sqlite3.connect("file:/app/LLM-ContractAudit-WarnSystem/backend/chroma_data/chroma.sqlite3?mode=ro", uri=True)
import chromadb
from chromadb.config import Settings
c = chromadb.PersistentClient(path="/app/LLM-ContractAudit-WarnSystem/backend/chroma_data",
                              settings=Settings(anonymized_telemetry=False))
for col in sorted(c.list_collections(), key=lambda x: x.name):
    try: n = c.get_collection(col.name).count()
    except Exception as e: n = "err:" + type(e).__name__
    print("%s=%s" % (col.name, n))
'@
    $f2Path = Join-Path $TempDir 'check_collections.py'
    [IO.File]::WriteAllText($f2Path, $f2code, (New-Object Text.UTF8Encoding($false)))
    [void](& docker cp $f2Path "${ApiContainer}:/tmp/check_collections.py" 2>&1)
    $f2 = Docker-Exec -Container $ApiContainer -Cmd @('python','/tmp/check_collections.py')
    Write-Host $f2 -ForegroundColor DarkGray
    $cnt = @{}
    foreach ($m in [regex]::Matches($f2, '(?m)^(\w+)=(\S+)\s*$')) { $cnt[$m.Groups[1].Value] = $m.Groups[2].Value }
    $okTpl = ($cnt['contract_templates'] -eq '363')
    $okLaw = ($cnt['laws'] -eq '161')
    $okStd = ($cnt['standard_clauses'] -eq '227')
    Set-R 'F2.contract_templates' $(if ($okTpl) { 'PASS' } else { 'FAIL' }) "实际=$(if($cnt['contract_templates']){$cnt['contract_templates']}else{'缺失'})，期望 363"
    Set-R 'F2.laws'               $(if ($okLaw) { 'PASS' } else { 'FAIL' }) "实际=$(if($cnt['laws']){$cnt['laws']}else{'缺失'})，期望 161"
    Set-R 'F2.standard_clauses'   $(if ($okStd) { 'PASS' } else { 'FAIL' }) "实际=$(if($cnt['standard_clauses']){$cnt['standard_clauses']}else{'缺失'})，期望 227"

    # ═══════════════════════════════════════════════════════════════════════
    # G. API health / warmup
    # ═══════════════════════════════════════════════════════════════════════
    Head 'G. API health / RAG warmup'
    Sub 'G1. 等待 warmup.rag == ready'
    $warm = $null ; $warmReady = $false
    for ($i = 0; $i -lt 80; $i++) {
        $r = Invoke-Curl -Url "$ApiBase/api/health" -TimeoutSecLocal 10
        if ($r.Status -eq 200) {
            $warm = Try-ParseJson $r.Body
            if ($warm -and $warm.warmup -and $warm.warmup.rag -eq 'ready') { $warmReady = $true; break }
            if ($warm -and $warm.warmup -and $warm.warmup.rag -eq 'failed') { break }
        }
        Start-Sleep -Seconds 3
    }
    $warmJson = if ($warm) { ($warm | ConvertTo-Json -Depth 6 -Compress) } else { '(无响应)' }
    Set-R 'G1.warmup.rag' $(if ($warmReady) { 'PASS' } else { 'FAIL' }) $warmJson

    # ═══════════════════════════════════════════════════════════════════════
    # H. RAG 分类链（容器内真实生产函数）
    # ═══════════════════════════════════════════════════════════════════════
    Head 'H. RAG 分类链（容器内生产代码路径）'
    $probeText = '建设工程施工合同 工程概况 合同价款 违约责任 争议解决 保密 验收标准'
    $hcode = @'
import os, sys, json
sys.path.insert(0, "/app/LLM-ContractAudit-WarnSystem/backend")
import ai.rag.vector_store as vs
q = "建设工程施工合同 工程概况 合同价款 违约责任 争议解决 保密 验收标准"
matches = vs.search_similar_templates(q, 3)
print("RETRIEVAL|%d|%s" % (len(matches), "|".join(str(m.get("type")) for m in matches)))
if os.getenv("DEEPSEEK_API_KEY"):
    try:
        from ai.classifier.rag_classifier import classify_by_rag
        r = classify_by_rag(q, top_k=3)
        print("CLASSIFY|%s|%s|%d|%s" % (r.get("method"), r.get("fallback"),
              len(r.get("top_matches") or []), r.get("contract_type")))
    except Exception as e:
        print("CLASSIFY|ERROR|%s" % repr(e))
else:
    print("CLASSIFY|BLOCKED|no key in container env")
'@
    $hPath = Join-Path $TempDir 'check_rag.py'
    [IO.File]::WriteAllText($hPath, $hcode, (New-Object Text.UTF8Encoding($false)))
    [void](& docker cp $hPath "${ApiContainer}:/tmp/check_rag.py" 2>&1)
    $hOut = Docker-Exec -Container $ApiContainer -Cmd @('python','/tmp/check_rag.py')
    Write-Host $hOut -ForegroundColor DarkGray

    $retr = ($hOut -split "`r?`n" | Where-Object { $_ -like 'RETRIEVAL|*' } | Select-Object -First 1)
    $cls  = ($hOut -split "`r?`n" | Where-Object { $_ -like 'CLASSIFY|*' }  | Select-Object -First 1)
    $retrN = 0
    if ($retr) { $retrN = [int](($retr -split '\|')[1]) }
    Set-R 'H1.retrieval' $(if ($retrN -gt 0) { 'PASS' } else { 'FAIL' }) "search_similar_templates 命中 $retrN 条（$retr）"

    if ($cls -match '^CLASSIFY\|BLOCKED') {
        Set-R 'H2.classify.method' 'BLOCKED' "容器内无 DeepSeek Key，无法跑完整分类链（检索命中 $retrN 条 > 0，按代码逻辑不会走 fallback）"
        Set-R 'H3.classify.fallback' 'BLOCKED' '同上'
    } elseif ($cls -match '^CLASSIFY\|ERROR') {
        Set-R 'H2.classify.method' 'FAIL' $cls
        Set-R 'H3.classify.fallback' 'FAIL' $cls
    } else {
        $p = $cls -split '\|'
        $method = $p[1] ; $fb = $p[2] ; $tm = [int]$p[3]
        Set-R 'H2.classify.method' $(if ($method -eq 'rag') { 'PASS' } else { 'FAIL' }) "method=$method（必须为 rag，出现 rag-fallback-llm 即 FAIL）"
        Set-R 'H3.classify.fallback' $(if ($fb -eq 'False') { 'PASS' } else { 'FAIL' }) "fallback=$fb"
        Set-R 'H4.classify.top_matches' $(if ($tm -gt 0) { 'PASS' } else { 'FAIL' }) "top_matches=$tm"
    }

    # ═══════════════════════════════════════════════════════════════════════
    # I. 16 项 E2E（真实 HTTP）
    # ═══════════════════════════════════════════════════════════════════════
    Head 'I. 16 项 E2E（真实 HTTP + 容器）'

    # ── 生成测试夹具（在容器内生成，宿主无需 Python；全部为合成数据，不含任何真实合同）──
    Sub 'I0. 生成合成测试合同（容器内生成 → docker cp 到宿主）'
    $genCode = @'
import os
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from PIL import Image, ImageDraw, ImageFont

OUT = "/tmp/fixtures"
os.makedirs(OUT, exist_ok=True)

# 中文合同（DOCX 是纯文本，不依赖字体文件）——用于分类 / 审核 / 修改 / 导出
CN = [
    "建设工程施工合同",
    "合同编号：A24-ACC-0001",
    "第一条 工程概况",
    "工程名称：验收测试用合成工程",
    "第二条 合同价款",
    "合同总价为人民币壹佰万元整",
    "第三条 违约责任",
    "违约金为合同总价的 30%",
    "第四条 保密",
    "双方对合同内容承担永久保密义务",
    "第五条 争议解决",
    "提交甲方所在地人民法院裁决",
]
d = Document()
for ln in CN:
    d.add_paragraph(ln)
d.save(os.path.join(OUT, "acceptance.docx"))

# 文字层 PDF：用 reportlab 内置 CID 中文字体（无需字体文件）
c = canvas.Canvas(os.path.join(OUT, "acceptance_text.pdf"), pagesize=A4)
c.setFont("STSong-Light", 13)
y = 800
for ln in CN:
    c.drawString(50, y, ln); y -= 26
c.showPage(); c.save()

# 扫描件 / 图片：ASCII 渲染（镜像内无 CJK 字体文件，PIL 无法绘制中文；
# 用 reportlab 自带 Vera.ttf 保证字号可被 OCR 识别）
EN = [
    "SERVICE AGREEMENT",
    "Contract No: A24-ACC-0002",
    "Article 1 Scope",
    "The Supplier shall provide software development services.",
    "Article 2 Payment",
    "The Buyer shall prepay 90 percent before delivery.",
    "Article 3 Penalty",
    "The penalty shall be 30 percent of the contract amount.",
    "Article 4 Confidentiality",
    "Both parties keep this contract confidential permanently.",
    "Article 5 Jurisdiction",
    "Disputes go to the court where the Supplier is located.",
]
font = None
try:
    import reportlab
    p = os.path.join(os.path.dirname(reportlab.__file__), "fonts", "Vera.ttf")
    if os.path.isfile(p):
        font = ImageFont.truetype(p, 30)
except Exception:
    pass
if font is None:
    try: font = ImageFont.load_default(size=30)
    except Exception: font = ImageFont.load_default()

img = Image.new("RGB", (1700, 2200), "white")
dr = ImageDraw.Draw(img)
y = 100
for ln in EN:
    dr.text((80, y), ln, fill="black", font=font); y += 56
img.save(os.path.join(OUT, "acceptance.png"))
img.save(os.path.join(OUT, "acceptance.jpg"), "JPEG", quality=95)
img.save(os.path.join(OUT, "acceptance_scan.pdf"), "PDF", resolution=150)
print("FIXTURES_OK")
'@
    $genPath = Join-Path $TempDir 'gen_fixtures.py'
    [IO.File]::WriteAllText($genPath, $genCode, (New-Object Text.UTF8Encoding($false)))
    [void](& docker cp $genPath "${ApiContainer}:/tmp/gen_fixtures.py" 2>&1)
    $genOut = Docker-Exec -Container $ApiContainer -Cmd @('python','/tmp/gen_fixtures.py')
    [void](& docker cp "${ApiContainer}:/tmp/fixtures/." $FixtureDir 2>&1)
    $fx = Get-ChildItem $FixtureDir -File -ErrorAction SilentlyContinue
    if ($fx -and $fx.Count -ge 5) { Set-R 'I0.fixtures' 'PASS' (($fx | ForEach-Object { "$($_.Name)($($_.Length)B)" }) -join ', ') }
    else { Set-R 'I0.fixtures' 'FAIL' "夹具生成失败: $genOut" }

    # ── 注册 + 登录（拿 token）──
    Sub 'I-auth. 注册用户'
    $uname = "acc_$Suffix"
    $regJson = To-JsonFile @{ username = $uname; password = 'AccPassw0rd!' ; email = "$uname@example.com" } 'reg.json'
    $reg = Invoke-Curl -Url "$ApiBase/api/auth/register" -Method POST -JsonFile $regJson
    $regObj = Try-ParseJson $reg.Body
    if ($reg.Status -eq 200 -and $regObj.data.token) {
        $script:Token = $regObj.data.token
        Set-R 'I-auth' 'PASS' "注册并取得 token（角色应为 uploader）"
    } else {
        $loginJson = To-JsonFile @{ username = $uname; password = 'AccPassw0rd!' } 'login.json'
        $login = Invoke-Curl -Url "$ApiBase/api/auth/login" -Method POST -JsonFile $loginJson
        $loginObj = Try-ParseJson $login.Body
        if ($login.Status -eq 200 -and $loginObj.data.token) { $script:Token = $loginObj.data.token; Set-R 'I-auth' 'PASS' '登录取得 token' }
        else { Set-R 'I-auth' 'FAIL' "注册 HTTP $($reg.Status): $($reg.Body)" }
    }

    function Upload-One {
        param([string]$Path, [string]$Label, [string]$Key)
        if (-not $script:Token) { Set-R $Key 'BLOCKED' '无 token'; return $null }
        if (-not (Test-Path $Path)) { Set-R $Key 'FAIL' "夹具缺失: $Path"; return $null }
        $r = Invoke-Curl -Url "$ApiBase/api/contracts/upload" -Method POST -Form @("file=@$Path") -Token $script:Token -TimeoutSecLocal 600
        $o = Try-ParseJson $r.Body
        if ($r.Status -eq 200 -and $o.data.id) { Set-R $Key 'PASS' "HTTP 200, contract_id=$($o.data.id)"; return $o.data.id }
        else { Set-R $Key 'FAIL' "HTTP $($r.Status): $($r.Body)"; return $null }
    }

    # 1) DOCX
    Sub 'I1. DOCX 上传'
    $idDocx = Upload-One -Path (Join-Path $FixtureDir 'acceptance.docx') -Label 'docx' -Key 'I1.upload.docx'
    # 2) 普通 PDF
    Sub 'I2. 普通 PDF 上传'
    $idPdf = Upload-One -Path (Join-Path $FixtureDir 'acceptance_text.pdf') -Label 'pdf' -Key 'I2.upload.pdf'
    # 3) 扫描 PDF
    Sub 'I3. 扫描 PDF 上传（OCR）'
    $idScan = Upload-One -Path (Join-Path $FixtureDir 'acceptance_scan.pdf') -Label 'scanpdf' -Key 'I3.upload.scanpdf'
    # 4) JPG
    Sub 'I4. JPG 上传（OCR）'
    $idJpg = Upload-One -Path (Join-Path $FixtureDir 'acceptance.jpg') -Label 'jpg' -Key 'I4.upload.jpg'
    # 5) PNG
    Sub 'I5. PNG 上传（OCR）'
    $idPng = Upload-One -Path (Join-Path $FixtureDir 'acceptance.png') -Label 'png' -Key 'I5.upload.png'

    # 6) RAG 分类（读回 contract_type + confidence；method 已在 H 段验证）
    Sub 'I6. 合同类型分类'
    if ($idDocx) {
        $r = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx" -Token $script:Token
        $o = Try-ParseJson $r.Body
        $ct = $o.data.contract_type ; $cf = $o.data.type_confidence
        if ($ct -and $ct -ne 'other') { Set-R 'I6.classify' 'PASS' "contract_type=$ct, confidence=$cf" }
        else { Set-R 'I6.classify' 'FAIL' "contract_type=$ct（分类未生效）" }
        if ($o.data.parsed_text -and $o.data.parsed_text.Length -gt 10) { Set-R 'I6b.parsed_text' 'PASS' "parsed_text 长度 $($o.data.parsed_text.Length)" }
        else { Set-R 'I6b.parsed_text' 'FAIL' 'parsed_text 为空' }
    } else { Set-R 'I6.classify' 'BLOCKED' 'DOCX 未成功上传' }

    # 7) 风险审核 + 8) 风险证据
    Sub 'I7. 风险审核（异步）+ I8. 风险证据'
    if (-not $hasKey) {
        Set-R 'I7.audit' 'BLOCKED' '缺少 DeepSeek API Key：审核链路依赖外部 LLM'
        Set-R 'I8.risk-evidence' 'BLOCKED' '同上'
    } elseif (-not $idDocx) {
        Set-R 'I7.audit' 'BLOCKED' 'DOCX 未成功上传'
        Set-R 'I8.risk-evidence' 'BLOCKED' '同上'
    } else {
        $a = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/audit" -Method POST -Token $script:Token -TimeoutSecLocal 120
        $done = $false
        for ($i = 0; $i -lt [math]::Ceiling($TimeoutSec / 5); $i++) {
            Start-Sleep -Seconds 5
            $cr = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx" -Token $script:Token
            $co = Try-ParseJson $cr.Body
            if ($co.data.status -eq 'completed') { $done = $true; break }
        }
        if ($done) {
            $rr = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/audit-result" -Token $script:Token
            $ro = Try-ParseJson $rr.Body
            $n = $ro.data.total
            Set-R 'I7.audit' 'PASS' "审核完成，风险条数=$n"
            $withEv = @($ro.data.items | Where-Object { $_.reason -or $_.evidence -or $_.clause_text }).Count
            if ($n -eq 0) { Set-R 'I8.risk-evidence' 'MANUAL_REQUIRED' '该合成合同未命中风险，无法核验证据字段（建议换一份含高风险条款的合同手工复核）' }
            elseif ($withEv -gt 0) { Set-R 'I8.risk-evidence' 'PASS' "$withEv/$n 条含 reason/evidence/clause_text" }
            else { Set-R 'I8.risk-evidence' 'FAIL' '风险条目缺少证据字段' }
        } else { Set-R 'I7.audit' 'FAIL' "审核超时（>${TimeoutSec}s）：HTTP $($a.Status) $($a.Body)" ; Set-R 'I8.risk-evidence' 'BLOCKED' '审核未完成' }
    }

    # 9/10/11) 修改 / 多轮 / adopted
    $adoptedId = $null
    if ($idDocx -and $hasKey -and (Test-Path (Join-Path $FixtureDir 'acceptance.docx'))) {
        Sub 'I9. 普通条款修改（replace）'
        $clause = '违约金为合同总价的 30%'
        $j1 = To-JsonFile @{ clause_key = 'acc1'; clause_text = $clause; instruction = '把违约金比例调低到合理范围，并要求与实际损失相适应'; history = @(); scope = 'clause'; operation = 'replace' } 'rev1.json'
        $r1 = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/revise" -Method POST -JsonFile $j1 -Token $script:Token -TimeoutSecLocal 300
        $o1 = Try-ParseJson $r1.Body
        if ($r1.Status -eq 200 -and $o1.data.revision_id) {
            Set-R 'I9.revise.replace' 'PASS' "revision_id=$($o1.data.revision_id)"
            $rid1 = $o1.data.revision_id
            $rev1 = $o1.data.revised_clause

            Sub 'I10. 多轮修改会话'
            $j2 = To-JsonFile @{ clause_key = 'acc1'; clause_text = $rev1; instruction = '再补充一句：违约金以实际损失为限'; history = @(); scope = 'clause'; operation = 'replace' } 'rev2.json'
            $r2 = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/revise" -Method POST -JsonFile $j2 -Token $script:Token -TimeoutSecLocal 300
            $o2 = Try-ParseJson $r2.Body
            if ($r2.Status -eq 200 -and $o2.data.revision_id) {
                $rid2 = $o2.data.revision_id
                Set-R 'I10.revise.multiround' 'PASS' "第 2 轮 revision_id=$rid2（同一 clause_key=acc1）"
                Sub 'I11. 确认采用 adopted'
                $j3 = To-JsonFile @{ clause_key = 'acc1' } 'adopt.json'
                $r3 = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/revisions/$rid2/adopt" -Method POST -JsonFile $j3 -Token $script:Token
                $o3 = Try-ParseJson $r3.Body
                if ($r3.Status -eq 200 -and $o3.data.adopted) { Set-R 'I11.adopt' 'PASS' "adopted=true (revision $rid2)"; $adoptedId = $rid2 }
                else { Set-R 'I11.adopt' 'FAIL' "HTTP $($r3.Status): $($r3.Body)" }
            } else { Set-R 'I10.revise.multiround' 'FAIL' "HTTP $($r2.Status): $($r2.Body)"; Set-R 'I11.adopt' 'BLOCKED' '第 2 轮失败' }
        } else { Set-R 'I9.revise.replace' 'FAIL' "HTTP $($r1.Status): $($r1.Body)"; Set-R 'I10.revise.multiround' 'BLOCKED' '第 1 轮失败'; Set-R 'I11.adopt' 'BLOCKED' '第 1 轮失败' }
    } else {
        $why = if (-not $hasKey) { '缺少 DeepSeek Key' } else { 'DOCX 未成功上传' }
        Set-R 'I9.revise.replace' 'BLOCKED' $why ; Set-R 'I10.revise.multiround' 'BLOCKED' $why ; Set-R 'I11.adopt' 'BLOCKED' $why
    }

    # 12/13) R09 新增条款 + 用户自主选择位置
    if ($idDocx -and $hasKey) {
        Sub 'I13. R09 位置候选（只读接口，用户自主选择的前提）'
        $jL = To-JsonFile @{ query = '第三条 违约责任' } 'locate.json'
        $rL = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/locate-clause" -Method POST -JsonFile $jL -Token $script:Token -TimeoutSecLocal 120
        $oL = Try-ParseJson $rL.Body
        $cands = @($oL.data.candidates)
        if ($rL.Status -eq 200 -and $cands.Count -gt 0) { Set-R 'I13.r09.position-candidates' 'PASS' "定位候选 $($cands.Count) 条（用户自主选择的前提成立）" }
        else { Set-R 'I13.r09.position-candidates' 'FAIL' "HTTP $($rL.Status)，候选 $($cands.Count) 条: $($rL.Body)" }

        $jS = To-JsonFile @{ risk_type = 'R09'; instruction = '' } 'sugg.json'
        $rS = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/add-clause-suggestion" -Method POST -JsonFile $jS -Token $script:Token -TimeoutSecLocal 300
        $oS = Try-ParseJson $rS.Body
        $occ = @($oS.data.heading_occurrences)
        if ($rS.Status -eq 200 -and $occ.Count -gt 0) {
            Set-R 'I12.r09.suggestion' 'PASS' "建议位置 + headings/occurrences=$($occ.Count) 条（含 target_text/paragraph_index）"
            Sub 'I12. R09 新增条款（add_clause，使用用户选定位置）'
            $pick = $occ | Where-Object { $_.num -eq 3 } | Select-Object -First 1
            if (-not $pick) { $pick = $occ[0] }
            $jA = To-JsonFile @{
                clause_key = 'acc_r09'; clause_text = ''; scope = 'clause'; operation = 'add_clause'
                instruction = '补充一条不可抗力条款，约定通知义务与免责范围'
                position = @{ anchor = $pick.cn; target_text = $pick.target_text; paragraph_index = $pick.paragraph_index }
            } 'add.json'
            $rA = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/revise" -Method POST -JsonFile $jA -Token $script:Token -TimeoutSecLocal 300
            $oA = Try-ParseJson $rA.Body
            if ($rA.Status -eq 200 -and $oA.data.revision_id) {
                Set-R 'I12.r09.add_clause' 'PASS' "新增条款已生成 revision_id=$($oA.data.revision_id)（位置: 第$($pick.cn)条 target_text=$($pick.target_text) idx=$($pick.paragraph_index)）"
                $ridR09 = $oA.data.revision_id
                $jA2 = To-JsonFile @{ clause_key = 'acc_r09' } 'adopt_r09.json'
                $rA2 = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/revisions/$ridR09/adopt" -Method POST -JsonFile $jA2 -Token $script:Token
                if ($rA2.Status -eq 200) { Add-Note "R09 采用成功（revision $ridR09）" } else { Add-Note "R09 采用失败: HTTP $($rA2.Status) $($rA2.Body)" }
            } else { Set-R 'I12.r09.add_clause' 'FAIL' "HTTP $($rA.Status): $($rA.Body)" }
        } else { Set-R 'I12.r09.suggestion' 'FAIL' "HTTP $($rS.Status): $($rS.Body)"; Set-R 'I12.r09.add_clause' 'BLOCKED' '未取得候选位置' }
    } else {
        $why = if (-not $hasKey) { '缺少 DeepSeek Key' } else { 'DOCX 未成功上传' }
        Set-R 'I12.r09.suggestion' 'BLOCKED' $why ; Set-R 'I12.r09.add_clause' 'BLOCKED' $why ; Set-R 'I13.r09.position-candidates' 'BLOCKED' $why
    }

    # 14) DOCX 导出（含 replace + add_clause 组合，验证不再出现 S-1 自检误判 400）
    Sub 'I14. DOCX 导出（replace + add_clause 组合）'
    if ($idDocx) {
        $outDocx = Join-Path $TempDir 'revised.docx'
        $r = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/revised-docx" -OutFile $outDocx -Token $script:Token -TimeoutSecLocal 300
        $magic = ''
        if (Test-Path $outDocx) { $bytes = [IO.File]::ReadAllBytes($outDocx) ; if ($bytes.Length -ge 2) { $magic = [Text.Encoding]::ASCII.GetString($bytes[0..1]) } }
        if ($r.Status -eq 200 -and $magic -eq 'PK') {
            $kb = [math]::Round((Get-Item $outDocx).Length / 1KB, 1)
            Set-R 'I14.export.docx' 'PASS' "HTTP 200，DOCX 大小 $kb KB，zip magic=PK"
        }
        elseif ($r.Status -eq 400) {
            $detail = ''
            if (Test-Path $outDocx) { $detail = (Get-Content $outDocx -Raw -ErrorAction SilentlyContinue) }
            Set-R 'I14.export.docx' 'FAIL' "HTTP 400 —— 需排查是否为 S-1「replace + add_clause 自检误判」：$detail"
        }
        else { Set-R 'I14.export.docx' 'FAIL' "HTTP $($r.Status)" }
    } else { Set-R 'I14.export.docx' 'BLOCKED' 'DOCX 未成功上传' }

    # 15) PDF 审核报告导出
    Sub 'I15. PDF 正式审核报告导出'
    if ($idDocx) {
        $outPdf = Join-Path $TempDir 'report.pdf'
        $r = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/audit-report/pdf" -OutFile $outPdf -Token $script:Token -TimeoutSecLocal 300
        $magic = ''
        if (Test-Path $outPdf) { $bytes = [IO.File]::ReadAllBytes($outPdf) ; if ($bytes.Length -ge 4) { $magic = [Text.Encoding]::ASCII.GetString($bytes[0..3]) } }
        if ($r.Status -eq 200 -and $magic -eq '%PDF') { Set-R 'I15.export.report-pdf' 'PASS' "HTTP 200，PDF magic=%PDF，大小 $([math]::Round((Get-Item $outPdf).Length/1KB,1)) KB" }
        elseif (-not $hasKey) { Set-R 'I15.export.report-pdf' 'BLOCKED' "HTTP $($r.Status)（无 Key 时通常无审核结果可导出）" }
        else { Set-R 'I15.export.report-pdf' 'FAIL' "HTTP $($r.Status)" }
    } else { Set-R 'I15.export.report-pdf' 'BLOCKED' 'DOCX 未成功上传' }

    # 16) 前端 / SPA 路由
    Sub 'I16. 前端页面与 SPA 路由直达'
    $r1 = Invoke-Curl -Url "$WebBase/" -TimeoutSecLocal 30
    $idxOk = ($r1.Status -eq 200 -and $r1.Body -match '<div id="app"')
    $r2 = Invoke-Curl -Url "$WebBase/contracts" -TimeoutSecLocal 30
    $deepOk = ($r2.Status -eq 200 -and $r2.Body -match '<div id="app"')
    if ($idxOk -and $deepOk) { Set-R 'I16.frontend.spa' 'PASS' '首页与深链 /contracts 均 200 且返回 index.html（createWebHistory 回退正常）' }
    elseif ($idxOk -and -not $deepOk) { Set-R 'I16.frontend.spa' 'FAIL' "深链 /contracts 返回 $($r2.Status)（SPA 回退异常，检查 serve -s）" }
    else { Set-R 'I16.frontend.spa' 'FAIL' "首页返回 $($r1.Status)" }

    # ═══════════════════════════════════════════════════════════════════════
    # F. save/load 之后复验（用 load 回来的镜像再起一套）
    # ═══════════════════════════════════════════════════════════════════════
    Head 'F. save/load 之后复验（用 load 回来的镜像重启一套）'
    if ($SkipSaveLoad) {
        Set-R 'F.loaded.health' 'SKIP' '按 -SkipSaveLoad 跳过'
    } else {
        # 关掉当前一套（保留卷），改用 load 回来的 tag 再起
        Push-Location $TempDir
        try {
            & docker compose -p $ProjectName down 2>&1 | Out-Null
            $composeText2 = ($composeText -replace [regex]::Escape($ApiImage), $ApiImageSave) -replace [regex]::Escape($WebImage), $WebImageSave
            [IO.File]::WriteAllText($composeDst, $composeText2, (New-Object Text.UTF8Encoding($false)))
            & docker compose -p $ProjectName up -d 2>&1 | Out-Null
            $up2 = $LASTEXITCODE

            $ok = $false
            for ($i = 0; $i -lt 60; $i++) { $rr = Invoke-Curl -Url "$ApiBase/" -TimeoutSecLocal 5 ; if ($rr.Status -eq 200) { $ok = $true; break } ; Start-Sleep -Seconds 3 }
            Set-R 'F.loaded.health' $(if ($ok) { 'PASS' } else { 'FAIL' }) "compose up exit=$up2；GET / → $(if($ok){'200'}else{'失败'})"

            $w2 = $false
            for ($i = 0; $i -lt 80; $i++) {
                $rr = Invoke-Curl -Url "$ApiBase/api/health" -TimeoutSecLocal 10
                if ($rr.Status -eq 200) { $o = Try-ParseJson $rr.Body ; if ($o.warmup.rag -eq 'ready') { $w2 = $true; break } ; if ($o.warmup.rag -eq 'failed') { break } }
                Start-Sleep -Seconds 3
            }
            Set-R 'F.loaded.warmup' $(if ($w2) { 'PASS' } else { 'FAIL' }) "warmup.rag=$(if($w2){'ready'}else{'未 ready'})"

            $c2 = Docker-Exec -Container $ApiContainer -Cmd @('python','/tmp/check_rag.py')
            $cls2 = ($c2 -split "`r?`n" | Where-Object { $_ -like 'CLASSIFY|*' } | Select-Object -First 1)
            if (-not $hasKey) { Set-R 'F.loaded.classify' 'BLOCKED' '无 Key：无法验证真实分类链（检索部分见上）' }
            elseif ($cls2 -match '^CLASSIFY\|rag\|') { Set-R 'F.loaded.classify' 'PASS' $cls2 }
            else { Set-R 'F.loaded.classify' 'FAIL' "$cls2" }

            if ($idDocx) {
                $outD = Join-Path $TempDir 'revised2.docx'
                $rd = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/revised-docx" -OutFile $outD -Token $script:Token -TimeoutSecLocal 300
                Set-R 'F.loaded.export-docx' $(if ($rd.Status -eq 200) { 'PASS' } else { 'FAIL' }) "HTTP $($rd.Status)"
                $outP = Join-Path $TempDir 'report2.pdf'
                $rp = Invoke-Curl -Url "$ApiBase/api/contracts/$idDocx/audit-report/pdf" -OutFile $outP -Token $script:Token -TimeoutSecLocal 300
                Set-R 'F.loaded.export-pdf' $(if ($rp.Status -eq 200) { 'PASS' } else { $(if ($hasKey) { 'FAIL' } else { 'BLOCKED' }) }) "HTTP $($rp.Status)"
            } else { Set-R 'F.loaded.export-docx' 'BLOCKED' '无合同'; Set-R 'F.loaded.export-pdf' 'BLOCKED' '无合同' }
        } finally { Pop-Location }
    }
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
}

# ═══════════════════════════════════════════════════════════════════════════
# J. 报告
# ═══════════════════════════════════════════════════════════════════════════
function Get-R { param([string]$Key) ; if ($script:R.Contains($Key)) { $script:R[$Key].Status } else { 'NOT_RUN' } }

$sb = New-Object Text.StringBuilder
function W { param([string]$T = '') ; [void]$sb.AppendLine($T) }

W "# A24 Docker 交付验收报告"
W ""
W "- 生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
W "- 机器：$env:COMPUTERNAME / $([Environment]::OSVersion.VersionString)"
W "- 项目根：$RepoRoot"
W "- compose project：$ProjectName"
W "- DeepSeek Key：$(if ($hasKey) { '已提供（内容未落盘、未回显）' } else { '未提供 → 依赖 LLM 的项标 BLOCKED' })"
W ""
W "## A. Docker build"
W "| 项 | 结果 | 说明 |"
W "|---|---|---|"
foreach ($k in $script:R.Keys) { if ($k -like 'A*' -or $k -like 'B*' -or $k -like 'C*') { W ("| {0} | {1} | {2} |" -f $k, $script:R[$k].Status, ($script:R[$k].Detail -replace '\|','/')) } }
W ""
W "## B. Docker save / load"
W "| 项 | 结果 | 说明 |"
W "|---|---|---|"
foreach ($k in $script:R.Keys) { if ($k -like 'D*') { W ("| {0} | {1} | {2} |" -f $k, $script:R[$k].Status, ($script:R[$k].Detail -replace '\|','/')) } }
W ""
W "## C. Fresh named volume / Chroma"
W "| 项 | 结果 | 说明 |"
W "|---|---|---|"
foreach ($k in $script:R.Keys) { if ($k -like 'E*' -or $k -like 'F0*' -or $k -like 'F1*' -or $k -like 'F2*') { W ("| {0} | {1} | {2} |" -f $k, $script:R[$k].Status, ($script:R[$k].Detail -replace '\|','/')) } }
W ""
W "## D. RAG"
W "| 项 | 结果 | 说明 |"
W "|---|---|---|"
foreach ($k in $script:R.Keys) { if ($k -like 'G*' -or $k -like 'H*') { W ("| {0} | {1} | {2} |" -f $k, $script:R[$k].Status, ($script:R[$k].Detail -replace '\|','/')) } }
W ""
W "## E. 16 项 E2E"
W "| 项 | 结果 | 说明 |"
W "|---|---|---|"
foreach ($k in $script:R.Keys) { if ($k -like 'I*') { W ("| {0} | {1} | {2} |" -f $k, $script:R[$k].Status, ($script:R[$k].Detail -replace '\|','/')) } }
W ""
W "## F. save/load 后复验"
W "| 项 | 结果 | 说明 |"
W "|---|---|---|"
foreach ($k in $script:R.Keys) { if ($k -like 'F.loaded*') { W ("| {0} | {1} | {2} |" -f $k, $script:R[$k].Status, ($script:R[$k].Detail -replace '\|','/')) } }
W ""
W "## G. 外网依赖"
W "- **DeepSeek API（https://api.deepseek.com）必须联网**：合同分类 / 要素抽取 / 证据抽取 / 建议生成 / 条款比对 / 条款修改均依赖它；"
W "  `upload` / `audit` / `add-clause-suggestion` / `revise` 四个端点都要求存在可用 Key。"
W "- 其余（Python 依赖 / OCR 模型 / embedding 模型 / 向量库 / 前端产物）按设计全部由镜像提供；"
W "  本次是否真正做到离线自足，以上方 C1（镜像自检）实测为准。"
W "- **本报告不声称「完全离线可运行」**。"
W ""
W "## H. 问题分类"
W "### 必须修复"
$mustFix = $script:R.Keys | Where-Object { $script:R[$_].Status -eq 'FAIL' }
if ($mustFix) { foreach ($k in $mustFix) { W ("- **{0}**：{1}" -f $k, $script:R[$k].Detail) } } else { W "- （无 FAIL 项）" }
W ""
W "### BLOCKED（环境/凭据缺失，不代表产品缺陷）"
$blk = $script:R.Keys | Where-Object { $script:R[$_].Status -eq 'BLOCKED' }
if ($blk) { foreach ($k in $blk) { W ("- {0}：{1}" -f $k, $script:R[$k].Detail) } } else { W "- （无）" }
W ""
W "### MANUAL_REQUIRED（无法自动化，需人工复核）"
$man = $script:R.Keys | Where-Object { $script:R[$_].Status -eq 'MANUAL_REQUIRED' }
if ($man) { foreach ($k in $man) { W ("- {0}：{1}" -f $k, $script:R[$k].Detail) } } else { W "- （无）" }
W ""
W "### 备注"
if ($script:Notes.Count -gt 0) { foreach ($n in $script:Notes) { W ("- {0}" -f $n) } } else { W "- （无）" }
W ""
W "## I. 最终状态"
W "- 本脚本不修改任何项目文件；如需确认请运行 git status / git rev-parse HEAD"
W "- 未 commit、未 push（本脚本不执行任何 git 写操作）"
W ""
W "## 夹具说明（重要）"
W "- 所有测试合同均为**脚本在容器内合成的假数据**，不含任何真实合同；"
W "- 中文夹具（DOCX / 文字层 PDF）用于分类与审核；"
W "- 图片类夹具（JPG/PNG/扫描 PDF）为 **ASCII** 文本：镜像内没有 CJK 字体文件，PIL 无法绘制中文。"

$report = $sb.ToString()
[IO.File]::WriteAllText($ReportPath, $report, (New-Object Text.UTF8Encoding($false)))

Head 'J. 最终报告'
Write-Host $report
Log "报告已写入: $ReportPath"
Log "构建日志目录: $LogDir"

# ═══════════════════════════════════════════════════════════════════════════
# K. 清理（只清自己创建的资源）
# ═══════════════════════════════════════════════════════════════════════════
Head 'K. 清理'
if ($KeepResources) {
    Set-R 'K.cleanup' 'SKIP' "按 -KeepResources 保留；project=$ProjectName，临时目录=$TempDir"
} else {
    Push-Location $TempDir
    try {
        & docker compose -p $ProjectName down -v --remove-orphans 2>&1 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    } catch { }
    Pop-Location -ErrorAction SilentlyContinue
    # 兜底：按名字删本脚本创建的容器（不使用任何 *prune*）
    & docker rm -f $ApiContainer $WebContainer 2>&1 | Out-Null
    # 删本脚本创建的卷（逐个，精确匹配 project 前缀）
    $leftover = (& docker volume ls --format '{{.Name}}' 2>&1 | Where-Object { $_ -like "$ProjectName*" })
    foreach ($v in $leftover) { & docker volume rm $v 2>&1 | Out-Null }
    # 删本脚本打的 tag（不删用户其它镜像）
    & docker rmi -f $ApiImage $WebImage $ApiImageSave $WebImageSave 2>&1 | Out-Null
    # 删临时目录（报告已另存到 $ReportPath）
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
    Set-R 'K.cleanup' 'PASS' "已清理容器 / 卷(project=$ProjectName) / acceptance tag / 临时目录；报告保留在 $ReportPath"
}

# 汇总
Head '验收汇总'
$order = @('PASS','FAIL','BLOCKED','MANUAL_REQUIRED','INFO','SKIP')
foreach ($s in $order) {
    $c = @($script:R.Keys | Where-Object { $script:R[$_].Status -eq $s }).Count
    if ($c -gt 0) { Write-Host ("  {0,-16} {1}" -f $s, $c) -ForegroundColor $(if ($s -eq 'FAIL') { 'Red' } elseif ($s -eq 'PASS') { 'Green' } else { 'Yellow' }) }
}
$fails = @($script:R.Keys | Where-Object { $script:R[$_].Status -eq 'FAIL' }).Count
Write-Host ''
if ($fails -gt 0) { Write-Host "结论：存在 $fails 项 FAIL，需修复后重新验收。" -ForegroundColor Red ; exit 1 }
else { Write-Host "结论：无 FAIL 项。请同时查看 BLOCKED / MANUAL_REQUIRED 项，不要把它们当作 PASS。" -ForegroundColor Green ; exit 0 }
