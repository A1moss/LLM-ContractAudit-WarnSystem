# =============================================================================
#  A24 合同智能审核系统 —— 校赛交付小包打包脚本
#
#  用途：生成用于「校赛邮件提交」的 ZIP。校赛通知硬性要求
#        「总数据量不超过 50MB」，因此本脚本：
#          · 只装源码 + 建库源 + Docker 文件 + 文档 + prepare 工具；
#          · 排除 embedding 模型（约 390MB）与预构建向量库（约 45MB）；
#          · 打包完成后**强制校验 ZIP <= 50MB**，超限直接失败并报告超限来源；
#          · 绝不用「删源码/删文档」的方式压体积。
#
#  与离线完整包的区别：
#        小包缺少模型与向量库 → 首次部署必须先跑 prepare.bat（一次性、需联网）；
#        离线完整包（build_offline_package.ps1）两者都带 → 解压即可离线运行。
#
#  安全：绝不打包 .env（含真实 DeepSeek Key 与 SECRET_KEY）、真实数据库、
#        真实合同数据、venv、node_modules、评测缓存、.idea、sketches、答辩PPT。
#
#  用法：
#      powershell -ExecutionPolicy Bypass -File scripts\build_school_package.ps1
#      ... -OutDir D:\deliver -PackageName "信息工程学院+A24合同智能审核系统"   # 校赛命名
# =============================================================================
[CmdletBinding()]
param(
    # ZIP 输出目录。默认为**仓库根目录的上一级**（同级）下的 delivery\，
    # 即交付产物放在 Git 项目目录**之外**，避免与源码混在一起或被误提交。
    [string]$OutDir = "",
    # 压缩包名（不含 .zip）。默认用中性名，见脚本末尾关于命名冲突的说明。
    [string]$PackageName = "",
    # 校赛体积上限（MB）
    [int]$LimitMB = 50,
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
if ([string]::IsNullOrWhiteSpace($PackageName)) { $PackageName = "A24-合同智能审核系统-校赛包-$Stamp" }
if ([string]::IsNullOrWhiteSpace($OutDir))      { $OutDir = Join-Path (Split-Path -Parent $Root) 'delivery' }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$StageRoot = Join-Path $env:TEMP ("a24_school_stage_" + $Stamp)
$Stage     = Join-Path $StageRoot $PackageName
$ZipPath   = Join-Path $OutDir ($PackageName + '.zip')

Write-Host "============================================================"
Write-Host "  A24 校赛交付小包"
Write-Host "============================================================"
Write-Host "  仓库根 : $Root"
Write-Host "  阶段目录: $Stage"
Write-Host "  输出 ZIP: $ZipPath"
Write-Host "  体积上限: $LimitMB MB"
Write-Host ""

# --- 清理旧暂存 -------------------------------------------------------------
if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null

# --- 排除规则（一律用【完整路径】，绝不用裸目录名）-------------------------
# 关键教训：backend\models 是 SQLAlchemy 模型源码包，若按裸名 "models" 排除，
# 会把源码一起排除掉，交付包直接跑不起来 —— 必须按路径精确排除。
$XdDirs = @(
    (Join-Path $Root '.git'),
    (Join-Path $Root '.idea'),
    (Join-Path $Root '.vscode'),
    (Join-Path $Root 'venv'),
    (Join-Path $Root 'backend\venv'),
    (Join-Path $Root 'backend\data'),
    (Join-Path $Root 'backend\chroma_data'),   # 校赛包不含预构建向量库
    (Join-Path $Root 'models'),                # 校赛包不含 embedding 模型
    (Join-Path $Root 'mysql_data'),
    (Join-Path $Root 'sketches'),
    (Join-Path $Root (Join-Path 'sketches' 'images')),
    (Join-Path $Root '答辩PPT'),
    (Join-Path $Root 'frontend\node_modules'),
    (Join-Path $Root 'frontend\dist'),
    (Join-Path $Root 'frontend\.vite'),
    (Join-Path $Root 'backend\evaluate\_pilot5'),
    (Join-Path $Root 'chroma_data'),           # 根目录空占位
    (Join-Path $Root 'data')                   # 根目录空占位
)
# 这些目录名在任何层级都只可能是运行期垃圾，按裸名排除是安全的
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

Write-Host "[1/4] 复制交付内容（按路径排除大资源与开发垃圾）..."
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

# 必须存在（缺失 = 交付包跑不起来）
Check-Present 'backend\main.py'                                     '后端入口'
Check-Present 'backend\models\__init__.py'                          'SQLAlchemy 模型源码包'
Check-Present 'backend\ai\rag\resources\contract_templates_source.json' '建库源（RAG 范本）'
Check-Present 'frontend\package.json'                               '前端工程'
Check-Present 'frontend\src'                                        '前端源码'
Check-Present 'start.bat'                                           '本地启动入口'
Check-Present 'prepare.bat'                                         '首次准备入口'
Check-Present 'scripts\start_backend.bat'                           '内部后端启动器'
Check-Present 'scripts\start_frontend.bat'                          '内部前端启动器'
Check-Present 'scripts\check_rag_ready.py'                          'RAG 就绪性检查'
Check-Present 'scripts\prepare_model.py'                            '模型准备脚本'
Check-Present 'Dockerfile.api'                                      'Docker 后端'
Check-Present 'Dockerfile.web'                                      'Docker 前端'
Check-Present 'docker-compose.yml'                                  'Docker 编排'
Check-Present 'README.md'                                           '交付说明'

# 必须不存在（安全与体积）
Check-Absent '.env'                       '含真实 API Key / SECRET_KEY'
Check-Absent 'backend\contract.db'        '真实数据库'
Check-Absent 'backend\data'               '真实合同与运行数据'
Check-Absent 'backend\venv'               '虚拟环境'
Check-Absent 'venv'                       '虚拟环境'
Check-Absent 'frontend\node_modules'      '前端依赖'
Check-Absent 'models'                     'embedding 模型（约 390MB，超校赛上限）'
Check-Absent 'backend\chroma_data'        '预构建向量库（约 45MB，超校赛上限）'
Check-Absent '.git'                       '版本库历史'
Check-Absent '.idea'                      'IDE 配置'
Check-Absent 'sketches'                   '页面草稿'
Check-Absent '答辩PPT'                    '答辩材料（单独提交）'

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
Write-Host "[3/4] 生成 ZIP ..."
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
$zipBytes  = (Get-Item $ZipPath).Length
$zipMB     = [math]::Round($zipBytes / 1MB, 2)
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

$over = $zipMB -gt $LimitMB
if ($over) {
    Write-Host "============================================================"
    Write-Host "  [失败] 交付包超过校赛上限！" -ForegroundColor Red
    Write-Host "    上限 : $LimitMB MB"
    Write-Host "    实际 : $zipMB MB"
    Write-Host "    超出 : $([math]::Round($zipMB - $LimitMB, 2)) MB"
    Write-Host ""
    Write-Host "  请检查上面清单里体积异常项。注意：不得通过删除源码或文档来压体积，"
    Write-Host "  应确认没有误把大资源（models / chroma_data / node_modules / venv）装入。"
    Write-Host "============================================================"
    if (-not $KeepStaging) { Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue }
    exit 1
}

Write-Host "============================================================"
Write-Host "  校赛小包生成成功" -ForegroundColor Green
Write-Host "    ZIP   : $ZipPath"
Write-Host "    大小  : $zipMB MB（上限 $LimitMB MB，余量 $([math]::Round($LimitMB - $zipMB, 2)) MB）"
Write-Host "    文件数: $fileCount"
Write-Host ""
Write-Host "  这个包【不含】模型与向量库，首次部署请先运行 prepare.bat（一次性、需联网），"
Write-Host "  之后再运行 start.bat。若要「解压即可离线运行」，请改用离线完整包。"
Write-Host ""
Write-Host "  命名提醒：校赛通知要求以「参赛学院+作品名称」命名，但省赛参赛指南明确"
Write-Host "  「打包文件中的文件名或目录名」不得出现参赛学校名称，否则取消资格。"
Write-Host "  两者冲突，故本脚本默认使用中性名。若确定只用于校赛内部提交，可执行："
Write-Host "    Rename-Item '$ZipPath' '信息工程学院+A24合同智能审核系统.zip'"
Write-Host "============================================================"

if (-not $KeepStaging) { Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue }
exit 0
