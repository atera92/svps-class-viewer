@echo off
chcp 65001 > nul
rem データを更新して、公開サイトに反映する（GitHub Pages）。公開.command の Windows 版。
cd /d "%~dp0"
echo アーカイブ一覧を取得中...
python update_archives.py || goto :err
echo.
echo 試合データを生成中...
python build_data.py || goto :err
echo.
git add -A
git diff --cached --quiet && (echo 更新はありませんでした。& pause & exit /b 0)
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
git commit -q -m "データ更新 %TODAY%" || goto :err
git push -q || goto :err
echo 公開しました。友人は数十秒後にページを再読み込みすれば新しい試合が入ります。
pause
exit /b 0
:err
echo 失敗しました。上のメッセージを確認してください。
pause
exit /b 1
