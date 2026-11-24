; VOSR2 Lite 桌面版 NSIS 安装器
; 编译：makensis /DAPP_VERSION=0.2.1 -DSRC_7ZA="C:\Program Files\7-Zip\7z.exe" setup.nsi
; 产物：VOSR2Lite-Setup-v<版本>.exe（需与数据分卷 VOSR2Lite-Data.7z.001.. 同目录分发）
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "StrFunc.nsh"
; CLI 全局命令：PATH 增删需要字符串搜索/替换。安装段与卸载段各用一套函数
; （StrFunc 的 un 版本必须单独 Using，且不能在同一个宏里混用 install/uninstall 调用）。
${Using:StrFunc} StrStr
${Using:StrFunc} UnStrStr
${Using:StrFunc} UnStrRep

!ifndef APP_VERSION
  !define APP_VERSION "0.4.3"
!endif

Name "VOSR2 Lite 桌面版"
OutFile "VOSR2Lite-Setup-v${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\VOSR2Lite"
InstallDirRegKey HKCU "Software\VOSR2Lite" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
CRCCheck on
BrandingText "VOSR2 Lite"

!define DATA_PREFIX "VOSR2Lite-Data.7z"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\VOSR2Lite"

; 图标路径相对 setup.nsi 所在目录（scripts/installer/），克隆后无需改路径即可编译
!ifndef APP_ICON
  !define APP_ICON "..\..\desktop\src-tauri\icons\icon.ico"
!endif
; 7za/7z 由编译机提供（不纳入仓库版本控制），用 -DSRC_7ZA=... 指定
!ifndef SRC_7ZA
  !define SRC_7ZA "7za.exe"
!endif
!ifndef SRC_DATA
  !error "单文件安装包：需用 -DSRC_DATA=<数据卷 .7z.001 路径> 内嵌数据分卷"
!endif

Var StartMenuFolder
Var TOOLSDIR
Var HAD_USER_CONFIG

; ---------- 终止运行中的 VOSR2 Lite（壳 + 其 Python 子进程）----------
!macro KillRunning
  DetailPrint "检测到 VOSR2 Lite 正在运行，正在自动终止..."
  nsExec::ExecToLog 'taskkill /f /im VOSR2Lite.exe'
  Sleep 1500
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$TOOLSDIR\killvosr2lite.ps1"'
  Sleep 1500
!macroend

