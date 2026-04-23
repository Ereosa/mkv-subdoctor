@echo off
:: MKV SubDoctor — Windows installer launcher
:: Double-click this file or run from the command prompt.

echo.
echo  MKV SubDoctor — Installer
echo  ==================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed or not in PATH.
    echo.
    echo  Download Python 3.10 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  Make sure to tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Run the Python installer, passing through any arguments
python "%~dp0install.py" %*
if errorlevel 1 (
    echo.
    echo  Installation encountered errors — see above.
    pause
    exit /b 1
)

pause
