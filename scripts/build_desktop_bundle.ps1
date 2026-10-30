#Requires -Version 5.1
# scripts/build_desktop_bundle.ps1
# 构建 VOSR2-Lite 桌面版发布资产（供 NSIS 安装器 scripts/installer/setup.nsi 分发）。
#
# 产物（默认输出到 dist\desktop-release\）：
#   VOSR2Lite-Data.7z.001..NNN  数据卷：app/（应用代码 + 便携 Python 运行时 + 模型）
#   app-v<ver>.zip              增量包：仅应用代码（壳内更新器下载，不含运行时/模型）
#   app-v<ver>.zip.sha256       增量包校验和（壳内更新器用它校验）
#   SHA256SUMS.txt              全部产物清单
#   manifest.json               产物元信息（分卷清单 / 体积 / 哈希）
#   upload-list.txt             待上传文件列表
#
# 数据卷解压后布局（与壳 python_process.rs 的 resolve_app_dir / resolve_runtime_dir
# 以及 updater.rs 的 PRESERVE_TOP_DIRS 一一对应，改动前先看那三处）：
#   <InstallDir>\app\
#     start_portable.py   壳启动的入口（scripts/start_portable.py 复制而来）
#     version.json        本地版本标识，缺失会导致更新被静默跳过
#     app\ vosr\          应用代码包
#     config.yaml         保留用户修改（PRESERVE_ROOT_FILES），增量包不含它
#     runtime\python.exe  便携解释器（WinPython 的 python-3.12.10.amd64 内容）
#     model\              权重（config.yaml 的 model.pretrained_dir 相对 PROJECT_ROOT 解析）
#     data\ outputs\ logs\ 运行态目录（增量更新时整体保留）
#
# 用法：
#   .\scripts\build_desktop_bundle.ps1                                  # 全量（含运行时准备与装依赖）
#   .\scripts\build_desktop_bundle.ps1 -SkipIncremental                 # 只打数据卷
#   .\scripts\build_desktop_bundle.ps1 -SkipDataVolume                  # 只打增量包
#   .\scripts\build_desktop_bundle.ps1 -RuntimeDir D:\WPy64-312101 -SkipAutoPrepare -SkipDeps

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$Root = '',
    [string]$OutDir = '',
    [string]$StagingDir = '',

    # 便携解释器：给 -RuntimeDir 时直接用（须含 python.exe）；否则自动下载 WinPython 并装依赖
    [string]$RuntimeDir = '',
    [string]$WinPythonUrl = 'https://github.com/winpython/winpython/releases/download/16.5.20250614/Winpython64-3.12.10.1dotb4.exe',
    # 官方 release 资产 SHA256（与家族仓 SeedVR2 / TTS 同一版本口径；升级 WinPython 时必须同步改这两处）
    [string]$WinPythonSha256 = '4061f0e936289ca1df48fc8e7357a4c30e6010f053ffd2f986f518a09bbf03e8',
    [string[]]$WinPythonMirrorPrefixes = @('https://gh-proxy.com/'),

    # torch：本仓要求 cu128（RTX 50 系 sm_120；cu121 不支持），与 requirements.txt 一致
    [string]$TorchIndexUrl = 'https://download.pytorch.org/whl/cu128',
    [string]$TorchVersion = '2.10.0',
    [string]$TorchvisionVersion = '0.25.0',

    # 模型：默认自动取 model/ 下的权重目录（排除 torch_cache 这一 hub 缓存）
    [string]$ModelDir = '',
    [string[]]$ModelDirs = @(),
    [switch]$IncludeTorchHubCache,

    # 分卷上限：支持 "1900MB" / "1.5GB" / 纯字节数；留空用默认 1900MB
    [string]$MaxPartBytes = '',
    [ValidateRange(0, 9)][int]$Level = 1,
    [double]$MinFreeGb = 0,

    [switch]$SkipAutoPrepare,
    [switch]$SkipDeps,
    [switch]$SkipDataVolume,
    [switch]$SkipIncremental,
    [switch]$KeepStaging,
    [switch]$KeepArchive,
    [switch]$SkipSizeGate
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')

# ---------------------------------------------------------------- 常量 ----
$DataArchiveName = 'VOSR2Lite-Data.7z'
$PayloadAppName = 'app'

