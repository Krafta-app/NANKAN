# -*- coding: utf-8 -*-
"""Streamlit ページ共通ヘルパー（DB初期化・整形・メモ編集ウィジェット）。
keiba_bot（重い・selenium読込）は import しない。閲覧系ページを軽く保つ。"""
import threading
import time
import streamlit as st
import store as db

PLACE_OPTIONS = {"大井": "10", "川崎": "11", "船橋": "12", "浦和": "13"}
PLACE_BY_CODE = {v: k for k, v in PLACE_OPTIONS.items()}
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _fetch_loop(interval=1800):
    """南関の開催時間帯(14〜23時)に未取得結果を定期取得する常駐ループ。
    fetch_results は requests ベース（selenium不要）なので軽い。"""
    import fetch_results
    while True:
        try:
            if 14 <= time.localtime().tm_hour <= 23:
                fetch_results.run_once()
        except Exception as e:
            print(f"[bg-fetch] error: {e}")
        time.sleep(interval)


@st.cache_resource
def setup():
    """DBスキーマ作成＋キャッシュから台帳バックフィル＋結果取得スレッド起動
    （いずれも Streamlit プロセス内で1回だけ。アプリ起動中は結果が自動更新される）。"""
    db.init_db()
    n = db.backfill_from_cache()
    threading.Thread(target=_fetch_loop, daemon=True, name="keiba-fetch").start()
    return n


def refresh():
    """新しい予想キャッシュを台帳へ取り込む（手動更新ボタン用）。"""
    setup.clear()
    db.init_db()
    return db.backfill_from_cache()


def is_cloud():
    """Streamlit Cloud で動いているか（secrets に is_cloud=true を入れた時 True）。
    クラウドでは生成(Selenium)を出さず閲覧に誘導するために使う。"""
    try:
        return bool(st.secrets.get("is_cloud", False))
    except Exception:
        return False


def fmt_date(d):
    return f"{d[:4]}/{d[4:6]}/{d[6:8]}" if d and len(d) == 8 else (d or "")


def circled(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n) if n is not None else ""
    return _CIRCLED[n - 1] if 1 <= n <= 20 else str(n)


def tier_badge(tier):
    if not tier:
        return ""
    colors = {"S": "#d32f2f", "A": "#e64a19", "B": "#f9a825",
              "C": "#388e3c", "D": "#1976d2", "E": "#757575"}
    c = colors.get(tier, "#757575")
    return (f"<span style='background:{c};color:#fff;border-radius:4px;"
            f"padding:1px 7px;font-weight:700;font-size:0.85em'>{tier}</span>")


def race_label(race):
    """アーカイブ一覧の1行ラベル。"""
    badge = "✅" if race.get("has_result") else "⏳"
    dist = race.get("dist") or ""
    nm = race.get("race_name") or ""
    return (f"{badge} {race['race_num']:>2}R  {race.get('place_name','')}"
            f"  {dist}m  {nm}")


def _save_note_cb(uma_id, horse_name, wkey):
    db.set_note(uma_id, horse_name, st.session_state.get(wkey, ""))


def note_editor(uma_id, horse_name, key_prefix="", height=70):
    """馬ごとメモ欄（uma_id キー）。フォーカスを外すと自動保存。"""
    if not uma_id:
        st.caption("（uma_id未取得のためメモ不可）")
        return
    wkey = f"note_{key_prefix}_{uma_id}"
    if wkey not in st.session_state:
        st.session_state[wkey] = db.get_note(uma_id)
    st.text_area(
        f"{horse_name} のメモ", key=wkey, height=height,
        on_change=_save_note_cb, args=(uma_id, horse_name, wkey),
        label_visibility="collapsed",
        placeholder=f"📝 {horse_name} のメモ（離れると自動保存・全レース共通）",
    )
