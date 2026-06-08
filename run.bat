@echo off
REM ---------------------------------------------------------------
REM  Launch the Parsel UI using the isolated venv.
REM  Double-click this file. No console window stays open: the GUI
REM  runs via pythonw.exe (windowed Python) and this cmd closes.
REM ---------------------------------------------------------------
cd /d "%~dp0"

REM First-run setup needs the console to show pip progress.
if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found. Creating it now...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM Launch detached with windowed Python so no terminal lingers.
start "" ".venv\Scripts\pythonw.exe" main.py
