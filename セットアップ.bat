@echo off
chcp 65001 > nul
rem Windows で最初に1回だけダブルクリックする。Python の部品と、読み取り用のブラウザを入れる。
cd /d "%~dp0"
where python > nul 2>&1
if errorlevel 1 (
  echo Python が見つかりません。python.org から Python 3.10 以上を入れてください。
  echo 入れるときに「Add python.exe to PATH」にチェックを入れること。
  pause
  exit /b 1
)
echo Python の部品を入れています...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo 部品のインストールに失敗しました。
  pause
  exit /b 1
)
echo 読み取り用のブラウザを入れています（Google Chrome が入っていれば、そちらを優先して使います）...
python -m playwright install chromium
echo.
echo セットアップが終わりました。
pause
