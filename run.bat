@echo off
REM Start Kuro.
REM Kuro's libraries live in %LOCALAPPDATA%\Kuro\venv (outside Documents, which
REM Windows can lock down). First run sets them up; after that they update
REM automatically whenever requirements.txt changes, and repair themselves if broken.
REM A log of each start is kept in %LOCALAPPDATA%\Kuro\run-log.txt.
cd /d "%~dp0"
set "KURODIR=%LOCALAPPDATA%\Kuro"
set "VENV=%KURODIR%\venv"
set "PY=%VENV%\Scripts\python.exe"
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
"%PY%" main.py >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
goto :eof

:setup
echo Setting up Kuro (first time, takes a few minutes)...
where py >nul 2>nul || (echo Python is not installed. Get Python 3.12 from python.org first.& goto :eof)
py -3.12 -m venv "%VENV%" >> "%LOG%" 2>&1 || py -3 -m venv "%VENV%" >> "%LOG%" 2>&1
goto :eof

:install
echo Installing Kuro's libraries, one moment...
"%PY%" -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 goto :eof
copy /y requirements.txt "%VENV%\requirements.installed" >> "%LOG%" 2>&1
goto :eof

:repair
echo Repairing Kuro's display library, one moment...
"%PY%" -m pip install --force-reinstall --no-deps --no-cache-dir --disable-pip-version-check PySide6-Essentials shiboken6 >> "%LOG%" 2>&1
goto :eof

:failed
type "%LOG%"
echo.
echo Kuro stopped with an error. The log above is saved in %LOG%
pause
goto :eof
