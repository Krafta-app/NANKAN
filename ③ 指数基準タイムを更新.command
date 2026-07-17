#!/bin/zsh
# NAR公式の地方全場とcourse-dbのJRA全102コース基準を更新します。
cd -- "$(dirname "$0")"

echo "▶ 速度指数の基準タイムを更新します…"
echo "   初回はNAR公式の過去5年分を取得するため、数分かかる場合があります。"
echo ""

python3 tools/update_nar_speed_reference.py
NAR_STATUS=$?

python3 tools/update_jra_course_db.py
JRA_STATUS=$?

echo ""
if [ "${NAR_STATUS}" -eq 0 ] && [ "${JRA_STATUS}" -eq 0 ]; then
  echo "✅ 地方・JRAの基準タイム更新が完了しました。"
  FINAL_STATUS=0
else
  echo "⚠️ 一部の更新に失敗しました。既存の基準データは残っています。"
  FINAL_STATUS=1
fi

echo "Enterキーを押すと閉じます。"
read _
exit "${FINAL_STATUS}"
