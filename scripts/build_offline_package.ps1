# =============================================================================
#  A24 合同智能审核系统 —— 离线完整包打包脚本
#
#  用途：生成用于「省赛网评 / 现场答辩」的完整 ZIP。省赛参赛指南**没有**总包体积
#        上限（仅 S4B 演示视频限制 150MB），因此本包把离线运行所需的大资源一并装入：
#          · models\hf-cache\             embedding 模型（约 390MB，已解开符号链接）
#          · backend\chroma_data\         预构建向量库（约 45MB）
#          · 完整源码、建库源、Docker 文件、文档、启动器
#
#  交付效果：拿到本包后**不需要联网下载 embedding，也不需要重新构建 Chroma**，
#            直接 start.bat 即可运行当前生产 RAG 链路。
#
#  安全：绝不打包 .env（真实 DeepSeek Key / SECRET_KEY）、真实数据库、真实合同数据、
#        venv、node_modules、评测缓存、.idea、sketches、答辩PPT。
#
#  用法：
#      powershell -ExecutionPolicy Bypass -File scripts\build_offline_package.ps1
#      ... -OutDir D:\deliver -PackageName "A24-合同智能审核系统-离线完整包"
# =============================================================================
[CmdletBinding()]
param(
    # ZIP 输出目录。默认为**仓库根目录的上一级**（同级）下的 delivery\，
    # 即交付产物放在 Git 项目目录**之外**，避免与源码混在一起或被误提交。
    [string]$OutDir = "",
    [string]$PackageName = "",
    # 保留临时暂存目录（排错用）
    [switch]$KeepStaging
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root 'backend\main.py'))) {
    Write-Host "[错误] 未找到 backend\main.py，请确认本脚本位于 <仓库根>\scripts\ 下。" -ForegroundColor Red
    exit 1
}

$Stamp = Get-Date -Format 'yyyyMMdd'
if ([string]::IsNullOrWhiteSpace($PackageName)) { $PackageName = "A24-合同智能审核系统-离线完整包-$Stamp" }
if ([string]::IsNullOrWhiteSpace($OutDir))      { $OutDir = Join-Path (Split-Path -Parent $Root) 'delivery' }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$StageRoot = Join-Path $env:TEMP ("a24_offline_stage_" + $Stamp)
$Stage     = Join-Path $StageRoot $PackageName
$ZipPath   = Join-Path $OutDir ($PackageName + '.zip')

Write-Host "============================================================"
Write-Host "  A24 离线完整包"
Write-Host "============================================================"
Write-Host "  仓库根 : $Root"
Write-Host "  阶段目录: $Stage"
Write-Host "  输出 ZIP: $ZipPath"
Write-Host ""

if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null

# --- 排除规则（一律用【完整路径】，绝不用裸目录名）-------------------------
# 关键教训：backend\models 是 SQLAlchemy 模型源码包，若按裸名 "models" 排除，
# 会把源码一起排除掉。本包需要 models\（模型缓存），但排除清单必须精确到路径，
# 否则无法区分「根目录的模型缓存」与「backend 的源码包」。
$XdDirs = @(
    (Join-Path $Root '.git'),
    (Join-Path $Root '.idea'),
    (Join-Path $Root '.vscode'),
    (Join-Path $Root 'venv'),
    (Join-Path $Root 'backend\venv'),
    (Join-Path $Root 'backend\data'),
    (Join-Path $Root 'mysql_data'),
    (Join-Path $Root 'sketches'),
    (Join-Path $Root '答辩PPT'),
    (Join-Path $Root 'frontend\node_modules'),
    (Join-Path $Root 'frontend\dist'),
    (Join-Path $Root 'frontend\.vite'),
    (Join-Path $Root 'backend\evaluate\_pilot5'),
    (Join-Path $Root 'chroma_data'),           # 根目录空占位（注意：不排除 backend\chroma_data）
    (Join-Path $Root 'data')                   # 根目录空占位（注意：不排除 backend\data 由上一行单独处理）
)
$XdNames = @('__pycache__', '.pytest_cache', '.mypy_cache', 'node_modules')

