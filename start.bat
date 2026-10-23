@echo off
title VOSR2-Lite
chcp 65001 >nul

:: Fix OMP duplicate library issue on Windows
set "KMP_DUPLICATE_LIB_OK=TRUE"

echo ============================================
echo   VOSR2-Lite - AI Image Super-Resolution
echo ============================================
echo.

:: Python detection priority:
::   1. Project virtual environment (.venv) - most isolated, recommended
::   2. System Python (C:\Python312 etc.)
::   3. Python in PATH (skipping IDE-bundled ones)
set "PYTHON_CMD="

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%~dp0.venv\Scripts\python.exe"
    echo [OK] Found project venv
    goto :python_found
)

if exist "C:\Python312\python.exe" (
    set "PYTHON_CMD=C:\Python312\python.exe"
    echo [OK] Found system Python 3.12
    goto :python_found
)

if exist "C:\Python313\python.exe" (
    set "PYTHON_CMD=C:\Python313\python.exe"
    echo [OK] Found system Python 3.13
    goto :python_found
)

for /f "delims=" %%i in ('where python 2^>nul') do (
    echo %%i | findstr /i "TRAE" >nul
    if errorlevel 1 (
        set "PYTHON_CMD=%%i"
        echo [OK] Found Python in PATH
        goto :python_found
    )
)

echo [ERROR] No Python found. Please install Python 3.12+ first:
echo         https://www.python.org/downloads/
pause
exit /b 1

:python_found
echo Using: %PYTHON_CMD%
echo.

:: Check model weights
if not exist "%~dp0model\VOSR2\checkpoints\ema_model.safetensors" (
    echo [WARN] VOSR2 model weights not found.
    echo        Run: %PYTHON_CMD% scripts\download_model.py
    echo.
)

echo Starting server at http://127.0.0.1:7871 ...
echo Press Ctrl+C to stop.
echo.

"%PYTHON_CMD%" -m app.integrated_app.app_server

if errorlevel 1 (
    echo.
    echo [ERROR] Server exited unexpectedly. Check logs\app.log
    pause
)
