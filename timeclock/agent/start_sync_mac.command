#!/bin/bash
cd "$(dirname "$0")"
if [ ! -f config.ini ]; then echo "config.ini is missing. Copy config.example.ini to config.ini and fill it in."; read; exit 1; fi
echo "Installing requirements (first time only)..."
python3 -m pip install --quiet --user -r requirements.txt
python3 sync_agent.py
