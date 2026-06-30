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


def stop_on_cloud_db_error(err, action="クラウドDBの読み込み"):
    """Supabaseの設定/スキーマ不備を、赤いTracebackではなく作業手順として表示する。"""
    st.error(f"{action}に失敗しました。")
    st.warning(
        "スマホ版はSupabaseのデータを読んで表示します。"
        "このエラーは、Supabaseの表が未作成・古い、またはStreamlit CloudのSecretsが違う時に出ます。"
    )
    st.markdown(
        """
確認してください。

1. Supabase の SQL Editor で、このフォルダの `supabase/schema.sql` をもう一度ぜんぶ実行する
2. Streamlit Cloud の `Manage app` → `Settings` → `Secrets` に `is_cloud = true` と `[supabase]` の `url` / `key` が入っているか確認する
3. `key` は `anon` ではなく `service_role` を使う
4. Secretsを直したら、Streamlit Cloud の `Reboot app` を押す
5. Macで予想を作った後、必要なら `② データをクラウドへ.command` で過去データも送る
        """
    )
    st.caption(f"内部エラー: {type(err).__name__}")
    st.stop()


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


def gate_label(field_size, umaban):
    """馬番と頭数から 内枠/中枠/外枠 を返す（今回想定の表示用）。"""
    try:
        n = int(field_size); g = int(umaban)
    except (TypeError, ValueError):
        return ""
    if n <= 0 or g <= 0:
        return ""
    pos = (g - 0.5) / n
    if pos <= 1 / 3:
        return "内枠"
    if pos >= 2 / 3:
        return "外枠"
    return "中枠"


def pattern_summary(pattern):
    """好走パターンを「逃◯ 内✕」の短い文字列に（一覧の目印用）。"""
    if not pattern:
        return ""
    short = {"逃げ": "逃", "番手": "番", "内枠": "内", "中枠": "中", "外枠": "外"}
    return " ".join(f"{short[d]}{pattern[d]}" for d in PATTERN_DIMS_ORDER if pattern.get(d))


PATTERN_DIMS_ORDER = ["逃げ", "番手", "内枠", "中枠", "外枠"]
_PATTERN_OPTS = ["—", "◯", "△", "✕"]


def _save_note_cb(uma_id, horse_name, wkey):
    db.set_note(uma_id, horse_name, st.session_state.get(wkey, ""))


def _save_pattern_cb(uma_id, horse_name, dim_keys):
    pat = {}
    for dim, wkey in dim_keys.items():
        v = st.session_state.get(wkey, "—")
        if v and v != "—":
            pat[dim] = v
    db.set_pattern(uma_id, horse_name, pat)


def note_editor(uma_id, horse_name, key_prefix="", height=70):
    """普通メモ欄（uma_id キー）。フォーカスを外すと自動保存。"""
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


def pattern_editor(uma_id, horse_name, key_prefix="", race_ctx=None):
    """好走パターン入力（逃げ/番手/内枠/中枠/外枠 を ◯△✕）。変更で自動保存。
    ◯かつ今回その位置/枠 → 予想+5、✕ → -5。"""
    if not uma_id:
        return
    cur = db.get_pattern(uma_id)
    cap = "好走パターン（◯=得意 △=普通 ✕=苦手）"
    if race_ctx:
        cap += f"　／ 今回想定: {race_ctx}"
    st.caption(cap)
    cols = st.columns(len(db.PATTERN_DIMS))
    dim_keys = {}
    for i, dim in enumerate(db.PATTERN_DIMS):
        wkey = f"pat_{key_prefix}_{uma_id}_{dim}"
        dim_keys[dim] = wkey
        if wkey not in st.session_state:
            st.session_state[wkey] = cur.get(dim) or "—"
    for i, dim in enumerate(db.PATTERN_DIMS):
        with cols[i]:
            st.selectbox(
                dim, _PATTERN_OPTS, key=dim_keys[dim],
                on_change=_save_pattern_cb, args=(uma_id, horse_name, dim_keys),
            )


def memo_editor(uma_id, horse_name, key_prefix="", height=70, race_ctx=None):
    """普通メモ＋好走パターンをまとめて編集。予想画面・結果画面・メモページ共通。"""
    if not uma_id:
        st.caption("（uma_id未取得のためメモ不可）")
        return
    note_editor(uma_id, horse_name, key_prefix=key_prefix, height=height)
    pattern_editor(uma_id, horse_name, key_prefix=key_prefix, race_ctx=race_ctx)