$XfFiles = @(
    '.env', '.env.local', '.env.production', '.env.development',
    'contract.db', 'contract.db-wal', 'contract.db-shm',
    '*.pyc', '*.pyo', '*.log', '.DS_Store', 'Thumbs.db',
    'cache.json', 'cache_elements.json', 'cache_risks_llm.json',
    'rag_predictions.json'
)
# 不要把「已经生成好的交付包」再装进新的交付包：否则包会自我嵌套、体积虚增。
# 这里按交付包命名精确排除（而不用 *.zip 一刀切，避免以后误删需要交付的压缩包）。
$XfFiles += ($PackageName + '.zip')
$XfFiles += 'A24-*-校赛包-*.zip'
$XfFiles += 'A24-*-离线完整包-*.zip'

Write-Host "[1/4] 复制交付内容（含模型与向量库）..."
Write-Host "      提示：模型约 390MB，复制需要一点时间。"
$rcArgs = @($Root, $Stage, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
$rcArgs += '/XD'
$rcArgs += $XdDirs
$rcArgs += $XdNames
$rcArgs += '/XF'
$rcArgs += $XfFiles
& robocopy @rcArgs | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "[错误] robocopy 复制失败，退出码 = $LASTEXITCODE" -ForegroundColor Red
    exit 1
}
Write-Host "      复制完成（robocopy 退出码 $LASTEXITCODE，0-7 均为正常）"
Write-Host ""

# --- 校验 -------------------------------------------------------------------
Write-Host "[2/4] 交付内容校验"
$problems = @()
function Check-Absent([string]$rel, [string]$why) {
    $p = Join-Path $Stage $rel
    if (Test-Path $p) { $script:problems += "不应包含却存在: $rel （$why）" }
    else { Write-Host ("      [PASS] 已排除 {0,-42} {1}" -f $rel, $why) }
}
function Check-Present([string]$rel, [string]$why) {
    $p = Join-Path $Stage $rel
    if (Test-Path $p) { Write-Host ("      [PASS] 已包含 {0,-42} {1}" -f $rel, $why) }
    else { $script:problems += "必须包含却缺失: $rel （$why）" }
}

Check-Present 'backend\main.py'                                     '后端入口'
Check-Present 'backend\models\__init__.py'                          'SQLAlchemy 模型源码包'
Check-Present 'backend\ai\rag\resources\contract_templates_source.json' '建库源（RAG 范本）'
Check-Present 'backend\chroma_data\chroma.sqlite3'                  '预构建向量库（离线直接可用）'
Check-Present 'models\hf-cache\hub'                                 'embedding 模型缓存根'
Check-Present 'frontend\package.json'                               '前端工程'
Check-Present 'start.bat'                                           '本地启动入口'
Check-Present 'prepare.bat'                                         '首次准备入口'
Check-Present 'scripts\check_rag_ready.py'                          'RAG 就绪性检查'
Check-Present 'Dockerfile.api'                                      'Docker 后端'
Check-Present 'Dockerfile.web'                                      'Docker 前端'
Check-Present 'docker-compose.yml'                                  'Docker 编排'
Check-Present 'README.md'                                           '交付说明'

Check-Absent '.env'                       '含真实 API Key / SECRET_KEY'
Check-Absent 'backend\contract.db'        '真实数据库'
Check-Absent 'backend\data'               '真实合同与运行数据'
Check-Absent 'backend\venv'               '虚拟环境'
Check-Absent 'venv'                       '虚拟环境'
Check-Absent 'frontend\node_modules'      '前端依赖'
Check-Absent '.git'                       '版本库历史'
Check-Absent '.idea'                      'IDE 配置'
Check-Absent 'sketches'                   '页面草稿'
Check-Absent '答辩PPT'                     '答辩材料（单独提交）'

