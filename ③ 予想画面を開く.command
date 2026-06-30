#!/bin/zsh
# 南関競馬AIの予想生成画面を開きます。
cd -- "$(dirname "$0")"

APP_FILE="main.py"
START_PORT=8501
PORT=8501

while lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

URL="http://localhost:${PORT}"

echo "▶ 南関競馬AIの予想画面を開きます…"
if [ "${PORT}" != "${START_PORT}" ]; then
  echo "   ${START_PORT}番は使用中なので、${PORT}番で開きます。"
fi
echo "   ${URL}"
echo ""

(sleep 2; open "${URL}" >/dev/null 2>&1) &
python3 -m streamlit run "${APP_FILE}" --server.port "${PORT}" --server.headless false

echo ""
echo "──────────────────────────────"
echo "Streamlitを終了しました。Enterキーを押すと閉じます。"
read _
