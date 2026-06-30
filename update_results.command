#!/bin/zsh
# レース結果を今すぐ取得する（自動取得を待たずに手動更新したいとき）。
cd -- "$(dirname "$0")"
echo "▶ 結果を取得します..."
/usr/bin/python3 fetch_results.py
echo ""
echo "完了。Vercelサイトの結果・成績に反映されます。Enterで閉じます。"
read _
