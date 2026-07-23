@echo off
rem Runs one deal-finder cycle. Called hourly by Windows Task Scheduler.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m dealfinder.main >> "logs\scheduler.log" 2>&1
