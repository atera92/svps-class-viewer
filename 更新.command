#!/bin/bash
# 新しい節が配信されたら、これをダブルクリックするだけ。
cd "$(dirname "$0")"
echo "アーカイブ一覧を取得中..."
python3 update_archives.py || exit 1
echo
echo "試合データを生成中..."
python3 build_data.py || exit 1
echo
echo "完了しました。ビューアを開き直してください（新しいバトルだけが追加されます）。"
