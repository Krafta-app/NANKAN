#!/bin/zsh
cd -- "$(dirname "$0")"
echo "▶ 翌日の南関データ自動更新を設定します。"
echo "   毎日18〜23時（日本時間）に確認し、翌日開催の全レースを更新します。"
echo "   Macにログインし、スリープしていない間に動作します。"
python3 tools/manage_auto_update.py install
STATUS=$?
echo "Enterキーを押すと閉じます。"
read _
exit "${STATUS}"
