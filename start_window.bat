@echo off
rem «Одно окно»: двойной щелчок — откроется http://127.0.0.1:8765 в браузере.
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python -m lot_analyzer.app
pause
