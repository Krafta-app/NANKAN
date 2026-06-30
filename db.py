# -*- coding: utf-8 -*-
"""
南関競馬AI 永続ストア (SQLite)

UI(Streamlit) と 結果取得スクリプト(fetch_results.py) の両方から読み書きする。
- races        : 生成した予想レースの台帳（アーカイブ/結果待ち判定）
- race_results : 取得した着順結果（馬単位）
- horse_notes  : 馬ごとメモ（uma_id で永続。別レースでも引き継ぐ）
- horse_marks  : 印を端末非依存に永続化（任意）

DBファイル: data/keiba.db （WALモードでUI=読み/fetch=書きの同時アクセスに対応）
レースキー : race_key = f"{YYYYMMDD}_{place_code}_{r_num}"  ← 既存キャッシュ命名と統一
"""
import os
import re
import glob
import json
import sqlite3
import datetime
from contextlib import contextmanager

_BASE = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(_BASE, "data")
DB_PATH = os.path.join(DB_DIR, "keiba.db")
CACHE_DIR = os.path.join(_BASE, "cache")

PLACE_NAME_BY_CODE = {"10": "大井", "11": "川崎", "12": "船橋", "13": "浦和"}
NK_PLACE_BY_CODE = {"10": "20", "11": "21", "12": "19", "13": "18"}

# 好走パターン（馬ごとメモの2種類目）。位置(逃げ/番手)と枠(内/中/外)を◯△✕で記録。
PATTERN_DIMS = ["逃げ", "番手", "内枠", "中枠", "外枠"]
PATTERN_MARKS = ["◯", "△", "✕"]


def race_id_from_taisen_cache(date, place_code, race_num):
    """既存の cache/taisen_{16桁id}.html から race_id をオフラインで復元。
    過去日は番組表メニューから消えるため、開催特定APIより確実。"""
    nk = NK_PLACE_BY_CODE.get(str(place_code))
    if not nk:
        return None
    prefix = f"{date}{nk}"                 # 例: 2026062819
    suffix = f"{int(race_num):02d}"        # 例: 09
    for p in glob.glob(os.path.join(CACHE_DIR, "taisen_*.html")):
        m = re.search(r"taisen_(\d{16})\.html$", os.path.basename(p))
        if m and m.group(1).startswith(prefix) and m.group(1).endswith(suffix):
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 接続
# ---------------------------------------------------------------------------
def _connect():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    race_key       TEXT PRIMARY KEY,
    date           TEXT,            -- YYYYMMDD
    place_code     TEXT,            -- 10/11/12/13
    place_name     TEXT,            -- 大井/川崎/船橋/浦和
    race_num       INTEGER,
    race_id        TEXT,            -- 16桁 nankan id (/result/{race_id}.do)
    course         TEXT,            -- 例: ダ1400m（外）
    dist           TEXT,            -- 例: 1400
    race_name      TEXT,
    grades_json    TEXT,            -- {馬名: tier}
    eval_list_text TEXT,            -- 【評価一覧】 A[2][5] ...
    uma_ids_json   TEXT,            -- {馬名: uma_id}
    generated_at   TEXT,
    has_result     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS race_results (
    race_key    TEXT,
    umaban      INTEGER,
    horse_name  TEXT,
    uma_id      TEXT,
    finish_rank INTEGER,
    popularity  INTEGER,
    time_diff   REAL,
    fetched_at  TEXT,
    PRIMARY KEY (race_key, umaban)
);