# 应用代码：随增量包与数据卷一起分发
$CodeIncludeDirs = @('app', 'vosr')
$CodeIncludeFiles = @('requirements.txt', 'README.md', 'LICENSE', 'NOTICE', 'CHANGELOG.md')
# 打包时排除：缓存 / 虚拟环境 / 测试 / 运行态数据 / 构建产物 / 私钥
$CodeExcludePatterns = @(
    '__pycache__\*', '*.pyc', '*.pyo', '.pytest_cache\*', '.mypy_cache\*', '.ruff_cache\*',
    '.venv\*', 'venv\*', 'tests\*', 'dist\*', 'build\*', '.git\*',
    'data\cache\*', 'data\uploads\*', 'outputs\*', 'logs\*', '*.log',
    '*.db', '*.db-wal', '*.db-shm', '*.bak', '*.bak.*',
    'desktop\*', 'scripts\*', 'docs\*', 'model\*', 'runtime\*',
    '.env', '.env.*', 'tauri.key', '*.pem'
)
# 增量包额外排除：这些由旧安装保留（updater.rs 的 PRESERVE_ROOT_FILES / PRESERVE_TOP_DIRS），
# 且体积大（运行时 / 模型）——用户修改优先于新版默认，故不随增量包分发。
$IncrementalExcludePatterns = $CodeExcludePatterns + @('config.yaml', 'runtime\*', 'model\*', 'version.json.bak')

# ------------------------------------------------------------ 版本解析 ----
function Get-Vosr2ProjectVersion {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $vfile = Join-Path $ProjectRoot 'version.json'
    if (Test-Path -LiteralPath $vfile) {
        $data = Read-Vosr2Json -Path $vfile
        if ($data.version) {
            return [string]$data.version
        }
    }
    throw "无法从 version.json 解析版本号，请显式传 -Version"
}

# ------------------------------------------------------- 运行时自动准备 ----
function Start-Vosr2RuntimePrepare {
    param(
        [Parameter(Mandatory = $true)][string]$WorkDir,
        [Parameter(Mandatory = $true)][string]$Url
    )
    $sevenZip = Find-Vosr2SevenZip
    $exePath = Join-Path $WorkDir 'WinPython.exe'
    if (-not (Test-Path -LiteralPath $exePath)) {
        $verified = $false
        if (Test-Path -LiteralPath $Url -PathType Leaf) {
            Write-Host "  使用本地 WinPython 安装器：$Url"
            Copy-Item -LiteralPath $Url -Destination $exePath -Force
        } else {
            $candidates = @($Url)
            foreach ($prefix in $WinPythonMirrorPrefixes) {
                if ($prefix) { $candidates += ($prefix.TrimEnd('/') + '/' + $Url) }
            }
            $lastError = '未尝试任何来源'
            $chosen = ''
            foreach ($candidate in $candidates) {
                Write-Host "  下载 WinPython 便携解释器：$candidate"
                try {
                    Invoke-WebRequest -Uri $candidate -OutFile $exePath -UseBasicParsing -TimeoutSec 900
                } catch {
                    $lastError = $_.Exception.Message
                    if (Test-Path -LiteralPath $exePath) { Remove-Item -LiteralPath $exePath -Force }
                    Write-Host "    下载失败：$lastError —— 切换下一来源"
                    continue
                }
                if (-not $WinPythonSha256) { $chosen = $candidate; break }
                $actual = Get-Vosr2FileSha256 -Path $exePath
                if ($actual -eq $WinPythonSha256.ToLowerInvariant()) {
                    $chosen = $candidate
                    $verified = $true
                    break
                }
                $lastError = "SHA256 不符（期望 $($WinPythonSha256.ToLowerInvariant())，实际 $actual）"
                Remove-Item -LiteralPath $exePath -Force
                Write-Host "    $lastError —— 切换下一来源"
            }
            if (-not (Test-Path -LiteralPath $exePath)) {
                throw "WinPython 便携解释器全部 $($candidates.Count) 个来源不可用。离线兜底：把安装器预先下载到本地，再以 -WinPythonUrl <本地路径> 重试。最后错误：$lastError"
            }
            Write-Host "  WinPython 安装器就绪（来源：$chosen）"
        }
        if (-not $verified -and $WinPythonSha256) {
            $actual = Get-Vosr2FileSha256 -Path $exePath
            if ($actual -ne $WinPythonSha256.ToLowerInvariant()) {
                throw "WinPython 安装器 SHA256 不符（期望 $WinPythonSha256，实际 $actual）——下载损坏或上游被篡改"
            }
            Write-Host "  WinPython 安装器 SHA256 校验通过"
        }
    }
    $extractDir = Join-Path $WorkDir 'wp'
    if (-not (Test-Path -LiteralPath $extractDir)) {
        New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
        Write-Host "  解压 WinPython ..."
        if ($sevenZip) {
            $res = Invoke-Vosr2Native -Exe $sevenZip -Arguments @('x', $exePath, "-o$extractDir", '-y')
            if ($res.ExitCode -ne 0) {
                throw "7z 解压 WinPython 失败，退出码 $($res.ExitCode)"
            }
        } else {
            $psi = Start-Process -FilePath $exePath -ArgumentList '/S', "/D=$extractDir" -PassThru -Wait
            if ($psi.ExitCode -ne 0) {
                throw "WinPython 自解压失败，退出码 $($psi.ExitCode)（可安装 7-Zip 后重试）"
            }
        }
    }
    return (Resolve-Vosr2RuntimeRoot -Path $extractDir)
}

