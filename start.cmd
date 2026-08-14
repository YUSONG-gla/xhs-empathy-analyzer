@echo off
setlocal

if "%PORT%"=="" set "PORT=8000"
cd /d "%~dp0heart\backend"
python -m uvicorn main:app --host 0.0.0.0 --port %PORT%
