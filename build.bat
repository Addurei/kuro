@echo off
REM Builds dist\Kuro\Kuro.exe - a standalone app that doesn't need Python installed.
cd /d "%~dp0"
call "%LOCALAPPDATA%\Kuro\venv\Scripts\activate.bat"
python -m pip install -q --disable-pip-version-check -r requirements-dev.txt
python -m pytest -q || (echo Tests failed - not building. & pause & exit /b 1)
pyinstaller --noconfirm --clean --windowed ^
  --exclude-module PIL --exclude-module tkinter ^
  --name Kuro ^
  --icon assets\icon.ico ^
  --add-data "assets;assets" ^
  --hidden-import pyttsx3.drivers ^
  --hidden-import pyttsx3.drivers.sapi5 ^
  --hidden-import comtypes.stream ^
  main.py
echo.
echo Built: dist\Kuro\Kuro.exe
pause
