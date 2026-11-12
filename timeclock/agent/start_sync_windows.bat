@echo off
cd /d "%~dp0"
if not exist config.ini (
  echo config.ini is missing. Copy config.example.ini to config.ini and fill it in.
  pause
  exit /b 1
)
echo Installing requirements (first time only)...
python -m pip install --quiet -r requirements.txt
python sync_agent.py
pause
