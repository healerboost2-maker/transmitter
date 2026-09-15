@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo GMA DAVAO AMFM CASTER - PRODUCTION BUILD
echo ================================================

where py >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python launcher ^(py^) was not found.
    exit /b 1
)

if not exist ".venv" (
    echo Creating isolated build environment...
    py -m venv .venv
    if errorlevel 1 exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1

python -m pip install -r requirements-production.txt
if errorlevel 1 exit /b 1

python -m py_compile "amfmCaster(3).py"
if errorlevel 1 (
    echo ERROR: Python syntax check failed.
    exit /b 1
)

rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

python -m PyInstaller --noconfirm --clean "GMA_DAVAO_AMFM_Caster.spec"
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)

echo.
echo BUILD COMPLETE.
echo Output: dist\GMA_DAVAO_AMFM_Caster.exe
endlocal
