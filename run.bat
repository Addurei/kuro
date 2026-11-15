@echo off
REM Run this window minimised and let it close once Kuro is up: Kuro itself
REM runs with no console window at all (pythonw).
if not "%~1"=="min" (start "Kuro" /min cmd /c "%~f0" min & exit /b)
REM Start Kuro.
REM Kuro's libraries live in %LOCALAPPDATA%\Kuro\venv (outside Documents, which
REM Windows can lock down). First run sets them up; after that they update
REM automatically whenever requirements.txt changes, and repair themselves if broken.
REM A log of each start is kept in %LOCALAPPDATA%\Kuro\run-log.txt.
cd /d "%~dp0"
set "KURODIR=%LOCALAPPDATA%\Kuro"
set "VENV=%KURODIR%\venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "LOG=%KURODIR%\run-log.txt"
set "PYTHONPYCACHEPREFIX=%KURODIR%\pycache"
if not exist "%KURODIR%" mkdir "%KURODIR%"
echo ==== %date% %time% ==== > "%LOG%"

if not exist "%PY%" call :setup
if not exist "%PY%" goto :failed
fc /b requirements.txt "%VENV%\requirements.installed" >nul 2>&1
if errorlevel 1 call :install
"%PY%" -c "import PySide6.QtWidgets" >nul 2>&1
if errorlevel 1 call :repair

echo Starting Kuro... >> "%LOG%"
"%PY%" -c "import app.controller" >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
start "" "%PYW%" main.py
goto :eof

:setup
echo Setting up Kuro (first time, takes a few minutes)...
set "PYL=py"
where py >nul 2>nul || set "PYL=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
if not "%PYL%"=="py" if not exist "%PYL%" set "PYL=%WINDIR%\py.exe"
if not "%PYL%"=="py" if not exist "%PYL%" (echo Python is not installed. Get Python 3.12 from python.org, tick "Add python.exe to PATH", then start Kuro again. >> "%LOG%"& goto :eof)
"%PYL%" -3.12 -m venv "%VENV%" >> "%LOG%" 2>&1 || "%PYL%" -3 -m venv "%VENV%" >> "%LOG%" 2>&1
goto :eof

:install
echo Installing Kuro's libraries, one moment...
"%PY%" -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 goto :eof
copy /y requirements.txt "%VENV%\requirements.installed" >> "%LOG%" 2>&1
echo Slimming Kuro's libraries (removes parts he never uses)...
"%PY%" scripts\slim.py >> "%LOG%" 2>&1
goto :eof

:repair
echo Repairing Kuro's display library, one moment...
"%PY%" -m pip install --force-reinstall --no-deps --no-cache-dir --disable-pip-version-check PySide6-Essentials shiboken6 >> "%LOG%" 2>&1
goto :eof

:failed
start "" notepad "%LOG%"
goto :eof
