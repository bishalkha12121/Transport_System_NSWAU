@echo off
title TfNSW Live Departures - Setup
color 0A

echo ================================================
echo  TfNSW Live Departures - Local Setup
echo  Project Management INF304
echo ================================================
echo.

:: ── Check Python ─────────────────────────────────
echo [1/5] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Python is not installed or not in PATH.
    echo  Please download and install Python 3.10+ from:
    echo  https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo  Found: %%i
echo.

:: ── Check pip ────────────────────────────────────
echo [2/5] Checking pip...
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: pip not found. Reinstall Python with pip included.
    pause
    exit /b 1
)
echo  pip OK
echo.

:: ── Install dependencies ──────────────────────────
echo [3/5] Installing Python dependencies...
echo  (This may take a minute on first run)
echo.
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Failed to install dependencies.
    echo  Try running this file as Administrator.
    pause
    exit /b 1
)
echo.
echo  Dependencies installed successfully.
echo.

:: ── Check .env file ───────────────────────────────
echo [4/5] Checking environment variables...
if not exist ".env" (
    echo.
    echo  WARNING: No .env file found!
    echo  Creating a template .env file...
    echo.
    (
        echo TFNSW_API_KEY=your_tfnsw_api_key_here
        echo SUPABASE_URL=https://zpgrbvgawmkrdmaxanbv.supabase.co
        echo SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpwZ3Jidmdhd21rcmRtYXhhbmJ2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODAwNjk2OTQsImV4cCI6MjA5NTY0NTY5NH0.1IpEBMLlxwbDwAy0nOxwfzOy7Gu9ZugBrZyeOJb9PCg
    ) > .env
    echo  .env file created.
    echo  IMPORTANT: Open .env and replace TFNSW_API_KEY with your actual key.
    echo.
) else (
    echo  .env file found OK
)
echo.

:: ── Start the server ──────────────────────────────
echo [5/5] Starting the server...
echo.
echo ================================================
echo  App running at: http://localhost:3400
echo  Landing page:   http://localhost:3400/landing.html
echo  Press Ctrl+C to stop the server
echo ================================================
echo.

python app.py

pause