; ---------- CLI 全局命令：把 $INSTDIR 追加到当前用户 PATH（幂等）----------
; 无参数：直接使用 $INSTDIR。函数内部用 $0/$1/$2/$3 须自行压栈保存。
; 只在 PATH 中不存在该条目时追加，避免重复安装/升级产生重复项。
Function AddToUserPath
  Push $0
  Push $1
  Push $2
  Push $3
  ReadRegStr $1 HKCU "Environment" "Path"
  ${StrStr} $2 "$1" "$INSTDIR"
  ${If} $2 == ""
    ${If} $1 == ""
      StrCpy $1 "$INSTDIR"
    ${Else}
      ; 避免前一条目无尾随分号时粘连
      StrCpy $3 "$1" 1 -1
      ${If} $3 == ";"
        StrCpy $1 "$1$INSTDIR"
      ${Else}
        StrCpy $1 "$1;$INSTDIR"
      ${EndIf}
    ${EndIf}
    WriteRegExpandStr HKCU "Environment" "Path" "$1"
    ; 广播环境变量变更，让新开的终端/AI agent 立即看到 CLI 命令
    SendMessage ${HWND_BROADCAST} ${WM_WININICHANGE} 0 "STR:Environment" /TIMEOUT=5000
    DetailPrint "已将 CLI 命令目录加入当前用户 PATH: $INSTDIR"
  ${Else}
    DetailPrint "CLI 命令目录已在当前用户 PATH 中，跳过"
  ${EndIf}
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; ---------- 卸载：从当前用户 PATH 精确移除 $INSTDIR 条目 ----------
Function un.RemoveFromUserPath
  Push $0
  Push $1
  Push $2
  Push $3
  ReadRegStr $1 HKCU "Environment" "Path"
  ${UnStrStr} $2 "$1" "$INSTDIR"
  ${IfNot} $2 == ""
    ; 先按 ";<entry>" 与 "<entry>;" 两种粘连清理，再兜底精确替换，最后折叠重复分号
    ${UnStrRep} $1 "$1" ";$INSTDIR" ""
    ${UnStrRep} $1 "$1" "$INSTDIR;" ""
    ${UnStrRep} $1 "$1" "$INSTDIR" ""
    ${UnStrRep} $1 "$1" ";;" ";"
    ${UnStrRep} $1 "$1" ";;" ";"
    ; 去掉首尾分号
    StrCpy $3 "$1" 1
    ${If} $3 == ";"
      StrCpy $1 "$1" "" 1
    ${EndIf}
    StrCpy $3 "$1" 1 -1
    ${If} $3 == ";"
      StrCpy $1 "$1" -1
    ${EndIf}
    WriteRegExpandStr HKCU "Environment" "Path" "$1"
    SendMessage ${HWND_BROADCAST} ${WM_WININICHANGE} 0 "STR:Environment" /TIMEOUT=5000
    DetailPrint "已从当前用户 PATH 移除 CLI 命令目录: $INSTDIR"
  ${EndIf}
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; ---------- MUI 页面 ----------
!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_STARTMENU "Application" $StartMenuFolder
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\VOSR2Lite.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 VOSR2 Lite"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; ---------- 安装 ----------
Section "VOSR2 Lite 桌面版" SEC_APP
  ; 自建临时工具目录（不依赖 $PLUGINSDIR：NSIS 3.10 实测该变量可能为空）
  StrCpy $TOOLSDIR "$TEMP\vosr2lite-tools"
  RMDir /r "$TOOLSDIR"
  CreateDirectory "$TOOLSDIR"
  SetOutPath "$TOOLSDIR"
  ; 统一嵌入名为 7za.exe：编译机上的 7-Zip 可能是 7z.exe（需 7z.dll）也可能是独立的
  ; 7za.exe，下面的 ExecWait 只认 7za.exe。改名嵌入可避免「文件在但进程起不来，
  ; ExecWait 拿到空错误码」这种不报错也不成功的安装失败（v0.2.1 真机踩过）。
  File /oname=7za.exe "${SRC_7ZA}"
  ; 单文件安装包：数据分卷直接内嵌（瘦包已不带环境/模型，体积可接受）
  File /oname=${DATA_PREFIX}.001 "${SRC_DATA}"
  File "killvosr2lite.ps1"

  !insertmacro KillRunning

  ; 覆盖安装保护：数据卷自带默认 config.yaml，7za 解压会覆盖用户改过的版本。
  ; config.yaml 承载 CSRF / Basic Auth / 限流等安全开关，重置会让老用户配置失效。
  ; 解压前若旧配置存在则备份，解压后还原（全新安装无旧文件，直接用数据卷默认值）。
  StrCpy $HAD_USER_CONFIG "0"
  ${If} ${FileExists} "$INSTDIR\app\config.yaml"
    CopyFiles /SILENT "$INSTDIR\app\config.yaml" "$TOOLSDIR\config.yaml.user"
    StrCpy $HAD_USER_CONFIG "1"
    DetailPrint "已备份用户配置 config.yaml（覆盖安装后还原）"
  ${EndIf}

  ; 校验数据分卷存在（数据卷含 runtime/ + model/ + app/ 基线）
  ${IfNot} ${FileExists} "$TOOLSDIR\${DATA_PREFIX}.001"
    MessageBox MB_ICONSTOP "缺少数据分卷 ${DATA_PREFIX}.001。$\n请确认 setup.exe 与全部数据分卷（.001/.002/...）位于同一目录。"
    Abort
  ${EndIf}

  ; 创建安装目录并解压数据分卷
  SetOutPath "$INSTDIR"
  DetailPrint "正在解压数据分卷（体积大，视磁盘速度需数分钟，请勿关闭窗口）..."
  ExecWait '"$TOOLSDIR\7za.exe" x "$TOOLSDIR\${DATA_PREFIX}.001" -o"$INSTDIR" -y -bso0 -bsp0' $0
  ; 释放临时工具（内嵌数据卷已解出，避免占用 %TEMP%）
  Delete "$TOOLSDIR\${DATA_PREFIX}.001"
  Delete "$TOOLSDIR\7za.exe"
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "数据解压失败（7z 错误码 $0）。$\n请检查：1) 磁盘空间是否充足；2) 数据分卷是否完整（SHA256 校验见随附 SHA256SUMS）。"
    Abort
  ${EndIf}

  ; 覆盖安装保护：还原用户配置（备份仅在解压前存在旧 config.yaml 时产生）
  ${If} $HAD_USER_CONFIG == "1"
    CopyFiles /SILENT "$TOOLSDIR\config.yaml.user" "$INSTDIR\app\config.yaml"
    Delete "$TOOLSDIR\config.yaml.user"
    DetailPrint "已还原用户配置 config.yaml"
  ${EndIf}

  ; 数据卷是旧基线，此处用安装器内嵌文件覆盖为最新壳/版本元数据（壳更新无需重打数据卷）
  SetOutPath "$INSTDIR"
  File "VOSR2Lite.exe"
  ; CLI 启动器：内嵌 shim + 注册当前用户 PATH，任意目录可直接敲 vosr2
  File "vosr2.cmd"
  Call AddToUserPath
  SetOutPath "$INSTDIR\app"
  File "..\..\version.json"

  ${IfNot} ${FileExists} "$INSTDIR\VOSR2Lite.exe"
    MessageBox MB_ICONSTOP "安装异常：未找到 VOSR2Lite.exe，安装可能不完整。"
    Abort
  ${EndIf}
  ${IfNot} ${FileExists} "$INSTDIR\app\start_portable.py"
    MessageBox MB_ICONSTOP "安装异常：未找到 app\start_portable.py，数据分卷可能不完整。"
    Abort
  ${EndIf}

  ; 快捷方式（开始菜单 + 桌面）
  !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
    CreateDirectory "$SMPROGRAMS\$StartMenuFolder"
    CreateShortCut "$SMPROGRAMS\$StartMenuFolder\VOSR2 Lite.lnk" "$INSTDIR\VOSR2Lite.exe"
    ; CLI 快捷方式：打开命令行窗口并运行 shim，方便发现 CLI
    CreateShortCut "$SMPROGRAMS\$StartMenuFolder\VOSR2 Lite CLI.lnk" "$SYSDIR\cmd.exe" '/k "$INSTDIR\vosr2.cmd" --help'
  !insertmacro MUI_STARTMENU_WRITE_END
  CreateShortCut "$DESKTOP\VOSR2 Lite.lnk" "$INSTDIR\VOSR2Lite.exe"

  ; 注册表（卸载信息）
  WriteRegStr HKCU "Software\VOSR2Lite" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "VOSR2 Lite 桌面版"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "ReSerendipity"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\VOSR2Lite.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; ---------- 更新器自动重启（自更新七步的第 7 步）----------
  ; 应用内更新器下载本安装包后以 `/S /RELAUNCH` 启动，安装完自动拉起主程序，
  ; 普通安装不带该标志，不受影响。用 $CMDLINE + StrStr 检测为主程序名留通用性。
  ${StrStr} $0 "$CMDLINE" "/RELAUNCH"
  ${If} $0 != ""
    DetailPrint "更新安装完成，正在重新启动 VOSR2 Lite…"
    Exec '"$INSTDIR\VOSR2Lite.exe"'
  ${EndIf}
  RMDir /r "$TOOLSDIR"
