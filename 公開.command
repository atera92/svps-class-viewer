#!/bin/bash
# データを更新して、公開サイトに反映する（GitHub Pages）。
cd "$(dirname "$0")"
set -e
echo "アーカイブ一覧を取得中..."
python3 update_archives.py
echo
echo "試合データを生成中..."
python3 build_data.py
echo
if [ -z "$(git status --porcelain)" ]; then
  echo "更新はありませんでした。"
  exit 0
fi
git add -A
git commit -q -m "データ更新 $(date +%Y-%m-%d)"
git push -q
echo "公開しました。友人は数十秒後にページを再読み込みすれば新しい試合が入ります。"
