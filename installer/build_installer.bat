@echo off
REM Builds dist\KuroSetup.exe. Needs Inno Setup 6 (free, jrsoftware.org).
setlocal
cd /d "%~dp0"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php
  pause
  exit /b 1
)
for /f "tokens=2 delims== " %%v in ('findstr /c:"VERSION = " ..\app\version.py') do set KURO_VERSION=%%~v
echo Building KuroSetup.exe (version %KURO_VERSION%)...
"%ISCC%" kuro.iss || (pause & exit /b 1)
echo.
echo Done: dist\KuroSetup.exe
pause