CREATE TABLE IF NOT EXISTS horse_notes (
    uma_id       TEXT PRIMARY KEY,
    horse_name   TEXT,
    note_text    TEXT,
    pattern_json TEXT,           -- 好走パターン {逃げ/番手/内枠/中枠/外枠: ◯/△/✕}
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS horse_marks (
    race_key   TEXT,
    uma_id     TEXT,
    mark       TEXT,
    updated_at TEXT,
    PRIMARY KEY (race_key, uma_id)
);

CREATE INDEX IF NOT EXISTS idx_races_date     ON races(date);
CREATE INDEX IF NOT EXISTS idx_results_uma    ON race_results(uma_id);
CREATE INDEX IF NOT EXISTS idx_results_name   ON race_results(horse_name);
"""


def init_db():
    """スキーマを作成（冪等）。"""
    with get_conn() as c:
        c.executescript(SCHEMA)
        # 既存DBへの列追加（無ければ追加・あれば無視）
        try:
            c.execute("ALTER TABLE horse_notes ADD COLUMN pattern_json TEXT")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# races（台帳）
# ---------------------------------------------------------------------------
def register_race(race_key, date, place_code, place_name, race_num,
                  race_id=None, course=None, dist=None, race_name=None,
                  grades=None, eval_list_text=None, uma_ids=None,
                  generated_at=None, data_html=None, data_text=None):
    """予想生成時/バックフィル時に races へ upsert。has_result は保持。
    data_html/data_text はローカルSQLiteでは未使用（アーカイブはcacheファイルを参照）。
    Supabaseバックエンドでのみ race_pages に保存される。"""
    grades_json = json.dumps(grades, ensure_ascii=False) if grades is not None else None
    uma_ids_json = json.dumps(uma_ids, ensure_ascii=False) if uma_ids is not None else None
    with get_conn() as c:
        c.execute(
            """
            INSERT INTO races (race_key, date, place_code, place_name, race_num,
                               race_id, course, dist, race_name,
                               grades_json, eval_list_text, uma_ids_json, generated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(race_key) DO UPDATE SET
                date=excluded.date,
                place_code=excluded.place_code,
                place_name=excluded.place_name,
                race_num=excluded.race_num,
                race_id=COALESCE(excluded.race_id, races.race_id),
                course=COALESCE(excluded.course, races.course),
                dist=COALESCE(excluded.dist, races.dist),
                race_name=COALESCE(excluded.race_name, races.race_name),
                grades_json=COALESCE(excluded.grades_json, races.grades_json),
                eval_list_text=COALESCE(excluded.eval_list_text, races.eval_list_text),
                uma_ids_json=COALESCE(excluded.uma_ids_json, races.uma_ids_json),
                generated_at=COALESCE(excluded.generated_at, races.generated_at)
            """,
            (race_key, date, place_code, place_name, race_num,
             race_id, course, dist, race_name,
             grades_json, eval_list_text, uma_ids_json,
             generated_at or _now()),
        )


def set_race_id(race_key, race_id):
    with get_conn() as c:
        c.execute("UPDATE races SET race_id=? WHERE race_key=?", (race_id, race_key))


def get_race(race_key):
    with get_conn() as c:
        row = c.execute("SELECT * FROM races WHERE race_key=?", (race_key,)).fetchone()
        return dict(row) if row else None


def list_archive(date=None, place_code=None, only_with_result=None):
    """アーカイブ一覧。date/place_code で絞り込み、日付降順・R昇順。"""
    q = "SELECT * FROM races WHERE 1=1"
    args = []
    if date:
        q += " AND date=?"
        args.append(date)
    if place_code:
        q += " AND place_code=?"
        args.append(place_code)
    if only_with_result is True:
        q += " AND has_result=1"
    elif only_with_result is False:
        q += " AND has_result=0"
    q += " ORDER BY date DESC, place_code, race_num ASC"
    with get_conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def list_dates():
    with get_conn() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT date FROM races ORDER BY date DESC").fetchall()]


def races_pending_result(on_or_before=None):
    """結果未取得（has_result=0）かつ当日以前のレース。fetch_results 用。"""
    q = "SELECT * FROM races WHERE has_result=0"
    args = []
    if on_or_before:
        q += " AND date<=?"
        args.append(on_or_before)
    q += " ORDER BY date ASC, race_num ASC"
    with get_conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ---------------------------------------------------------------------------
# race_results（着順）
# ---------------------------------------------------------------------------
def upsert_results(race_key, rows):
    """rows: list of dict(umaban, horse_name, uma_id, finish_rank, popularity, time_diff)
    1件でも入れば races.has_result=1。"""
    if not rows:
        return 0
    now = _now()
    with get_conn() as c:
        for r in rows:
            c.execute(
                """
                INSERT INTO race_results
                    (race_key, umaban, horse_name, uma_id, finish_rank, popularity, time_diff, fetched_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(race_key, umaban) DO UPDATE SET
                    horse_name=excluded.horse_name,
                    uma_id=COALESCE(excluded.uma_id, race_results.uma_id),
                    finish_rank=excluded.finish_rank,
                    popularity=excluded.popularity,
                    time_diff=excluded.time_diff,
                    fetched_at=excluded.fetched_at
                """,
                (race_key, r.get("umaban"), r.get("horse_name"), r.get("uma_id"),
                 r.get("finish_rank"), r.get("popularity"), r.get("time_diff"), now),
            )
        c.execute("UPDATE races SET has_result=1 WHERE race_key=?", (race_key,))
    return len(rows)


def get_results(race_key):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM race_results WHERE race_key=? ORDER BY finish_rank ASC",
            (race_key,)).fetchall()]


# ---------------------------------------------------------------------------
# horse_notes（馬ごとメモ）
# ---------------------------------------------------------------------------
def get_note(uma_id):
    if not uma_id:
        return ""
    with get_conn() as c:
        row = c.execute("SELECT note_text FROM horse_notes WHERE uma_id=?", (uma_id,)).fetchone()
        return row[0] if row else ""


def set_note(uma_id, horse_name, text):
    """空文字なら削除。"""
    if not uma_id:
        return
    text = (text or "").strip()
    with get_conn() as c:
        if not text:
            row = c.execute("SELECT pattern_json FROM horse_notes WHERE uma_id=?", (uma_id,)).fetchone()
            if row and row[0] and row[0] not in ("", "{}"):
                c.execute("UPDATE horse_notes SET note_text='', updated_at=? WHERE uma_id=?", (_now(), uma_id))
            else:
                c.execute("DELETE FROM horse_notes WHERE uma_id=?", (uma_id,))
        else:
            c.execute(
                """
                INSERT INTO horse_notes (uma_id, horse_name, note_text, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(uma_id) DO UPDATE SET
                    horse_name=excluded.horse_name,
                    note_text=excluded.note_text,
                    updated_at=excluded.updated_at
                """,
                (uma_id, horse_name, text, _now()),
            )


def get_notes_map(uma_ids):
    """{uma_id: note_text} を一括取得（レース表示で全馬分まとめて）。"""
    ids = [u for u in (uma_ids or []) if u]
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    with get_conn() as c:
        rows = c.execute(
            f"SELECT uma_id, note_text FROM horse_notes WHERE uma_id IN ({ph})", ids
        ).fetchall()
        return {r[0]: r[1] for r in rows}


def search_notes(query=""):
    """メモ全件（馬名部分一致で絞り込み）。更新日時の新しい順。"""
    q = "SELECT uma_id, horse_name, note_text, pattern_json, updated_at FROM horse_notes"
    args = []
    if query:
        q += " WHERE horse_name LIKE ?"
        args.append(f"%{query}%")
    q += " ORDER BY updated_at DESC"
    with get_conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ---------------------------------------------------------------------------
# horse_notes（好走パターン）
# ---------------------------------------------------------------------------
def get_pattern(uma_id):
    """{逃げ/番手/内枠/中枠/外枠: ◯/△/✕} を返す。無ければ {}。"""
    if not uma_id:
        return {}
    with get_conn() as c:
        row = c.execute("SELECT pattern_json FROM horse_notes WHERE uma_id=?", (uma_id,)).fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0]) or {}
        except Exception:
            return {}
    return {}


def set_pattern(uma_id, horse_name, pattern):
    """好走パターンを保存。空マークは除去。全空かつ普通メモも無ければ行ごと削除。"""
    if not uma_id:
        return
    pattern = {k: v for k, v in (pattern or {}).items() if v}
    pj = json.dumps(pattern, ensure_ascii=False) if pattern else None
    with get_conn() as c:
        if not pattern:
            row = c.execute("SELECT note_text FROM horse_notes WHERE uma_id=?", (uma_id,)).fetchone()
            if row and (row[0] or "").strip():
                c.execute("UPDATE horse_notes SET pattern_json=NULL, updated_at=? WHERE uma_id=?", (_now(), uma_id))
            else:
                c.execute("DELETE FROM horse_notes WHERE uma_id=?", (uma_id,))
        else:
            c.execute(
                """
                INSERT INTO horse_notes (uma_id, horse_name, pattern_json, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(uma_id) DO UPDATE SET
                    horse_name=excluded.horse_name,
                    pattern_json=excluded.pattern_json,
                    updated_at=excluded.updated_at
                """,
                (uma_id, horse_name, pj, _now()),
            )


def get_patterns_map(uma_ids):
    """{uma_id: pattern_dict} を一括取得（採点・展開表示で全馬分まとめて）。"""
    ids = [u for u in (uma_ids or []) if u]
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    out = {}
    with get_conn() as c:
        rows = c.execute(
            f"SELECT uma_id, pattern_json FROM horse_notes WHERE uma_id IN ({ph})", ids
        ).fetchall()
    for u, pj in rows:
        if pj:
            try:
                d = json.loads(pj)
            except Exception:
                d = {}
            if d:
                out[u] = d
    return out


def races_for_uma(uma_id):
    """その馬が出走した（結果取得済みの）レース一覧。メモページのリンク用。"""
    if not uma_id:
        return []
    with get_conn() as c:
        rows = c.execute(
            """
            SELECT rr.race_key, rr.finish_rank, rr.popularity,
                   r.date, r.place_name, r.race_num, r.race_name
            FROM race_results rr
            LEFT JOIN races r ON r.race_key = rr.race_key
            WHERE rr.uma_id=?
            ORDER BY r.date DESC
            """,
            (uma_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# horse_marks（印・任意）
# ---------------------------------------------------------------------------
def set_mark(race_key, uma_id, mark):
    with get_conn() as c:
        if not mark:
            c.execute("DELETE FROM horse_marks WHERE race_key=? AND uma_id=?", (race_key, uma_id))
        else:
            c.execute(
                """
                INSERT INTO horse_marks (race_key, uma_id, mark, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(race_key, uma_id) DO UPDATE SET
                    mark=excluded.mark, updated_at=excluded.updated_at
                """,
                (race_key, uma_id, mark, _now()),
            )


def get_marks(race_key):
    with get_conn() as c:
        return {r[0]: r[1] for r in c.execute(
            "SELECT uma_id, mark FROM horse_marks WHERE race_key=?", (race_key,)).fetchall()}


# ---------------------------------------------------------------------------
# tier別 的中集計
# ---------------------------------------------------------------------------
def tier_hit_stats(date=None, place_code=None):
    """結果取得済みレースについて、予想tier別に 1着/連対/複勝 をカウント。
    grades_json(馬名→tier) と race_results(馬名→着順) を馬名で結合。"""
    q = "SELECT race_key, grades_json FROM races WHERE has_result=1 AND grades_json IS NOT NULL"
    args = []
    if date:
        q += " AND date=?"
        args.append(date)
    if place_code:
        q += " AND place_code=?"
        args.append(place_code)
    stats = {}  # tier -> {n, win, ren, fuku}

    def _bucket(t):
        return stats.setdefault(t, {"n": 0, "win": 0, "ren": 0, "fuku": 0})

    with get_conn() as c:
        races = c.execute(q, args).fetchall()
        for rk, gj in races:
            try:
                grades = json.loads(gj) if gj else {}
            except Exception:
                grades = {}
            if not grades:
                continue
            res = c.execute(
                "SELECT horse_name, finish_rank FROM race_results WHERE race_key=?", (rk,)
            ).fetchall()
            rank_by_name = {r[0]: r[1] for r in res if r[1] is not None}
            for name, tier in grades.items():
                rank = rank_by_name.get(name)
                if rank is None:
                    # 馬名の表記ゆれを軽く吸収
                    for rn, rv in rank_by_name.items():
                        if rn == name or rn.replace(" ", "") == name.replace(" ", ""):
                            rank = rv
                            break
                if rank is None:
                    continue
                b = _bucket(tier)
                b["n"] += 1
                if rank == 1:
                    b["win"] += 1
                if rank <= 2:
                    b["ren"] += 1
                if rank <= 3:
                    b["fuku"] += 1
    return stats


# ---------------------------------------------------------------------------
# 既存 cache/*_dify.json からのバックフィル
# ---------------------------------------------------------------------------
_FNAME_RE = re.compile(r"(\d{8})_(\d{2})_(\d{1,2})_dify\.json$")


def _parse_meta_from_text(data_text):
    """data_text 先頭から race_name / course / dist をざっくり抽出。"""
    race_name, course, dist = "", "", ""
    lines = [l for l in (data_text or "").splitlines() if l.strip()]
    # 1行目: 📅 2026/06/22 浦和10R / 2行目あたりにレース名＋コース
    for l in lines[1:4]:
        if "【" in l:
            break
        m = re.search(r"(ダ|芝)\s?([\d,]{3,5})\s*[mｍ]\s*(（[内外]）)?", l)
        if m:
            course = m.group(0).strip()
            dist = m.group(2).replace(",", "")
            name = l.split(m.group(1))[0]
            # "...特別 Ｃ３(一)詳細  浦和" → 末尾の「詳細」「競馬場名」を除去
            name = name.replace("詳細", " ")
            for pn in ("大井", "川崎", "船橋", "浦和"):
                name = name.replace(pn, " ")
            race_name = re.sub(r"\s+", " ", name).strip() or l.strip()
            break
        if not race_name:
            race_name = re.sub(r"\s+", " ", l.replace("詳細", " ")).strip()
    return race_name, course, dist


def backfill_from_cache():
    """cache/*_dify.json を走査し races へ登録（未登録のみ・冪等）。
    race_id は旧キャッシュには無いので NULL（fetch_results が後で補完）。"""
    if not os.path.isdir(CACHE_DIR):
        return 0
    existing = set()
    with get_conn() as c:
        for r in c.execute("SELECT race_key FROM races").fetchall():
            existing.add(r[0])
    n = 0
    for path in glob.glob(os.path.join(CACHE_DIR, "*_dify.json")):
        m = _FNAME_RE.search(os.path.basename(path))
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
        grades = d.get("grades")
        if not grades:
            continue  # 予想未確定（raw/pace のみ等）はスキップ
        race_name, course, dist = _parse_meta_from_text(d.get("data_text", ""))
        # race_id: 新しいキャッシュは内部に保持。無ければ taisen キャッシュ名から復元。
        race_id = d.get("race_id") or race_id_from_taisen_cache(date, place_code, race_num)
        try:
            generated_at = datetime.datetime.fromtimestamp(
                os.path.getmtime(path)).isoformat(timespec="seconds")
        except Exception:
            generated_at = None
        register_race(
            race_key=race_key, date=date, place_code=place_code,
            place_name=PLACE_NAME_BY_CODE.get(place_code, "地方"),
            race_num=race_num, race_id=race_id, course=course, dist=dist,
            race_name=race_name, grades=grades,
            eval_list_text=d.get("eval_list_text"),
            uma_ids=d.get("uma_ids"), generated_at=generated_at,
        )
        n += 1
    return n


def get_race_horses(race_key):
    """レースの出走馬一覧を返す。
    結果があれば着順順、無ければ予想の uma_ids から。tier は grades から付与。
    -> [{umaban, name, uma_id, finish_rank, popularity, tier}]"""
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
            horses.append({
                "umaban": r["umaban"], "name": nm,
                "uma_id": r["uma_id"] or uma_ids.get(nm, ""),
                "finish_rank": r["finish_rank"], "popularity": r["popularity"],
                "tier": grades.get(nm, ""),
            })
    elif uma_ids:
        for nm, uid in uma_ids.items():
            horses.append({
                "umaban": None, "name": nm, "uma_id": uid,
                "finish_rank": None, "popularity": None, "tier": grades.get(nm, ""),
            })
    return horses


def get_cache_html(race_key):
    """race_key に対応する dify.json の data_html を返す（アーカイブ表示用）。"""
    m = re.match(r"(\d{8})_(\d{2})_(\d{1,2})$", race_key)
    if not m:
        return ""
    path = os.path.join(CACHE_DIR, f"{race_key}_dify.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("data_html", "")
    except Exception:
        return ""


if __name__ == "__main__":
    init_db()
    added = backfill_from_cache()
    print(f"init_db OK / backfilled {added} races")
    print("dates:", list_dates())
