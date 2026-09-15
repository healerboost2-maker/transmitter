@echo off
setlocal EnableExtensions

title GMA DAVAO AM-FM Caster - Production Build

cd /d "%~dp0"

set "PROJECT_DIR=%~dp0"
set "VENV_DIR=%PROJECT_DIR%..\.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "SPEC_FILE=%PROJECT_DIR%GMA_DAVAO_AMFM_Caster.spec"
set "SOURCE_FILE=%PROJECT_DIR%amfmCaster.py"
set "ICON_FILE=%PROJECT_DIR%am-fm_app.ico"
set "REQUIREMENTS=%PROJECT_DIR%requirements-production.txt"
set "DIST_DIR=%PROJECT_DIR%dist"
set "BUILD_DIR=%PROJECT_DIR%build"
set "OUTPUT_EXE=%DIST_DIR%\GMA_DAVAO_AMFM_Caster.exe"

echo.
echo ============================================================
echo   GMA DAVAO AM-FM CASTER - PRODUCTION BUILD
echo ============================================================
echo.
echo Project : %PROJECT_DIR%
echo Venv    : %VENV_DIR%
echo.

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python virtual environment not found:
    echo         %VENV_DIR%
    echo.
    echo Create it with:
    echo         py -3 -m venv "%VENV_DIR%"
    echo.
    pause
    exit /b 1
)

echo [OK] Virtual environment found.

if not exist "%SOURCE_FILE%" (
    echo [ERROR] Missing source file:
    echo         %SOURCE_FILE%
    pause
    exit /b 1
)

if not exist "%SPEC_FILE%" (
    echo [ERROR] Missing PyInstaller spec:
    echo         %SPEC_FILE%
    pause
    exit /b 1
)

if not exist "%ICON_FILE%" (
    echo [ERROR] Missing application icon:
    echo         %ICON_FILE%
    pause
    exit /b 1
)

if not exist "%REQUIREMENTS%" (
    echo [ERROR] Missing requirements file:
    echo         %REQUIREMENTS%
    pause
    exit /b 1
)

echo [OK] Production files verified.

echo.
echo [1/7] Updating pip...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to update pip.
    pause
    exit /b 1
)

echo.
echo [2/7] Installing production dependencies...
"%PYTHON_EXE%" -m pip install -r "%REQUIREMENTS%"
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo [3/7] Checking Python source syntax...
"%PYTHON_EXE%" -m py_compile "%SOURCE_FILE%"
if errorlevel 1 (
    echo [ERROR] Python syntax check failed.
    pause
    exit /b 1
)

echo [OK] Python syntax check passed.

echo.
echo [4/7] Cleaning previous build output...

if exist "%BUILD_DIR%" (
    rmdir /s /q "%BUILD_DIR%"
    if errorlevel 1 (
        echo [ERROR] Could not remove:
        echo         %BUILD_DIR%
        echo Close any application or Explorer window using the build files.
        pause
        exit /b 1
    )
)

if exist "%DIST_DIR%" (
    rmdir /s /q "%DIST_DIR%"
    if errorlevel 1 (
        echo [ERROR] Could not remove:
        echo         %DIST_DIR%
        echo Close any application or Explorer window using the build files.
        pause
        exit /b 1
    )
)

echo [OK] Previous build output removed.

echo.
echo [5/7] Building production executable...
echo.
echo PyInstaller spec:
echo   %SPEC_FILE%
echo.

"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean "%SPEC_FILE%"
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.
    echo.
    echo Review the PyInstaller output above for the first error.
    pause
    exit /b 1
)

echo.
echo [6/7] Verifying production executable...

if not exist "%OUTPUT_EXE%" (
    echo [ERROR] Build completed but executable was not found:
    echo         %OUTPUT_EXE%
    pause
    exit /b 1
)

for %%A in ("%OUTPUT_EXE%") do set "EXE_SIZE=%%~zA"

echo [OK] Production executable created.
echo.
echo   File: %OUTPUT_EXE%
echo   Size: %EXE_SIZE% bytes

echo.
echo [7/7] BUILD SUCCESSFUL
echo.
echo ============================================================
echo   OUTPUT
echo ============================================================
echo.
echo   GMA_DAVAO_AMFM_Caster.exe
echo.
echo   Location:
echo   %OUTPUT_EXE%
echo.
echo ============================================================
echo   NEXT STEP - INNO SETUP
echo ============================================================
echo.
echo Use the EXE above as the application payload in your
necho Inno Setup installer.
echo.
echo The .venv folder is NOT part of the installer.
echo Python is NOT required on the end user's PC.
echo Development files are NOT required on the end user's PC.
echo.
echo Opening dist folder...
echo.

explorer "%DIST_DIR%"

echo.
echo Build finished successfully.
pause
exit /b 0
