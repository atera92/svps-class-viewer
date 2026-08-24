#!/bin/bash
# ダブルクリックで起動。ローカルサーバを立ててブラウザを開くだけ。
cd "$(dirname "$0")"
PORT=8899

pkill -f "http.server $PORT" >/dev/null 2>&1
python3 -m http.server $PORT >/dev/null 2>&1 &
sleep 1

# 同じWi-Fi内のスマホから開くためのアドレス
IP=""
for IF in en0 en1 en2; do
  A=$(ipconfig getifaddr $IF 2>/dev/null)
  [ -n "$A" ] && IP=$A && break
done

open "http://localhost:$PORT/index.html"

echo "──────────────────────────────────────────"
echo " このMac      : http://localhost:$PORT/index.html"
if [ -n "$IP" ]; then
  echo " スマホ・タブレット : http://$IP:$PORT/index.html"
  echo "   （このMacと同じWi-Fiに繋がっていること／Macがスリープしていないこと）"
else
  echo " スマホ用アドレス : Wi-Fiに繋がっていないため取得できませんでした"
fi
echo "──────────────────────────────────────────"
echo " 止めるとき:  pkill -f 'http.server $PORT'"
echo " このウィンドウは閉じてかまいません。"
