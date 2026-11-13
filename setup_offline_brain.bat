@echo off
REM Optional: set up Kuro's free OFFLINE brain (Ollama). Private, no internet, no limits.
REM Reliable tool use wants ~16 GB RAM. On 8 GB laptops use the free Gemini brain instead.
setlocal
set MODEL=qwen3:8b
if not "%1"=="" set MODEL=%1
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not installed. Opening the download page...
  start https://ollama.com/download
  echo Install it, then run this file again.
  pause
  exit /b 1
)
echo Downloading %MODEL% (a few GB, one time only)...
ollama pull %MODEL% || (echo Download failed. & pause & exit /b 1)
echo.
echo Done! In Kuro: right-click ^> Settings ^> Brain ^> "Ollama", model %MODEL%.
echo Bigger PC? Try a stronger model:  setup_offline_brain.bat qwen3:14b
pause
