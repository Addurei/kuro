@echo off
REM Run this window minimised and let it close once the cat is up: the app
REM itself runs with no console window at all (pythonw).
if not "%~1"=="min" (start "Kuro" /min cmd /c "%~f0" min & exit /b)
REM Start the cat. A CHARACTER.json next to this file can reskin the install
REM (see app/character.py); its "folder" decides where settings live. Libraries
REM live in %LOCALAPPDATA%\<folder>\venv (outside Documents, which Windows can
REM lock down). First run sets them up; after that they update whenever
REM requirements.txt changes and repair themselves if broken. A log of each
REM start is kept next to them in run-log.txt.
cd /d "%~dp0"
set "CHAR=Kuro"
if exist "CHARACTER.json" for /f "usebackq delims=" %%i in (`powershell -noprofile -command "try{$j=Get-Content -Raw 'CHARACTER.json'|ConvertFrom-Json; if($j.folder){$j.folder}elseif($j.name){$j.name}}catch{}"`) do set "CHAR=%%i"
set "KURODIR=%LOCALAPPDATA%\%CHAR%"
set "VENV=%KURODIR%\venv"
REM custom installs share Kuro's libraries when those already exist
if not exist "%VENV%\Scripts\python.exe" (
  if exist "%LOCALAPPDATA%\Kuro\venv\Scripts\python.exe" set "VENV=%LOCALAPPDATA%\Kuro\venv"
)
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

echo Starting %CHAR%... >> "%LOG%"
"%PY%" -c "import app.controller" >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
start "" "%PYW%" main.py
goto :eof

:setup
echo Setting up %CHAR% (first time, takes a few minutes)...
where py >nul 2>nul || (echo Python is not installed. Get Python 3.12 from python.org first.& goto :eof)
py -3.12 -m venv "%VENV%" >> "%LOG%" 2>&1 || py -3 -m venv "%VENV%" >> "%LOG%" 2>&1
goto :eof

:install
echo Installing libraries, one moment...
"%PY%" -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 goto :eof
copy /y requirements.txt "%VENV%\requirements.installed" >> "%LOG%" 2>&1
echo Slimming the libraries (removes parts the cat never uses)...
"%PY%" scripts\slim.py >> "%LOG%" 2>&1
goto :eof

:repair
echo Repairing the display library, one moment...
"%PY%" -m pip install --force-reinstall --no-deps --no-cache-dir --disable-pip-version-check PySide6-Essentials shiboken6 >> "%LOG%" 2>&1
goto :eof

:failed
start "" notepad "%LOG%"
goto :eof
