# -*- coding: utf-8 -*-
"""アーカイブ閲覧: 過去の予想を選んで画面内に表示＋結果照合＋馬ごとメモ。"""
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="アーカイブ | 南関AI", layout="wide")

import store as db
import ui_common as ui

ui.setup()

st.title("📚 予想アーカイブ")

try:
    dates = db.list_dates()
except Exception as e:
    ui.stop_on_cloud_db_error(e, "予想アーカイブの読み込み")
if not dates:
    st.info("まだ予想がありません。トップの『予想生成』で作成すると、ここに保存されて見られます。")
    st.stop()

# --- 絞り込み ---
c1, c2, c3 = st.columns([2, 2, 1])
with c1:
    sel_date = st.selectbox("開催日", dates, format_func=ui.fmt_date)
with c2:
    try:
        places = sorted({r["place_code"] for r in db.list_archive(date=sel_date)})
    except Exception as e:
        ui.stop_on_cloud_db_error(e, "競馬場一覧の読み込み")
    place_labels = ["すべて"] + [ui.PLACE_BY_CODE.get(p, p) for p in places]
    sel_place_label = st.selectbox("競馬場", place_labels)
with c3:
    st.write("")
    st.write("")
    if st.button("🔄 最新を取込"):
        ui.refresh()
        st.rerun()

place_code = None if sel_place_label == "すべて" else ui.PLACE_OPTIONS.get(sel_place_label)
try:
    races = db.list_archive(date=sel_date, place_code=place_code)
except Exception as e:
    ui.stop_on_cloud_db_error(e, "レース一覧の読み込み")

if not races:
    st.warning("該当レースがありません。")
    st.stop()

# --- レース選択 ---
race_keys = [r["race_key"] for r in races]
race_by_key = {r["race_key"]: r for r in races}
sel_key = st.radio(
    "レース", race_keys, horizontal=True,
    format_func=lambda k: f"{race_by_key[k]['race_num']}R"
    + ("✅" if race_by_key[k]["has_result"] else ""),
)
race = race_by_key[sel_key]

st.subheader(
    f"{ui.fmt_date(race['date'])}　{race['place_name']}{race['race_num']}R"
    f"　{race.get('race_name') or ''}　{race.get('dist') or ''}m"
)

horses = db.get_race_horses(sel_key)
_uma_ids = [h["uma_id"] for h in horses]
notes_map = db.get_notes_map(_uma_ids)
patterns_map = db.get_patterns_map(_uma_ids)

tab_pred, tab_result, tab_memo = st.tabs(["🧠 予想", "🏁 結果・的中", "📝 メモ"])

# --- 予想（生成HTMLをそのまま画面内に表示）---
with tab_pred:
    try:
        html = db.get_cache_html(sel_key)
    except Exception as e:
        ui.stop_on_cloud_db_error(e, "予想HTMLの読み込み")
    if html:
        components.html(html, height=850, scrolling=True)
    else:
        st.warning("この予想のHTMLキャッシュが見つかりません（cache/ が削除された可能性）。")

# --- 結果・的中 ---
with tab_result:
    if not race["has_result"]:
        st.info("まだ結果が取得されていません（レース後に自動取得されます）。")
    else:
        rows = []
        for h in sorted(horses, key=lambda x: (x["finish_rank"] or 99)):
            hit = "◎" if (h["tier"] in ("S", "A") and (h["finish_rank"] or 99) <= 3) else ""
            rows.append({
                "着": h["finish_rank"],
                "馬番": h["umaban"],
                "馬名": h["name"],
                "予想": h["tier"],
                "人気": h["popularity"],
                "的中": hit,
                "メモ": "📝" if notes_map.get(h["uma_id"]) else "",
            })
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("的中◎ = 予想S/A かつ 3着以内")

# --- メモ（馬ごと・全レース共通）---
with tab_memo:
    st.caption("離れると自動保存。同じ馬は別の日・別レースでも同じメモ／好走パターンが出ます。")
    field = len(horses)
    order = sorted(horses, key=lambda x: (x["umaban"] or x["finish_rank"] or 99))
    for h in order:
        head = f"{ui.circled(h['umaban']) if h['umaban'] else ''} {h['name']}"
        if h["tier"]:
            head += f"  [{h['tier']}]"
        if notes_map.get(h["uma_id"]):
            head += "  📝"
        ps = ui.pattern_summary(patterns_map.get(h["uma_id"]))
        if ps:
            head += f"  ◇{ps}"
        has_any = bool(notes_map.get(h["uma_id"]) or patterns_map.get(h["uma_id"]))
        with st.expander(head, expanded=has_any):
            ctx = ui.gate_label(field, h["umaban"]) if h["umaban"] else None
            ui.memo_editor(h["uma_id"], h["name"], key_prefix=sel_key, race_ctx=ctx)