function Install-Vosr2RuntimeDeps {
    <#
        把运行依赖装进便携解释器。顺序是关键：
        1) 先装非 torch 依赖（默认 PyPI）；
        2) 再单独装 torch/torchvision —— 必须从 cu128 索引取，且要用 PIP_CONFIG_FILE=NUL
           屏蔽用户 pip 配置里的 extra-index-url：否则国内镜像的 +cpu 轮子会顶包，
           装出来 cuda_available=False（requirements.txt 顶部注释记录的实测坑）。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )
    $reqSrc = Join-Path $ProjectRoot 'requirements.txt'
    if (-not (Test-Path -LiteralPath $reqSrc)) {
        throw "缺少 requirements.txt：$reqSrc"
    }
    $reqNoTorch = Join-Path (Split-Path -Parent $PythonExe) 'requirements-notorch.txt'
    $lines = @()
    foreach ($line in [System.IO.File]::ReadAllLines($reqSrc)) {
        if ($line -match '^\s*(torch|torchvision|torchaudio)\b') {
            continue
        }
        $lines += $line
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($reqNoTorch, [string[]]$lines, $enc)

    Write-Host "  安装非 torch 依赖（默认 PyPI）..."
    $r = Invoke-Vosr2Native -Exe $PythonExe -Arguments @('-m', 'pip', 'install', '-r', $reqNoTorch, '--no-warn-script-location')
    if ($r.ExitCode -ne 0) {
        throw "安装非 torch 依赖失败（退出码 $($r.ExitCode)）：`n$($r.Text)"
    }

    Write-Host "  安装 torch $TorchVersion + torchvision $TorchvisionVersion（$TorchIndexUrl）..."
    $prevCfg = $env:PIP_CONFIG_FILE
    $env:PIP_CONFIG_FILE = 'NUL'
    try {
        $r = Invoke-Vosr2Native -Exe $PythonExe -Arguments @('-m', 'pip', 'install',
            "torch==$TorchVersion", "torchvision==$TorchvisionVersion",
            '--index-url', $TorchIndexUrl, '--no-warn-script-location')
    } finally {
        $env:PIP_CONFIG_FILE = $prevCfg
    }
    if ($r.ExitCode -ne 0) {
        throw "安装 torch(cu128) 失败（退出码 $($r.ExitCode)）：`n$($r.Text)"
    }
    Remove-Item -LiteralPath $reqNoTorch -Force -ErrorAction SilentlyContinue
}

function Test-Vosr2RuntimeTorch {
    param([Parameter(Mandatory = $true)][string]$PythonExe)
    $code = 'import torch;print(torch.__version__, torch.cuda.is_available())'
    $r = Invoke-Vosr2Native -Exe $PythonExe -Arguments @('-c', $code)
    if ($r.ExitCode -ne 0) {
        Write-Warning "便携解释器导入 torch 失败（退出码 $($r.ExitCode)）：$($r.Text)"
        return $false
    }
    Write-Host "  torch 自检：$($r.Text.Trim())"
    return $true
}