# 模型权重必须是实体文件且完整
Write-Host ""
Write-Host "      模型完整性..."
$snapRoot = Join-Path $Stage 'models\hf-cache\hub\models--shibing624--text2vec-base-chinese\snapshots'
$weights = @()
if (Test-Path $snapRoot) {
    $weights = @(Get-ChildItem $snapRoot -Recurse -File -Filter 'model.safetensors')
}
if ($weights.Count -eq 0) {
    $problems += "必须包含却缺失: 模型权重 model.safetensors（离线包不带模型就失去意义）"
} else {
    $w = $weights[0]
    Write-Host ("      [PASS] 模型权重 {0:N0} 字节（{1:N2} MB）" -f $w.Length, ($w.Length / 1MB))
    if ($w.Length -lt 100MB) { $problems += "模型权重明显偏小（$($w.Length) 字节），可能不完整" }
}
# 交付包内不允许有符号链接（解压后可能变成断链）
$links = @(Get-ChildItem (Join-Path $Stage 'models') -Recurse -Force -ErrorAction SilentlyContinue |
           Where-Object { $_.LinkType })
if ($links.Count -gt 0) {
    $problems += "models 内仍有 $($links.Count) 个符号链接，解压后可能失效"
    $links | Select-Object -First 5 | ForEach-Object { Write-Host "        链接: $($_.FullName)" -ForegroundColor Red }
} else {
    Write-Host "      [PASS] models 内无符号链接（全部为实体文件）"
}

# 匿名性：文件名/目录名不得出现校名与指导教师姓名（省赛原创承诺，违规取消资格）
Write-Host ""
Write-Host "      匿名性检查（文件名 / 目录名）..."
$Forbidden = @('计量', '信息工程学院', '叶敏超', '雷凌', 'CJLU', 'cjlu')
$NameHits = @()
Get-ChildItem $Stage -Recurse -Force | ForEach-Object {
    foreach ($f in $Forbidden) {
        if ($_.Name -like "*$f*") { $NameHits += "$($_.FullName.Substring($Stage.Length))  <- $f" }
    }
}
if ($NameHits.Count -gt 0) {
    $problems += "匿名性违规：文件名/目录名出现校名或指导教师姓名（$($NameHits.Count) 处）"
    $NameHits | Select-Object -First 20 | ForEach-Object { Write-Host "        违规: $_" -ForegroundColor Red }
} else {
    Write-Host "      [PASS] 文件名与目录名未出现校名/指导教师姓名"
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "[错误] 校验未通过，共 $($problems.Count) 项：" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "        · $_" -ForegroundColor Red }
    if (-not $KeepStaging) { Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue }
    exit 1
}
Write-Host ""

# --- 打包 -------------------------------------------------------------------
# 注意：不能用 [ZipFile]::CreateFromDirectory —— 它用 OS 路径分隔符写条目名，
# 在 Windows 上会写成反斜杠（如 "pkg\start.bat"），而 ZIP 规范要求正斜杠 "/"。
# 反斜杠条目在 Windows 资源管理器里尚能用，但在 macOS/Linux 的 unzip、7-Zip 等
# 工具下会被当成"文件名里带反斜杠"而无法还原目录结构。因此这里手工建条目。
Write-Host "[3/4] 生成 ZIP（约 400MB 级，可能需要几分钟）..."
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$baseLen = $Stage.Length + 1
$zipStream = [System.IO.File]::Open($ZipPath, [System.IO.FileMode]::Create)
try {
    $archive = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($f in (Get-ChildItem $Stage -Recurse -File -Force)) {
            $rel = $f.FullName.Substring($baseLen).Replace('\', '/')
            $entry = $archive.CreateEntry(($PackageName + '/' + $rel), [System.IO.Compression.CompressionLevel]::Optimal)
            $es = $entry.Open()
            try {
                $fs = [System.IO.File]::OpenRead($f.FullName)
                try { $fs.CopyTo($es) } finally { $fs.Dispose() }
            } finally { $es.Dispose() }
        }
    } finally { $archive.Dispose() }
} finally { $zipStream.Dispose() }
Write-Host "      已生成: $ZipPath"

