#!/bin/zsh
# これまでの予想・結果・メモを、クラウド(Supabase)へまとめてアップロードします。
cd -- "$(dirname "$0")"
echo "▶ クラウドへアップロード中…（少し時間がかかります）"
echo ""
/usr/bin/python3 migrate_to_supabase.py
echo ""
echo "──────────────────────────────"
echo "終わりました。Enterキーを押すと閉じます。"
read _
