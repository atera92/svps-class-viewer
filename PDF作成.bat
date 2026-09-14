@echo off
chcp 65001 > nul
rem マリガン早見表の PDF を作り直す（起動.bat でサーバを立ててから実行する）。
cd /d "%~dp0"
set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
  echo Google Chrome が見つかりません。
  pause
  exit /b 1
)
"%CHROME%" --headless --disable-gpu --no-pdf-header-footer --virtual-time-budget=15000 --print-to-pdf="%~dp0mulligan.pdf" "http://localhost:8899/mulligan.html?class=all&src=pass&view=sns"
"%CHROME%" --headless --disable-gpu --no-pdf-header-footer --virtual-time-budget=15000 --print-to-pdf="%~dp0mulligan_stream.pdf" "http://localhost:8899/mulligan.html?class=all&view=stream"
echo mulligan.pdf と mulligan_stream.pdf を作り直しました。
pause
