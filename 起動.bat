@echo off
chcp 65001 > nul
rem ダブルクリックで起動。ローカルサーバを立ててブラウザを開くだけ（起動.command の Windows 版）。
cd /d "%~dp0"
set PORT=8899
start "SVPS server" /min python -m http.server %PORT%
timeout /t 1 > nul
start "" http://localhost:%PORT%/index.html
echo ──────────────────────────────────────────
echo  このPC : http://localhost:%PORT%/index.html
echo  スマホ用アドレス（同じWi-Fiに繋いで、下の IPv4 アドレスを使う）:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo     http:%%a:%PORT%/index.html
echo  止めるとき : タスクバーの「SVPS server」のウィンドウを閉じる
echo ──────────────────────────────────────────
pause
