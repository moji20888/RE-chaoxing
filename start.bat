@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
rem ============================================================
rem  pure ASCII launcher - the real logic lives in start.py
rem  (keep this file simple: no Chinese, no labels, no blocks!)
rem ============================================================
python "start.py" %*
if errorlevel 9009 py "start.py" %*
echo.
pause