# 回读校验：条目名必须与暂存目录一致，且不得出现反斜杠（跨平台解压要求）
$staged = @(Get-ChildItem $Stage -Recurse -File -Force |
    ForEach-Object { $_.FullName.Substring($baseLen).Replace('\', '/') })
$archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
    $entries = @($archive.Entries | Where-Object { $_.Name -ne '' } | ForEach-Object {
        $_.FullName.Substring($PackageName.Length + 1) })
} finally { $archive.Dispose() }
$badSep = @($entries | Where-Object { $_ -match '\\' })
if ($badSep.Count -gt 0) {
    Write-Host "[错误] ZIP 条目名含反斜杠（违反 ZIP 规范，跨平台解压会失败）：$($badSep.Count) 个" -ForegroundColor Red
    $badSep | Select-Object -First 5 | ForEach-Object { Write-Host "        · $_" -ForegroundColor Red }
    if (-not $KeepStaging) { Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue }
    exit 1
}
$missingInZip = @($staged | Where-Object { $entries -notcontains $_ })
if ($missingInZip.Count -gt 0) {
    Write-Host "[错误] ZIP 内条目与暂存目录不一致，缺失 $($missingInZip.Count) 个：" -ForegroundColor Red
    $missingInZip | Select-Object -First 10 | ForEach-Object { Write-Host "        · $_" -ForegroundColor Red }
    if (-not $KeepStaging) { Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue }
    exit 1
}
Write-Host "      [PASS] ZIP 内 $($entries.Count) 个文件与暂存目录逐一对应（正斜杠分隔、中文文件名完好）"
Write-Host ""

# --- 体积与清单 -------------------------------------------------------------
Write-Host "[4/4] 体积与资源清单"
$zipMB     = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
$rawBytes  = (Get-ChildItem $Stage -Recurse -File -Force | Measure-Object Length -Sum).Sum
$rawMB     = [math]::Round($rawBytes / 1MB, 2)
$fileCount = (Get-ChildItem $Stage -Recurse -File -Force).Count
Write-Host "      未压缩 : $rawMB MB / $fileCount 个文件"
Write-Host "      压缩后 : $zipMB MB"
Write-Host ""
Write-Host "      顶层条目（未压缩大小）："
Get-ChildItem $Stage -Force | Sort-Object { -not $_.PSIsContainer }, Name | ForEach-Object {
    if ($_.PSIsContainer) {
        $s = (Get-ChildItem $_.FullName -Recurse -File -Force | Measure-Object Length -Sum).Sum
        Write-Host ("        {0,-34} {1,9:N2} MB" -f ($_.Name + '\'), ($s / 1MB))
    } else {
        Write-Host ("        {0,-34} {1,9:N2} MB" -f $_.Name, ($_.Length / 1MB))
    }
}
Write-Host ""
Write-Host "      大资源清单："
$modelMB = [math]::Round((Get-ChildItem (Join-Path $Stage 'models') -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB, 2)
$chromaMB = [math]::Round((Get-ChildItem (Join-Path $Stage 'backend\chroma_data') -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB, 2)
$jsonMB = [math]::Round((Get-Item (Join-Path $Stage 'backend\ai\rag\resources\contract_templates_source.json')).Length / 1MB, 2)
Write-Host ("        models\hf-cache\                          {0,9:N2} MB" -f $modelMB)
Write-Host ("        backend\chroma_data\                     {0,9:N2} MB" -f $chromaMB)
Write-Host ("        ai\rag\resources\...source.json          {0,9:N2} MB" -f $jsonMB)
Write-Host ""

Write-Host "============================================================"
Write-Host "  离线完整包生成成功" -ForegroundColor Green
Write-Host "    ZIP   : $ZipPath"
Write-Host "    大小  : $zipMB MB"
Write-Host "    文件数: $fileCount"
Write-Host ""
Write-Host "  本包已内置模型与向量库：解压后直接运行 start.bat 即可离线启动生产 RAG。"
Write-Host "  注意：DeepSeek API Key 仍必须自行在 .env 配置（大模型审核为联网依赖），"
Write-Host "        本包出于安全考虑不包含 .env。"
Write-Host "  校赛按 50MB 上限提交时请改用 build_school_package.ps1 生成的小包。"
Write-Host "============================================================"

if (-not $KeepStaging) { Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue }
exit 0
