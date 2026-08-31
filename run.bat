@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   SQL Helper
echo ============================================
echo.

REM ── Check Python is available ────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

REM ── Create venv only if it does not exist ────
if not exist ".venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo.
    echo Installing dependencies ^(first run only — this may take a minute^)...
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
    echo Dependencies installed.
) else (
    REM Activate existing venv
    call .venv\Scripts\activate.bat
)

echo.
echo Starting SQL Helper...
echo Open your browser at: http://localhost:8501
echo Press Ctrl+C to stop.
echo.

streamlit run main.py --server.headless false --server.port 8501 --server.address localhost

pause
