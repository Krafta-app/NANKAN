# -*- coding: utf-8 -*-
"""Supabase バックエンド（クラウド用）。db.py と同じ関数APIを提供する。
Supabaseの接続情報がある時だけ store.py がこちらを採用する。

接続情報の渡し方（どちらか）:
  - 環境変数 SUPABASE_URL / SUPABASE_KEY
  - Streamlit secrets: [supabase] url=... key=...
キーは service_role を推奨（サーバ側のみで使用、ブラウザには出ない）。"""
import os
import json
import datetime

import db as _local  # 純粋なパース関数（_parse_meta_from_text 等）を再利用

PLACE_NAME_BY_CODE = _local.PLACE_NAME_BY_CODE
# ローカル専用のパース系ヘルパーはそのまま再公開（fetch_results から呼ばれる）
race_id_from_taisen_cache = _local.race_id_from_taisen_cache
CACHE_DIR = _local.CACHE_DIR
PATTERN_DIMS = _local.PATTERN_DIMS
PATTERN_MARKS = _local.PATTERN_MARKS


# ---------------------------------------------------------------------------
# 接続
# ---------------------------------------------------------------------------
def _normalize_url(url):
    """プロジェクトURLを正規化。Data API画面の '.../rest/v1/' を貼っても動くよう除去。"""
    if not url:
        return url
    url = url.strip().rstrip("/")
    for suffix in ("/rest/v1", "/rest", "/auth/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")


def _creds():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not (url and key):
        try:
            import streamlit as st
            if "supabase" in st.secrets:
                s = st.secrets["supabase"]
                url = url or s.get("url")
                key = key or s.get("key")
        except Exception:
            pass
    return _normalize_url(url), key


def is_configured():
    # 強制ローカル（テスト・トラブル時のエスケープ）: KEIBA_FORCE_SQLITE=1
    if os.environ.get("KEIBA_FORCE_SQLITE"):
        return False
    u, k = _creds()
    return bool(u and k)


_CLIENT = None


def _c():
    global _CLIENT
    if _CLIENT is None:
        from supabase import create_client
        url, key = _creds()
        _CLIENT = create_client(url, key)
    return _CLIENT


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def init_db():
    """スキーマは supabase/schema.sql を SQL Editor で作成済みの前提。
    ここでは接続確認のみ（失敗してもアプリは落とさない）。"""
    try:
        _c().table("races").select("race_key").limit(1).execute()
    except Exception as e:
        print(f"[supabase] init check failed: {e}")


# ---------------------------------------------------------------------------
# races
# ---------------------------------------------------------------------------
def register_race(race_key, date, place_code, place_name, race_num,
                  race_id=None, course=None, dist=None, race_name=None,
                  grades=None, eval_list_text=None, uma_ids=None,
                  generated_at=None, data_html=None, data_text=None,
                  post_time=None):
    row = {
        "race_key": race_key, "date": date, "place_code": str(place_code),
        "place_name": place_name, "race_num": int(race_num),
        "race_id": race_id, "course": course, "dist": dist, "post_time": post_time,
        "race_name": race_name,
        "grades_json": json.dumps(grades, ensure_ascii=False) if grades is not None else None,
        "eval_list_text": eval_list_text,
        "uma_ids_json": json.dumps(uma_ids, ensure_ascii=False) if uma_ids is not None else None,
        "generated_at": generated_at or _now(),
    }
    # None は送らない（既存値を消さない＝COALESCE相当）
    row = {k: v for k, v in row.items() if v is not None or k == "race_key"}
    _c().table("races").upsert(row, on_conflict="race_key").execute()
    if data_html is not None:
        _c().table("race_pages").upsert(
            {"race_key": race_key, "data_html": data_html, "data_text": data_text},
            on_conflict="race_key",
        ).execute()


def set_race_id(race_key, race_id):
    _c().table("races").update({"race_id": race_id}).eq("race_key", race_key).execute()


