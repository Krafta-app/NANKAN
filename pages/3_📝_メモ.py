# -*- coding: utf-8 -*-
"""メモ: 馬ごとに蓄積したメモを横断検索・編集。過去の出走レースも確認。"""
import streamlit as st

st.set_page_config(page_title="メモ | 南関AI", layout="wide")

import store as db
import ui_common as ui

ui.setup()

st.title("📝 馬メモ")
st.caption("メモは uma_id（馬ごと）に保存。同じ馬は別の日・別レースでも同じメモが出ます。")

query = st.text_input("🔎 馬名で検索", placeholder="馬名の一部を入力")
notes = db.search_notes(query)

if not notes:
    if query:
        st.warning("該当するメモがありません。")
    else:
        st.info("まだメモがありません。アーカイブの各レース『📝メモ』タブから書けます。")
    st.stop()

st.caption(f"{len(notes)} 件")
for nt in notes:
    preview = (nt["note_text"] or "").replace("\n", " ")
    if len(preview) > 40:
        preview = preview[:40] + "…"
    with st.expander(f"🐎 {nt['horse_name']}　— {preview}"):
        ui.note_editor(nt["uma_id"], nt["horse_name"], key_prefix="memo", height=100)

        past = db.races_for_uma(nt["uma_id"])
        if past:
            st.markdown("**この馬の出走レース（結果取得済み）**")
            st.dataframe(
                [{
                    "日付": ui.fmt_date(p["date"]),
                    "場": p["place_name"],
                    "R": p["race_num"],
                    "レース名": p.get("race_name") or "",
                    "着": p["finish_rank"],
                    "人気": p["popularity"],
                } for p in past],
                hide_index=True, width="stretch",
            )
        st.caption(f"uma_id: {nt['uma_id']}　更新: {nt['updated_at']}")