SectionEnd

; ---------- 版本信息 ----------
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "VOSR2 Lite 桌面版"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileDescription" "VOSR2 Lite 桌面版安装程序"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "Apache-2.0 · ReSerendipity"

; ---------- 卸载 ----------
Section "Uninstall"
  StrCpy $TOOLSDIR "$TEMP\vosr2lite-tools"
  RMDir /r "$TOOLSDIR"
  CreateDirectory "$TOOLSDIR"
  SetOutPath "$TOOLSDIR"
  File "killvosr2lite.ps1"

  !insertmacro KillRunning

  Delete "$DESKTOP\VOSR2 Lite.lnk"
  !insertmacro MUI_STARTMENU_GETFOLDER Application $StartMenuFolder
  Delete "$SMPROGRAMS\$StartMenuFolder\VOSR2 Lite.lnk"
  Delete "$SMPROGRAMS\$StartMenuFolder\VOSR2 Lite CLI.lnk"
  RMDir "$SMPROGRAMS\$StartMenuFolder"
  ; CLI 全局命令：从当前用户 PATH 精确移除安装目录（先删注册表键前处理）
  Call un.RemoveFromUserPath
  DeleteRegKey HKCU "Software\VOSR2Lite"
  DeleteRegKey HKCU "${UNINST_KEY}"

  ; 删除安装目录（循环重试 30 秒，等待句柄释放）
  RMDir /r "$INSTDIR"
  StrCpy $R0 0
  ${DoWhile} ${FileExists} "$INSTDIR"
    IntOp $R0 $R0 + 1
    ${If} $R0 >= 30
      ${Break}
    ${EndIf}
    Sleep 1000
    RMDir /r "$INSTDIR"
  ${Loop}
  ; 兜底：卸载器退出后由独立 cmd 延迟清理
  nsExec::Exec 'cmd /c ping -n 6 127.0.0.1 >nul & rd /s /q "$INSTDIR"'
  RMDir /r "$TOOLSDIR"
SectionEnd

