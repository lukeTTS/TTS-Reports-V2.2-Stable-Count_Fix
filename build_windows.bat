@echo off
REM Builds a single-file Windows executable.
REM Run this inside the project folder after: pip install -r requirements.txt
pyinstaller --onefile --name TTSProReportGenerator --add-data "templates;templates" --add-data "static;static" app.py
pause