def get_race(race_key):
    r = _c().table("races").select("*").eq("race_key", race_key).limit(1).execute()
    return r.data[0] if r.data else None


def list_archive(date=None, place_code=None, only_with_result=None):
    q = _c().table("races").select("*")
    if date:
        q = q.eq("date", date)
    if place_code:
        q = q.eq("place_code", str(place_code))
    if only_with_result is True:
        q = q.eq("has_result", 1)
    elif only_with_result is False:
        q = q.eq("has_result", 0)
    q = q.order("date", desc=True).order("place_code").order("race_num")
    return q.execute().data or []


def list_dates():
    r = _c().table("races").select("date").order("date", desc=True).execute()
    seen, out = set(), []
    for row in (r.data or []):
        d = row["date"]
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def races_pending_result(on_or_before=None):
    q = _c().table("races").select("*").eq("has_result", 0)
    if on_or_before:
        q = q.lte("date", on_or_before)
    return q.order("date").order("race_num").execute().data or []


# ---------------------------------------------------------------------------
# race_results
# ---------------------------------------------------------------------------
def upsert_results(race_key, rows):
    if not rows:
        return 0
    now = _now()
    payload = [{
        "race_key": race_key, "umaban": r.get("umaban"), "horse_name": r.get("horse_name"),
        "uma_id": r.get("uma_id"), "finish_rank": r.get("finish_rank"),
        "popularity": r.get("popularity"), "time_diff": r.get("time_diff"),
        "fetched_at": now,
    } for r in rows]
    _c().table("race_results").upsert(payload, on_conflict="race_key,umaban").execute()
    _c().table("races").update({"has_result": 1}).eq("race_key", race_key).execute()
    return len(rows)


def get_results(race_key):
    r = _c().table("race_results").select("*").eq("race_key", race_key).order("finish_rank").execute()
    return r.data or []


# ---------------------------------------------------------------------------
# horse_notes
# ---------------------------------------------------------------------------
def get_note(uma_id):
    if not uma_id:
        return ""
    r = _c().table("horse_notes").select("note_text").eq("uma_id", uma_id).limit(1).execute()
    return (r.data[0]["note_text"] if r.data else "") or ""


def set_note(uma_id, horse_name, text):
    if not uma_id:
        return
    text = (text or "").strip()
    if not text:
        try:
            r = _c().table("horse_notes").select("pattern_json").eq("uma_id", uma_id).limit(1).execute()
            pj = r.data[0]["pattern_json"] if r.data else None
        except Exception:
            pj = None
        if pj and pj not in ("", "{}"):
            _c().table("horse_notes").update({"note_text": "", "updated_at": _now()}).eq("uma_id", uma_id).execute()
        else:
            _c().table("horse_notes").delete().eq("uma_id", uma_id).execute()
    else:
        _c().table("horse_notes").upsert(
            {"uma_id": uma_id, "horse_name": horse_name, "note_text": text, "updated_at": _now()},
            on_conflict="uma_id",
        ).execute()


def get_notes_map(uma_ids):
    ids = [u for u in (uma_ids or []) if u]
    if not ids:
        return {}
    r = _c().table("horse_notes").select("uma_id,note_text").in_("uma_id", ids).execute()
    return {row["uma_id"]: row["note_text"] for row in (r.data or [])}


def search_notes(query=""):
    # pattern_json 列が未追加のSupabaseでも落ちないようフォールバック
    for cols in ("uma_id,horse_name,note_text,pattern_json,updated_at",
                 "uma_id,horse_name,note_text,updated_at"):
        try:
            q = _c().table("horse_notes").select(cols)
            if query:
                q = q.ilike("horse_name", f"%{query}%")
            return q.order("updated_at", desc=True).execute().data or []
        except Exception:
            continue
    return []


