@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
    echo [1/4] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo [2/4] Activating virtual environment...
call .venv\Scripts\activate.bat
if errorlevel 1 goto :error

echo [3/4] Installing/updating dependencies including tzdata...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [4/4] Initializing demo data...
python seed.py
if errorlevel 1 goto :error

echo.
echo Setup complete.
echo Start the app with: run_windows.bat
exit /b 0

:error
echo.
echo Setup failed. Check the error above.
exit /b 1