# ------------------------------------------------------------ 载荷组装 ----
function New-Vosr2AppPayload {
    <#
        组装 <payload>\app\。$WithHeavy = $true 时一并放入 runtime/ 与 model/（数据卷）；
        $false 时只放代码（增量包）。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$PayloadDir,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Ver,
        [Parameter(Mandatory = $true)][bool]$WithHeavy,
        [string]$Runtime,
        [string]$ModelDirectory,
        [string[]]$ModelSubDirs
    )
    $appDir = Join-Path $PayloadDir $PayloadAppName
    New-Item -ItemType Directory -Path $appDir -Force | Out-Null
    $exclude = if ($WithHeavy) { $CodeExcludePatterns } else { $IncrementalExcludePatterns }
    $files = 0
    $bytes = [long]0

    foreach ($d in $CodeIncludeDirs) {
        $src = Join-Path $ProjectRoot $d
        if (-not (Test-Path -LiteralPath $src)) {
            throw "缺少应用目录 $d（$src）"
        }
        $st = Copy-Vosr2Tree -Source $src -Dest (Join-Path $appDir $d) -ExcludePatterns $exclude
        $files += $st.Files; $bytes += $st.Bytes
        Write-Host ("    · {0,-10} {1,6} 文件 / {2}" -f $d, $st.Files, (Format-Vosr2Size $st.Bytes))
    }

    # 启动脚本：开发态在 scripts/ 下，打包态必须落在 app/ 根（壳按此路径查找）
    $startSrc = Join-Path $ProjectRoot 'scripts\start_portable.py'
    if (-not (Test-Path -LiteralPath $startSrc)) {
        throw "缺少 scripts\start_portable.py"
    }
    Copy-Item -LiteralPath $startSrc -Destination (Join-Path $appDir 'start_portable.py') -Force
    $files += 1

    # 版本标识：壳依赖它识别本地版本，缺失会导致更新被静默跳过
    $verSrc = Join-Path $ProjectRoot 'version.json'
    if (-not (Test-Path -LiteralPath $verSrc)) {
        throw "缺少根 version.json"
    }
    Copy-Item -LiteralPath $verSrc -Destination (Join-Path $appDir 'version.json') -Force
    $files += 1

    # 配置文件：数据卷需要（首次安装的默认值）；增量包刻意不含（保留用户修改）
    if ($WithHeavy) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot 'config.yaml') -Destination (Join-Path $appDir 'config.yaml') -Force
        $files += 1
    }

    foreach ($f in $CodeIncludeFiles) {
        $src = Join-Path $ProjectRoot $f
        if (-not (Test-Path -LiteralPath $src)) {
            continue
        }
        $st = Copy-Vosr2Tree -Source $src -Dest $appDir -ExcludePatterns $exclude
        $files += $st.Files; $bytes += $st.Bytes
    }

    if ($WithHeavy) {
        # 运行时：壳只认 <app>\runtime\python.exe，故把 WinPython 的 python-3.12.x.amd64 内容搬成 runtime/
        if (-not $Runtime) {
            throw "数据卷需要便携运行时：请提供 -RuntimeDir 或去掉 -SkipAutoPrepare"
        }
        $pyInfo = Resolve-Vosr2RuntimeRoot -Path $Runtime
        $st = Copy-Vosr2Tree -Source $pyInfo.PythonDir -Dest (Join-Path $appDir 'runtime') `
            -ExcludePatterns @('pip-log.txt', '*.typecheck.log', 'WINPYTHON_*', 'notebooks\*')
        Write-Host ("    · {0,-10} {1,6} 文件 / {2}" -f 'runtime', $st.Files, (Format-Vosr2Size $st.Bytes))
        $files += $st.Files; $bytes += $st.Bytes

        if ($ModelDirectory -and (Test-Path -LiteralPath $ModelDirectory)) {
            foreach ($sub in $ModelSubDirs) {
                $src = Join-Path $ModelDirectory $sub
                if (-not (Test-Path -LiteralPath $src)) {
                    Write-Warning "跳过不存在的模型目录：$sub"
                    continue
                }
                $st = Copy-Vosr2Tree -Source $src -Dest (Join-Path $appDir (Join-Path 'model' $sub)) `
                    -ExcludePatterns @('*.lock', '*.bin.part', '.cache\*')
                Write-Host ("    · {0,-10} {1,6} 文件 / {2}" -f "model/$sub", $st.Files, (Format-Vosr2Size $st.Bytes))
                $files += $st.Files; $bytes += $st.Bytes
            }
        }

        # 运行态空目录骨架：PRESERVE_TOP_DIRS 会在更新时整体保留，首次安装需先存在
        foreach ($d in @('data\cache', 'data\uploads', 'outputs', 'logs')) {
            New-Item -ItemType Directory -Path (Join-Path $appDir $d) -Force | Out-Null
        }
    }

    return [pscustomobject]@{ Files = $files; Bytes = $bytes; AppDir = $appDir }
}

