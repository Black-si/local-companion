@echo off
setlocal
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 src\main.py
) else (
  python src\main.py
)