def get_pattern(uma_id):
    if not uma_id:
        return {}
    try:
        r = _c().table("horse_notes").select("pattern_json").eq("uma_id", uma_id).limit(1).execute()
    except Exception:
        return {}
    pj = r.data[0]["pattern_json"] if r.data else None
    if pj:
        try:
            return json.loads(pj) or {}
        except Exception:
            return {}
    return {}


def set_pattern(uma_id, horse_name, pattern):
    if not uma_id:
        return
    pattern = {k: v for k, v in (pattern or {}).items() if v}
    try:
        if not pattern:
            r = _c().table("horse_notes").select("note_text").eq("uma_id", uma_id).limit(1).execute()
            note = (r.data[0]["note_text"] if r.data else "") or ""
            if note.strip():
                _c().table("horse_notes").update({"pattern_json": None, "updated_at": _now()}).eq("uma_id", uma_id).execute()
            else:
                _c().table("horse_notes").delete().eq("uma_id", uma_id).execute()
        else:
            _c().table("horse_notes").upsert(
                {"uma_id": uma_id, "horse_name": horse_name,
                 "pattern_json": json.dumps(pattern, ensure_ascii=False), "updated_at": _now()},
                on_conflict="uma_id",
            ).execute()
    except Exception as e:
        print(f"[supabase] set_pattern skipped (pattern_json列が必要): {e}")


def get_patterns_map(uma_ids):
    ids = [u for u in (uma_ids or []) if u]
    if not ids:
        return {}
    try:
        r = _c().table("horse_notes").select("uma_id,pattern_json").in_("uma_id", ids).execute()
    except Exception:
        return {}
    out = {}
    for row in (r.data or []):
        pj = row.get("pattern_json")
        if pj:
            try:
                d = json.loads(pj)
            except Exception:
                d = {}
            if d:
                out[row["uma_id"]] = d
    return out


def races_for_uma(uma_id):
    if not uma_id:
        return []
    rr = _c().table("race_results").select("race_key,finish_rank,popularity").eq("uma_id", uma_id).execute().data or []
    if not rr:
        return []
    keys = [r["race_key"] for r in rr]
    races = _c().table("races").select("race_key,date,place_name,race_num,race_name").in_("race_key", keys).execute().data or []
    by_key = {r["race_key"]: r for r in races}
    out = []
    for r in rr:
        meta = by_key.get(r["race_key"], {})
        out.append({
            "race_key": r["race_key"], "finish_rank": r["finish_rank"], "popularity": r["popularity"],
            "date": meta.get("date"), "place_name": meta.get("place_name"),
            "race_num": meta.get("race_num"), "race_name": meta.get("race_name"),
        })
    out.sort(key=lambda x: (x["date"] or ""), reverse=True)
    return out


# ---------------------------------------------------------------------------
# horse_marks
# ---------------------------------------------------------------------------
def set_mark(race_key, uma_id, mark):
    if not mark:
        _c().table("horse_marks").delete().eq("race_key", race_key).eq("uma_id", uma_id).execute()
    else:
        _c().table("horse_marks").upsert(
            {"race_key": race_key, "uma_id": uma_id, "mark": mark, "updated_at": _now()},
            on_conflict="race_key,uma_id",
        ).execute()


def get_marks(race_key):
    r = _c().table("horse_marks").select("uma_id,mark").eq("race_key", race_key).execute()
    return {row["uma_id"]: row["mark"] for row in (r.data or [])}


