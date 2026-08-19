@echo off
rem Rebuild the dashboard from the latest world backup.
cd /d "%~dp0"
py -3 extract.py
py -3 bundle.py
echo Done. Open palworld-dashboard.html
pause
