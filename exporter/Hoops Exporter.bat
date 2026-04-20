@echo off
REM Hoops Highlight Exporter - Windows launcher
REM Double-click this file to open the GUI without a console window staying open.

cd /d "%~dp0"
start "" pythonw gui.py
if errorlevel 1 (
  echo pythonw not found, falling back to python...
  start "" python gui.py
)
