@echo off
cd /d "%~dp0"
echo Starting Richseed backend...
uvicorn app.main:app --reload
pause
