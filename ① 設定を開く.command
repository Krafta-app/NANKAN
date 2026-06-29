#!/bin/zsh
# Supabase(クラウドDB)の接続情報を入力するための設定ファイルを開きます。
cd -- "$(dirname "$0")"
mkdir -p .streamlit
F=".streamlit/secrets.toml"
[ -f "$F" ] || touch "$F"
if ! grep -q "\[supabase\]" "$F"; then
cat >> "$F" <<'EOF'

[supabase]
url = "ここにProject_URLを貼る"
key = "ここにservice_roleキーを貼る"
EOF
fi
open -e "$F"
echo ""
echo "✅ テキストエディタで設定ファイルを開きました。"
echo "   [supabase] の url と key の \"…\" の中を書き換えて、⌘+S で保存してください。"
echo "   （この黒い画面は閉じてOKです）"
