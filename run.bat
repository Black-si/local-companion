@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 src\main.py
) else (
  python src\main.py
)
