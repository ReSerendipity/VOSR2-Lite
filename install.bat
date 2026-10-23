@echo off
title VOSR2-Lite Installer
chcp 65001 >nul

echo ============================================
echo   VOSR2-Lite - Dependency Installer
echo ============================================
echo.

set "PYTHON_CMD="

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%~dp0.venv\Scripts\python.exe"
    echo [OK] Reusing existing project venv
    goto :python_found
)

if exist "C:\Python312\python.exe" set "PYTHON_CMD=C:\Python312\python.exe" & goto :make_venv
if exist "C:\Python313\python.exe" set "PYTHON_CMD=C:\Python313\python.exe" & goto :make_venv

for /f "delims=" %%i in ('where python 2^>nul') do (
    echo %%i | findstr /i "TRAE" >nul
    if errorlevel 1 (
        set "PYTHON_CMD=%%i"
        goto :make_venv
    )
)

echo [ERROR] No Python 3.12+ found. Install from https://www.python.org/downloads/
pause
exit /b 1

:make_venv
echo [1/3] Creating virtual environment...
"%PYTHON_CMD%" -m venv "%~dp0.venv"
if errorlevel 1 (
    echo [ERROR] venv creation failed.
    pause
    exit /b 1
)
set "PYTHON_CMD=%~dp0.venv\Scripts\python.exe"

:python_found
echo [2/3] Upgrading pip...
"%PYTHON_CMD%" -m pip install --upgrade pip

echo [3/3] Installing dependencies (this may take a few minutes)...
:: PyPI 的 torch 可能是 CPU 版；先按 CUDA 索引装 GPU 版，再装其余依赖。
:: PIP_CONFIG_FILE=NUL 绕过本机 pip.ini 的 extra-index-url（镜像 CPU 轮子
:: 会顶包 cu128 版，导致 cuda_available=False）。
set "PIP_CONFIG_FILE=NUL"
"%PYTHON_CMD%" -m pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
"%PYTHON_CMD%" -m pip install -r "%~dp0requirements.txt"
"%PYTHON_CMD%" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    echo         If PyTorch failed, install it manually with the CUDA index:
    echo         pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Install complete!
echo ============================================
echo.
echo Next steps:
echo   1. Download model weights (~6.6 GB^):
echo      %PYTHON_CMD% scripts\download_model.py
echo   2. Start the app:
echo      start.bat
echo.
pause
