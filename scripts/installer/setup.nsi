; VOSR2 Lite 桌面版 NSIS 安装器
; 编译：makensis /DAPP_VERSION=0.2.1 -DSRC_7ZA="C:\Program Files\7-Zip\7z.exe" setup.nsi
; 产物：VOSR2Lite-Setup-v<版本>.exe（需与数据分卷 VOSR2Lite-Data.7z.001.. 同目录分发）
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.2.1"
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

Var StartMenuFolder
Var TOOLSDIR

; ---------- 终止运行中的 VOSR2 Lite（壳 + 其 Python 子进程）----------
!macro KillRunning
  DetailPrint "检测到 VOSR2 Lite 正在运行，正在自动终止..."
  nsExec::ExecToLog 'taskkill /f /im VOSR2Lite.exe'
  Sleep 1500
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$TOOLSDIR\killvosr2lite.ps1"'
  Sleep 1500
!macroend

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
  File "${SRC_7ZA}"
  File "killvosr2lite.ps1"

  !insertmacro KillRunning

  ; 校验数据分卷存在（数据卷含 runtime/ + model/ + app/ 基线）
  ${IfNot} ${FileExists} "$EXEDIR\${DATA_PREFIX}.001"
    MessageBox MB_ICONSTOP "缺少数据分卷 ${DATA_PREFIX}.001。$\n请确认 setup.exe 与全部数据分卷（.001/.002/...）位于同一目录。"
    Abort
  ${EndIf}

  ; 创建安装目录并解压数据分卷
  SetOutPath "$INSTDIR"
  DetailPrint "正在解压数据分卷（体积大，视磁盘速度需数分钟，请勿关闭窗口）..."
  ExecWait '"$TOOLSDIR\7za.exe" x "$EXEDIR\${DATA_PREFIX}.001" -o"$INSTDIR" -y -bso0 -bsp0' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "数据解压失败（7z 错误码 $0）。$\n请检查：1) 磁盘空间是否充足；2) 数据分卷是否完整（SHA256 校验见随附 SHA256SUMS）。"
    Abort
  ${EndIf}

  ; 数据卷是旧基线，此处用安装器内嵌文件覆盖为最新壳/版本元数据（壳更新无需重打数据卷）
  SetOutPath "$INSTDIR"
  File "VOSR2Lite.exe"
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
  RMDir "$SMPROGRAMS\$StartMenuFolder"
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