function Assert-Vosr2PayloadLayout {
    param(
        [Parameter(Mandatory = $true)][string]$AppDir,
        [Parameter(Mandatory = $true)][bool]$WithHeavy
    )
    $must = @('start_portable.py', 'version.json')
    if ($WithHeavy) {
        $must += @('config.yaml', 'runtime\python.exe', 'app\integrated_app\app_server.py')
    }
    foreach ($rel in $must) {
        $p = Join-Path $AppDir $rel
        if (-not (Test-Path -LiteralPath $p)) {
            throw "载荷不完整，缺少 $rel（$p）"
        }
    }
}

# =========================================================== 主流程 ====
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Version) { $Version = Get-Vosr2ProjectVersion -ProjectRoot $Root }
if ($Version -notmatch '^\d+\.\d+\.\d+') {
    throw "非法版本号（须为 x.y.z）：$Version"
}
if (-not $OutDir) { $OutDir = Join-Path $Root 'dist\desktop-release' }
if (-not $StagingDir) { $StagingDir = Join-Path $Root 'dist\desktop-staging' }
$MaxPartBytes = ConvertTo-Vosr2Bytes -Value $MaxPartBytes
if ($MaxPartBytes -le 0) { $MaxPartBytes = Get-Vosr2DefaultMaxPart }
$Limit = Get-Vosr2GithubAssetLimit
if ($MaxPartBytes -gt $Limit) {
    throw "-MaxPartBytes ($MaxPartBytes) 超过 GitHub 单文件上限 ($Limit)"
}
if (-not $ModelDir) { $ModelDir = Join-Path $Root 'model' }

Write-Host ''
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " VOSR2-Lite 桌面发布包构建 v$Version" -ForegroundColor Cyan
Write-Host " 分卷上限：$(Format-Vosr2Size $MaxPartBytes)（GitHub 单文件上限 $(Format-Vosr2Size $Limit)）" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

# -------------------------------------------------- 模型目录自动识别 ----
if ($ModelDirs.Count -eq 0) {
    if (Test-Path -LiteralPath $ModelDir) {
        $ModelDirs = @(Get-ChildItem -LiteralPath $ModelDir -Force -Directory |
            Where-Object { $_.Name -ne 'torch_cache' -or $IncludeTorchHubCache } |
            ForEach-Object { $_.Name })
    }
    if ($ModelDirs.Count -eq 0) {
        Write-Warning "未在 $ModelDir 找到任何模型目录，数据卷将不含权重（仅壳可跑，首次推理需自行下载）"
    }
}
Write-Host " 模型目录：$($ModelDirs -join ', ')"

# -------------------------------------------------------- 运行时准备 ----
$needRuntime = -not $SkipDataVolume
if ($needRuntime -and -not $RuntimeDir -and $SkipAutoPrepare) {
    throw "数据卷需要便携运行时：请提供 -RuntimeDir，或去掉 -SkipAutoPrepare 让脚本自动下载 WinPython"
}
if ($needRuntime -and -not $RuntimeDir) {
    $prepDir = Join-Path $StagingDir '_prepare'
    New-Item -ItemType Directory -Path $prepDir -Force | Out-Null
    Write-Host "`n[准备] 便携 Python 运行时"
    $info = Start-Vosr2RuntimePrepare -WorkDir $prepDir -Url $WinPythonUrl
    $RuntimeDir = $info.PythonDir
    Write-Host "  解释器：$($info.PythonExe)"
}
if ($needRuntime -and -not $SkipDeps -and $RuntimeDir) {
    $pyInfo = Resolve-Vosr2RuntimeRoot -Path $RuntimeDir
    Write-Host "`n[准备] 安装运行依赖"
    Install-Vosr2RuntimeDeps -PythonExe $pyInfo.PythonExe -ProjectRoot $Root
    Test-Vosr2RuntimeTorch -PythonExe $pyInfo.PythonExe | Out-Null
}

