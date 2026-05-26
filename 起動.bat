@echo off
cd /d "%~dp0"
python -m streamlit run app.py --server.port 8504
start microsoft-edge:http://127.0.0.1:8504
pause
