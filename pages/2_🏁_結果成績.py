# -*- coding: utf-8 -*-
"""結果・成績: 予想tier別の的中率と、レースごとの的中状況をまとめて見る。"""
import streamlit as st

st.set_page_config(page_title="結果・成績 | 南関AI", layout="wide")

import store as db
import ui_common as ui

ui.setup()

st.title("🏁 結果・成績")

TIER_ORDER = ["S", "A", "B", "C", "D", "E", "F", "G"]

# --- 絞り込み ---
dates = db.list_dates()
c1, c2 = st.columns(2)
with c1:
    date_opt = st.selectbox("開催日", ["すべて"] + dates, format_func=lambda d: ui.fmt_date(d) if d != "すべて" else d)
with c2:
    place_opt = st.selectbox("競馬場", ["すべて"] + list(ui.PLACE_OPTIONS.keys()))

date = None if date_opt == "すべて" else date_opt
place_code = None if place_opt == "すべて" else ui.PLACE_OPTIONS[place_opt]

stats = db.tier_hit_stats(date=date, place_code=place_code)

if not stats:
    st.info("結果が取得済みのレースがまだありません。`python3 fetch_results.py` か、左の更新で取得されます。")
    st.stop()

# --- tier別 的中率 ---
st.subheader("予想tier別 的中率")
table, chart = [], {}
for t in TIER_ORDER:
    if t not in stats:
        continue
    s = stats[t]
    n = s["n"] or 1
    table.append({
        "tier": t, "頭数": s["n"],
        "勝率": f"{s['win']/n*100:.1f}%",
        "連対率": f"{s['ren']/n*100:.1f}%",
        "複勝率": f"{s['fuku']/n*100:.1f}%",
    })
    chart[t] = round(s["fuku"] / n * 100, 1)

cc1, cc2 = st.columns([1, 1])
with cc1:
    st.dataframe(table, hide_index=True, width="stretch")
with cc2:
    st.caption("tier別 複勝率(%)")
    st.bar_chart(chart, horizontal=True)

st.caption("S/A→C→Eと右肩下がりなら相対評価が機能している指標。")

# --- レースごとの的中状況 ---
st.subheader("レース別 的中状況")
races = db.list_archive(date=date, place_code=place_code, only_with_result=True)
rows = []
for r in races:
    horses = db.get_race_horses(r["race_key"])
    winner = next((h["name"] for h in horses if h["finish_rank"] == 1), "")
    top_marks = [h for h in horses if h["tier"] in ("S", "A")]
    hit = any((h["finish_rank"] or 99) <= 3 for h in top_marks)
    win_hit = any(h["finish_rank"] == 1 for h in top_marks)
    rows.append({
        "日付": ui.fmt_date(r["date"]),
        "場": r["place_name"],
        "R": r["race_num"],
        "レース名": r.get("race_name") or "",
        "1着": winner,
        "S/A的中": ("🎯勝" if win_hit else ("○複" if hit else "✕")),
    })
if rows:
    st.dataframe(rows, hide_index=True, width="stretch")
    n_hit = sum(1 for x in rows if x["S/A的中"] != "✕")
    st.caption(f"S/A評価が3着内に来たレース: {n_hit}/{len(rows)}（{n_hit/len(rows)*100:.0f}%）")
