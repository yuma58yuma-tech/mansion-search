@echo off
cd /d "%~dp0"
echo ========================================
echo  プロキシURL設定ツール
echo ========================================
echo.
echo IPRoyalで取得したプロキシURLを入力してください
echo 例: http://ユーザー名:パスワード@geo.iproyal.com:12321
echo.
set /p PROXY_URL="プロキシURL: "
echo.
echo 設定中...
C:\Users\yuma5\.fly\bin\flyctl.exe secrets set PROXY_URL="%PROXY_URL%" --app mansion-search-jp
echo.
echo 完了！アプリが自動的に再起動します。
echo 1〜2分後にスマホで https://mansion-search-jp.fly.dev/ を試してください。
echo.
pause
