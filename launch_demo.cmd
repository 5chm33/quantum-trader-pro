@echo off
setlocal
cd /d "%~dp0"

echo Quantum Trader Pro - Offline Demo
echo No broker connection, credentials, paper orders, or live orders are used.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.11 "%~dp0launch_demo.py" %*
    set "exit_code=%errorlevel%"
) else (
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo Python 3.11 or newer was not found.
        echo Install it from https://www.python.org/downloads/
        start "" "https://www.python.org/downloads/"
        if not defined CI pause
        exit /b 2
    )
    python "%~dp0launch_demo.py" %*
    set "exit_code=%errorlevel%"
)

echo.
if %exit_code%==0 (
    echo Demo completed. Open the newest folder under quantum-trader-demo-runs.
) else (
    echo Demo did not complete. Review the message above.
)
if not defined CI pause
exit /b %exit_code%