# ---------------------------------------------------------------------------
# tier別 的中集計
# ---------------------------------------------------------------------------
def tier_hit_stats(date=None, place_code=None):
    q = _c().table("races").select("race_key,grades_json").eq("has_result", 1)
    if date:
        q = q.eq("date", date)
    if place_code:
        q = q.eq("place_code", str(place_code))
    races = q.execute().data or []
    keys = [r["race_key"] for r in races if r.get("grades_json")]
    stats = {}
    if not keys:
        return stats
    # 結果をまとめて取得
    results = _c().table("race_results").select("race_key,horse_name,finish_rank").in_("race_key", keys).execute().data or []
    res_by_key = {}
    for r in results:
        if r["finish_rank"] is not None:
            res_by_key.setdefault(r["race_key"], {})[r["horse_name"]] = r["finish_rank"]

    def _b(t):
        return stats.setdefault(t, {"n": 0, "win": 0, "ren": 0, "fuku": 0})

    for r in races:
        try:
            grades = json.loads(r.get("grades_json") or "{}")
        except Exception:
            grades = {}
        rank_by_name = res_by_key.get(r["race_key"], {})
        for name, tier in grades.items():
            rank = rank_by_name.get(name)
            if rank is None:
                for rn, rv in rank_by_name.items():
                    if rn.replace(" ", "") == name.replace(" ", ""):
                        rank = rv
                        break
            if rank is None:
                continue
            b = _b(tier)
            b["n"] += 1
            if rank == 1:
                b["win"] += 1
            if rank <= 2:
                b["ren"] += 1
            if rank <= 3:
                b["fuku"] += 1
    return stats


# ---------------------------------------------------------------------------
# 共通ロジック（ローカルと同一）
# ---------------------------------------------------------------------------
def get_race_horses(race_key):
    race = get_race(race_key) or {}
    try:
        grades = json.loads(race.get("grades_json") or "{}")
    except Exception:
        grades = {}
    try:
        uma_ids = json.loads(race.get("uma_ids_json") or "{}")
    except Exception:
        uma_ids = {}
    horses = []
    results = get_results(race_key)
    if results:
        for r in results:
            nm = r["horse_name"]
            horses.append({"umaban": r["umaban"], "name": nm,
                           "uma_id": r["uma_id"] or uma_ids.get(nm, ""),
                           "finish_rank": r["finish_rank"], "popularity": r["popularity"],
                           "tier": grades.get(nm, "")})
    elif uma_ids:
        for nm, uid in uma_ids.items():
            horses.append({"umaban": None, "name": nm, "uma_id": uid,
                           "finish_rank": None, "popularity": None, "tier": grades.get(nm, "")})
    return horses


def get_cache_html(race_key):
    r = _c().table("race_pages").select("data_html").eq("race_key", race_key).limit(1).execute()
    return (r.data[0]["data_html"] if r.data else "") or ""


def backfill_from_cache():
    """ローカルの cache/*_dify.json を Supabase へ取り込む（Mac起動時の同期）。
    db.py のパース関数を再利用し、register_race(data_html付き) で送信。"""
    import glob
    cache_dir = _local.CACHE_DIR
    if not os.path.isdir(cache_dir):
        return 0
    try:
        existing = {r["race_key"] for r in _c().table("races").select("race_key").execute().data or []}
    except Exception:
        existing = set()
    n = 0
    for path in glob.glob(os.path.join(cache_dir, "*_dify.json")):
        m = _local._FNAME_RE.search(os.path.basename(path))
        if not m:
            continue
        date, place_code, race_num = m.group(1), m.group(2), int(m.group(3))
        race_key = f"{date}_{place_code}_{race_num}"
        if race_key in existing:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not d.get("grades"):
            continue
        race_name, course, dist = _local._parse_meta_from_text(d.get("data_text", ""))
        race_id = d.get("race_id") or _local.race_id_from_taisen_cache(date, place_code, race_num)
        register_race(
            race_key=race_key, date=date, place_code=place_code,
            place_name=PLACE_NAME_BY_CODE.get(place_code, "地方"), race_num=race_num,
            race_id=race_id, course=course, dist=dist, race_name=race_name,
            grades=d.get("grades"), eval_list_text=d.get("eval_list_text"),
            uma_ids=d.get("uma_ids"), data_html=d.get("data_html"), data_text=d.get("data_text"),
            post_time=d.get("post_time"),
        )
        n += 1
    return n
