@echo off
cd /d "%~dp0"

echo 既存プロセスを終了中...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im cloudflared.exe >nul 2>&1
ping 127.0.0.1 -n 3 >nul

echo Streamlit起動中...
start "Streamlit App" cmd /k "cd /d %~dp0 && python -m streamlit run app.py --server.port 8504"
ping 127.0.0.1 -n 8 >nul

echo.
echo ========================================
echo   スマホ用URLを生成中...
echo ========================================
echo.
cloudflared.exe tunnel --url http://localhost:8504
