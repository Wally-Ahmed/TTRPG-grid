@echo off
REM ============================================================================
REM  start.bat - one-command bootstrap + launcher for the TTRPG Grid Map
REM              Generator ("Cartographer's Table") on Windows.
REM
REM  What it does, in order:
REM    1. Verifies Python 3.10+ is available (clear error if not).
REM    2. Creates a local virtual environment in .venv\ if one doesn't exist.
REM    3. Installs requirements.txt the first time (or whenever Flask isn't
REM       importable), then skips that step on every run afterwards.
REM    4. Launches the app (python run.py), forwarding any extra arguments.
REM
REM  It is idempotent: the SAME command is both the first-run installer and the
REM  everyday launcher.
REM
REM  Usage:
REM    start.bat                 - set up (first run) then serve on 127.0.0.1:5000
REM    start.bat --port 8080     - pass through to run.py (custom port)
REM    start.bat --no-browser    - don't auto-open a browser tab
REM ============================================================================
setlocal EnableExtensions EnableDelayedExpansion

REM Operate from the directory this script lives in.
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "REQUIREMENTS=requirements.txt"

REM --- 1. Locate a suitable Python launcher (need >= 3.10) ---------------------
REM Prefer the Windows 'py' launcher, then fall back to 'python'.
set "PYTHON="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON=python"
    )
)

if not defined PYTHON (
    echo ERROR: Python 3.10 or newer is required but was not found.
    echo.
    echo   Install Python 3.10+ from https://www.python.org/downloads/
    echo   and be sure to tick "Add Python to PATH" during installation,
    echo   then run start.bat again.
    exit /b 1
)

REM --- 2. Create the virtual environment if it doesn't exist -------------------
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating virtual environment in %VENV_DIR%\ ...
    %PYTHON% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        exit /b 1
    )
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

REM --- 3. Install dependencies only when needed --------------------------------
REM Probe for Flask: if it imports, setup is already done and we skip pip.
"%VENV_PY%" -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ^(first run only^) ...
    "%VENV_PY%" -m pip install --upgrade pip >nul
    "%VENV_PY%" -m pip install -r "%REQUIREMENTS%"
    if errorlevel 1 (
        echo ERROR: failed to install dependencies.
        exit /b 1
    )
    echo Dependencies installed.
)

REM --- 4. Launch the app, forwarding any extra arguments ----------------------
"%VENV_PY%" run.py %*
exit /b %errorlevel%
