# scripts/installer/build_setup.ps1
# VOSR2-Lite 安装包构建（TD-011）：一条命令出 Setup.exe + SHA256SUMS.txt + provenance.json。
#
# 为什么这么写：
# - 安装包此前是「手工 makensis、产物 gitignore、无校验和、无 provenance」的 100 MB 文件，
#   出问题无法判断用户手上那份是不是发布者构建的那份。签名要证书（属主不做），
#   所以把「可溯源」补齐：记录 commit/tree/输入哈希 + 出包后自校验。
# - 工作树脏时默认拒绝出包（`provenance.py write` 里判），避免 provenance 与字节对不上。
#
# 用法（必须显式给数据卷，缺它会由 setup.nsi 的 !error 报出来）：
#   pwsh -File scripts/installer/build_setup.ps1 `
#     -DataVolume outputs/VOSR2Lite-Data.7z.001 `
#     -ShellExe  scripts/installer/VOSR2Lite.exe `
#     -Src7za    scripts/installer/7za.exe
#
# 产物：scripts/installer/VOSR2Lite-Setup-v<版本>.exe + SHA256SUMS.txt + *.provenance.json

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataVolume,
    [Parameter(Mandatory = $true)][string]$ShellExe,
    [string]$Src7za = '',
    [string]$Version = '',
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
$repo = (& git -C (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) rev-parse --show-toplevel).Trim()
$installerDir = Join-Path $repo 'scripts\installer'
$nsi = Join-Path $installerDir 'setup.nsi'

function Fail([string]$msg) { Write-Error $msg; exit 1 }

# ---- 1) 版本一致性：version.json 与 setup.nsi 的 APP_VERSION 必须同一个值 ----
if (-not $Version) {
    $versionJson = Join-Path $repo 'version.json'
    if (-not (Test-Path -LiteralPath $versionJson)) { Fail "找不到 version.json：$versionJson" }
    $Version = (Get-Content -LiteralPath $versionJson -Raw -Encoding utf8 | ConvertFrom-Json).version
}
if (-not $Version) { Fail '未能确定版本号（version.json 无 version 字段）' }

$nsiVersion = (Select-String -LiteralPath $nsi -Pattern '!define APP_VERSION "([^"]+)"' |
    Select-Object -First 1).Matches[0].Groups[1].Value
if ($nsiVersion -and $nsiVersion -ne $Version) {
    Fail "版本不一致：setup.nsi APP_VERSION=$nsiVersion，version.json=$Version（改一处会出两个版本号）"
}

# ---- 2) 输入存在性（缺一个都别往下走）----
foreach ($pair in @(@{ Path = $DataVolume; Name = '数据卷' }, @{ Path = $ShellExe; Name = '壳 exe' })) {
    if (-not (Test-Path -LiteralPath $pair.Path)) { Fail "缺少$($pair.Name)：$($pair.Path)" }
}
if (-not $Src7za) { $Src7za = Join-Path $installerDir '7za.exe' }
if (-not (Test-Path -LiteralPath $Src7za)) { Fail "缺少 7za.exe（用于内嵌解压数据卷）：$Src7za" }

$makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
if (-not $makensis) {
    $candidates = @("$env:ProgramFiles\NSIS\makensis.exe", "${env:ProgramFiles(x86)}\NSIS\makensis.exe")
    $makensis = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $makensis) { Fail '找不到 makensis（安装 NSIS 3.06+ 后重试）' }
$makensisVersion = (& $makensis /VERSION).Trim() -replace '^v', ''

# ---- 3) 时间戳与脏树检查 ----
# 实测 NSIS 3.10 对同一脚本 + 同一负载逐字节确定（PE 时间戳是 stub 常量、不随挂钟变），
# 所以这里设 SOURCE_DATE_EPOCH 只是给工具链留退路，复现保证来自「同一 commit + 同一输入」。
# 脏树则必须拦住：否则 provenance 里的 commit 与产物字节对不上，等于给伪造品背书。
if (-not $AllowDirty) {
    $dirty = (& git -C $repo status --porcelain --untracked-files=no)
    if ($dirty) {
        Fail "工作树有未提交改动，拒绝出包（provenance 的 commit 会与产物对不上）。如确需出包加 -AllowDirty。`n$dirty"
    }
}
$epoch = [int](& git -C $repo log -1 --format=%ct)
if (-not $epoch) { Fail '取不到 HEAD 提交时间（无法固定 SOURCE_DATE_EPOCH）' }
$env:SOURCE_DATE_EPOCH = "$epoch"

# ---- 4) 编译 ----
$outExe = Join-Path $installerDir "VOSR2Lite-Setup-v$Version.exe"
Push-Location $repo
try {
    & $makensis "/DAPP_VERSION=$Version" "/DSRC_DATA=$((Resolve-Path -LiteralPath $DataVolume).Path)" "/DSRC_7ZA=$((Resolve-Path -LiteralPath $Src7za).Path)" $nsi
    if ($LASTEXITCODE -ne 0) { Fail "makensis 失败，退出码 $LASTEXITCODE" }
} finally {
    Pop-Location
}
if (-not (Test-Path -LiteralPath $outExe)) { Fail "编译未见产物：$outExe" }

# ---- 5) 溯源 + 校验和（同一目录，随 Release 一起发）----
$provArgs = @(
    (Join-Path $installerDir 'provenance.py'), 'write',
    '--subject', $outExe,
    '--version', $Version,
    '--repo', $repo,
    '--tool-version', $makensisVersion,
    '--source-date-epoch', "$epoch",
    '--out-dir', $installerDir,
    '--input', $DataVolume,
    '--input', $ShellExe,
    '--input', $Src7za
)
if ($AllowDirty) { $provArgs += '--allow-dirty' }
& python -X utf8 @provArgs
if ($LASTEXITCODE -ne 0) { Fail "provenance 生成失败，退出码 $LASTEXITCODE" }

# 出包后立刻自校验一次：digest 与 provenance 必须自洽，否则这一包不许发
& python -X utf8 (Join-Path $installerDir 'provenance.py') verify `
    --base-dir $installerDir `
    --provenance "$outExe.provenance.json" `
    --checksums (Join-Path $installerDir 'SHA256SUMS.txt')
if ($LASTEXITCODE -ne 0) { Fail '自校验失败：产物与 provenance / SHA256SUMS 不一致' }

Write-Host "完成：$outExe"
Write-Host "校验和：$(Join-Path $installerDir 'SHA256SUMS.txt')"