# -------------------------------------------------------- 磁盘预检 ----
$rawBytes = [long]0
foreach ($sub in $ModelDirs) {
    $p = Join-Path $ModelDir $sub
    if (Test-Path -LiteralPath $p) {
        $rawBytes += [long](Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    }
}
if ($RuntimeDir -and (Test-Path -LiteralPath $RuntimeDir)) {
    $rawBytes += [long](Get-ChildItem -LiteralPath $RuntimeDir -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
}
# 峰值 ≈ 未压缩载荷 ×1.3：staging（同盘硬链接近乎 0）+ 归档 + 全部分卷同时落盘
$estimatedPeak = [math]::Round(($rawBytes * 1.3) / 1GB, 1)
if ($needRuntime -and -not $RuntimeDir) { $estimatedPeak += 4.0 }
$requiredFree = if ($MinFreeGb -gt 0) { [double]$MinFreeGb } else { [math]::Max($estimatedPeak, 2.0) }
Write-Host "`n[预检] 磁盘空间"
Assert-Vosr2DiskSpace -Path $OutDir -NeededGb $requiredFree | Out-Null
if ((Split-Path -Qualifier $StagingDir) -ne (Split-Path -Qualifier $OutDir)) {
    Assert-Vosr2DiskSpace -Path $StagingDir -NeededGb $requiredFree | Out-Null
}

$upload = @()
$volumeObjs = @()

# ---------------------------------------------------------- 增量包 ----
if (-not $SkipIncremental) {
    Write-Host "`n[增量包] 仅应用代码（不含运行时 / 模型 / 用户配置）" -ForegroundColor Yellow
    $incPayload = Join-Path $StagingDir 'incremental-payload'
    Remove-Vosr2TreeFast -Path $incPayload
    New-Item -ItemType Directory -Path $incPayload -Force | Out-Null
    $built = New-Vosr2AppPayload -PayloadDir $incPayload -ProjectRoot $Root -Ver $Version -WithHeavy $false
    Assert-Vosr2PayloadLayout -AppDir $built.AppDir -WithHeavy $false
    Assert-Vosr2NoForbiddenPayload -Path $incPayload
    Write-Host ("  载荷：{0} 文件 / {1}" -f $built.Files, (Format-Vosr2Size $built.Bytes))

    $zipBase = Join-Path $OutDir "app-v$Version"
    # 关键：SourceDir 必须是 app 目录本身而非其父目录——updater.rs 的 extract_zip_with
    # 把 zip 直接解到 app.new/，条目根就是 app/ 的内容（start_portable.py、app\、vosr\…）。
    # 若多包一层 app\，换载后会变成 app\app\app\integrated_app，壳找不到启动脚本。
    $arc = New-Vosr2Archive -SourceDir $built.AppDir -OutFile $zipBase -Format zip -Level 9
    $zipPath = $arc.Path
    $shaPath = "$zipPath.sha256"
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($shaPath, ((Get-Vosr2FileSha256 -Path $zipPath) + "  " + (Split-Path -Leaf $zipPath)), $enc)
    Write-Host ("  增量包：{0}（{1}）" -f (Split-Path -Leaf $zipPath), (Format-Vosr2Size $arc.Bytes))
    $upload += $zipPath
    $upload += $shaPath
    Remove-Vosr2TreeFast -Path $incPayload
}

# ---------------------------------------------------------- 数据卷 ----
if (-not $SkipDataVolume) {
    Write-Host "`n[数据卷] 应用 + 便携运行时 + 模型" -ForegroundColor Yellow
    $dataPayload = Join-Path $StagingDir 'data-payload'
    Remove-Vosr2TreeFast -Path $dataPayload
    New-Item -ItemType Directory -Path $dataPayload -Force | Out-Null
    $built = New-Vosr2AppPayload -PayloadDir $dataPayload -ProjectRoot $Root -Ver $Version -WithHeavy $true `
        -Runtime $RuntimeDir -ModelDirectory $ModelDir -ModelSubDirs $ModelDirs
    Assert-Vosr2PayloadLayout -AppDir $built.AppDir -WithHeavy $true
    Assert-Vosr2NoForbiddenPayload -Path $dataPayload
    Write-Host ("  载荷：{0} 文件 / {1}（未压缩）" -f $built.Files, (Format-Vosr2Size $built.Bytes))

    $archiveBase = Join-Path $StagingDir $DataArchiveName
    $arc = New-Vosr2Archive -SourceDir $dataPayload -OutFile $archiveBase -Format 7z -Level $Level
    Write-Host ("  归档：{0} → {1}" -f $arc.Format, (Format-Vosr2Size $arc.Bytes))

    $vols = @(Split-Vosr2FileIntoVolumes -File $arc.Path -MaxBytes $MaxPartBytes)
    foreach ($v in $vols) {
        $target = Join-Path $OutDir $v.Name
        Move-Item -LiteralPath $v.Path -Destination $target -Force
        $volumeObjs += [pscustomobject]@{ Index = $v.Index; Name = $v.Name; Bytes = $v.Bytes; Sha256 = $v.Sha256 }
        $upload += $target
        Write-Host ("    卷 {0}/{1}：{2}  {3}" -f $v.Index, $vols.Count, (Format-Vosr2Size $v.Bytes), $v.Name)
    }
    if (-not $KeepArchive) {
        Remove-Item -LiteralPath $arc.Path -Force -ErrorAction SilentlyContinue
    }
    Remove-Vosr2TreeFast -Path $dataPayload
}

# ------------------------------------------------------ SHA256SUMS ----
$sumTargets = @()
foreach ($p in $upload) {
    if (Test-Path -LiteralPath $p -PathType Leaf) { $sumTargets += $p }
}
if ($sumTargets.Count -gt 0) {
    $sumFile = Join-Path $OutDir 'SHA256SUMS.txt'
    Write-Vosr2Sha256Sums -Paths $sumTargets -OutFile $sumFile | Out-Null
    Write-Host "`n[清单] SHA256SUMS.txt（$($sumTargets.Count) 项）"
}

# ------------------------------------------------------ 体积门禁 ----
if (-not $SkipSizeGate -and $sumTargets.Count -gt 0) {
    $bad = @(Test-Vosr2AssetSizeGate -Paths $sumTargets)
    if ($bad.Count -gt 0) {
        foreach ($b in $bad) {
            Write-Host ("  [体积门禁] 违规：{0}（{1} 字节，上限 {2}，原因 {3}）" -f $b.Path, $b.Bytes, $b.Limit, $b.Reason) -ForegroundColor Red
        }
        throw "有 $($bad.Count) 个产物超过 GitHub 单文件上限，发布会被拒"
    }
    Write-Host "[体积门禁] 全部产物通过（每个文件均 ≤ $(Format-Vosr2Size $Limit)）"
}

# ------------------------------------------------------ manifest ----
$manifest = [ordered]@{
    schema              = 1
    product             = 'VOSR2-Lite-Desktop'
    app_version         = $Version
    arch                = 'win-x64'
    created_utc         = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    volume_limit_bytes  = [long]$Limit
    max_part_bytes      = [long]$MaxPartBytes
    installer           = 'VOSR2Lite-Setup-v' + $Version + '.exe'
    data_archive        = $DataArchiveName
    unpack_note         = '数据分卷必须与安装器同目录；安装器用内嵌 7za 解 VOSR2Lite-Data.7z.001 到安装目录'
    model_dirs          = @($ModelDirs)
    data_volumes        = @(
        foreach ($v in $volumeObjs) {
            [ordered]@{ index = $v.Index; file = $v.Name; bytes = [long]$v.Bytes; sha256 = $v.Sha256 }
        }
    )
    incremental_package = if ($SkipIncremental) { $null } else { "app-v$Version.zip" }
}
Write-Vosr2Json -Object $manifest -Path (Join-Path $OutDir 'manifest.json')

if ($sumFile -and (Test-Path -LiteralPath $sumFile)) { $upload += $sumFile }
$upload += (Join-Path $OutDir 'manifest.json')
[System.IO.File]::WriteAllLines((Join-Path $OutDir 'upload-list.txt'), [string[]]$upload)

if (-not $KeepStaging) {
    Remove-Vosr2TreeFast -Path $StagingDir
}

$totalBytes = [long]0
foreach ($p in $sumTargets) { $totalBytes += [long](Get-Item -LiteralPath $p).Length }
Write-Host ''
Write-Host "[完成] 产物目录：$OutDir" -ForegroundColor Green
Write-Host ("[完成] 共 {0} 个待上传文件，合计 {1}" -f $upload.Count, (Format-Vosr2Size $totalBytes)) -ForegroundColor Green
Write-Host "[下一步] 编译安装器：cd scripts\installer; makensis /DAPP_VERSION=$Version -DSRC_7ZA=... setup.nsi" -ForegroundColor Green
