@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (py -3 -m cerex_core run --config configs\mission_demo.json) else (python -m cerex_core run --config configs\mission_demo.json)
echo.
pause
