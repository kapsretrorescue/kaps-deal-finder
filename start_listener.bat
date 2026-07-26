@echo off
rem Keeps Discord commands answering instantly while this window is open.
rem Close the window (or Ctrl+C) to stop; the cloud keeps scanning either way.
title Kap's Deal Finder - Discord listener
cd /d "%~dp0"
".venv\Scripts\python.exe" -m dealfinder.listener
pause
