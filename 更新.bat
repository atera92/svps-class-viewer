@echo off
chcp 65001 > nul
rem 新しい節が配信されたら、これをダブルクリックするだけ（更新.command の Windows 版）。
cd /d "%~dp0"
echo アーカイブ一覧を取得中...
python update_archives.py || goto :err
echo.
echo 試合データを生成中...
python build_data.py || goto :err
echo.
echo 完了しました。ビューアを開き直してください（新しいバトルだけが追加されます）。
pause
exit /b 0
:err
echo 失敗しました。上のメッセージを確認してください。
pause
exit /b 1
