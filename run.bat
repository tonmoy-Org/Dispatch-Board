@echo off
setlocal
cd /d "%~dp0"

echo ==============================================================
echo   Sterling Septic ^& Plumbing - Dispatch Board Automation
echo ==============================================================
echo.

if "%1"=="" (
    echo Available Commands:
    echo.
    echo [Job Check-In Quo Automation]
    echo   run.bat --checkin --dry-run                 Simulate Job Check-in cycle
    echo   run.bat --checkin --daemon                  Run continuous check-in monitor (60s loop)
    echo   run.bat --checkin --test-sms russell        Send test SMS to technician via Quo
    echo   run.bat --checkin --test-note russell       Test creating note on board
    echo   run.bat --checkin --test-delete-note russell Test deleting note from board
    echo.
    echo [Dispatch Board Display Automation ^& Dashboard]
    echo   run.bat --live --days 31 --loop             Run full automation loop with Web Dashboard
    echo   run.bat --live --days 5                     Run live for 5 days
    echo   run.bat --display                           Run display scraper directly (no dashboard)
    echo   run.bat --asset                             Run asset assignment scraper
    echo.
    echo Defaulting to: Job Check-in dry-run check...
    echo.
    python run.py --checkin --dry-run
) else (
    python run.py %*
)

pause
