"""NAR公式の月次レースCSVから、南関予想に使う実績データベースを作る。

ばんえい予想（`../../ばんえい予想/banei_core.py`）で効果が出ている3つの考え方を
南関へ移植するための土台。

1. 騎手・厩舎の「当季成績」を母数補正（縮約）つきで持ち、
   乗り替わりは *騎手単体ではなく騎手×厩舎* の差で見る。
2. 過去走を5走のスクレイピングに頼らず、収録月の全出走から総ざらいする
   （直接対決・物差し馬の材料が増える）。
3. 対象レースより未来のデータは一切使わない（as-of 厳守）。

CSVの入手元は NAR公式のレース情報ダウンロード（月次ZIP）。
`cache/nar_speed_csv/monthly/*.zip` と、展開済みCSVを置いたフォルダの両方を読む。
"""

from __future__ import annotations

import csv
import io
import os
import re
import threading
import time
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent

# 南関4場。ここに無い競馬場も対戦材料としては読み込む（交流・転入馬のため）。
NANKAN_PLACES = ("大井", "川崎", "船橋", "浦和")

# 南関の16桁レースIDに出てくる場コード。
NK_CODE_TO_PLACE = {"20": "大井", "21": "川崎", "19": "船橋", "18": "浦和"}
PLACE_TO_NK_CODE = {v: k for k, v in NK_CODE_TO_PLACE.items()}

PLACE_ALIASES = {"帯広ば": "帯広"}

# 母数補正の強さ。ばんえいと同じ12（12騎乗ぶんの事前分布へ寄せる）。
PRIOR_STRENGTH = 12.0
RECENT_PRIOR_STRENGTH = 8.0
RECENT_WINDOW_DAYS = 30
MIN_RECENT_STARTS = 8


# ==========================================================
# 探索パス
# ==========================================================
def _candidate_sources() -> List[Path]:
    """月次CSV/ZIPを探すフォルダ。環境変数 NAR_CSV_DIRS で追加できる。"""
    found: List[Path] = []
    env = os.environ.get("NAR_CSV_DIRS", "")
    for chunk in env.split(os.pathsep):
        chunk = chunk.strip()
        if chunk:
            found.append(Path(chunk).expanduser())
    found.append(ROOT / "data" / "nar_csv")
    found.append(ROOT / "cache" / "nar_speed_csv" / "monthly")
    found.append(ROOT / "cache" / "nar_speed_csv" / "daily")
    # ばんえい予想プロジェクトの data/（同じ月次CSVの展開済み）。
    found.append(ROOT.parent.parent / "ばんえい予想" / "data")
    return [p for p in found if p.exists()]


def _iter_source_files() -> List[Path]:
    files: List[Path] = []
    seen = set()
    for base in _candidate_sources():
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            name = path.name.lower()
            if name.endswith(".zip") and "race" in name:
                pass
            elif name.endswith(".csv") and ("horselist" in name or "racelist" in name):
                pass
            else:
                continue
            key = path.resolve()
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def _fingerprint(files: Sequence[Path]) -> Tuple:
    out = []
    for p in files:
        try:
            st = p.stat()
            out.append((str(p), int(st.st_mtime), st.st_size))
        except OSError:
            continue
    return tuple(out)


# ==========================================================
# 値の正規化
# ==========================================================
def _text(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).replace(" ", "").replace("　", "").strip()


def _norm_place(value) -> str:
    v = _text(value)
    return PLACE_ALIASES.get(v, v)


def _to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_float(value, default=None):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_csv_time(value) -> Optional[float]:
    """CSVの走破タイム（例 '1499' = 1分49秒9）を秒へ。'2062'は2分06秒2。"""
    raw = re.sub(r"[^\d]", "", str(value or ""))
    if not raw or len(raw) < 3:
        return None
    tenths = int(raw[-1])
    seconds = int(raw[-3:-1])
    minutes = int(raw[:-3]) if len(raw) > 3 else 0
    total = minutes * 60 + seconds + tenths / 10.0
    return total if total > 0 else None


def parse_csv_date(value) -> Optional[date]:
    raw = re.sub(r"[^\d]", "", str(value or ""))
    if len(raw) != 8:
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


_CORNER_TOKEN = re.compile(r"\(([^)]*)\)|(\d+)")


def parse_corner_order(text: str) -> Dict[int, int]:
    """コーナー通過順文字列を {馬番: その地点の位置} へ。

    '(4,5,10),(1,9),(3,6)-7-(2,8)' のように、括弧は横並び（同位置）を表す。
    括弧の中の頭数ぶんだけ次の位置が進む。
    """
    result: Dict[int, int] = {}
    pos = 1
    for m in _CORNER_TOKEN.finditer(str(text or "")):
        if m.group(1) is not None:
            nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
            if not nums:
                continue
            for n in nums:
                result.setdefault(n, pos)
            pos += len(nums)
        else:
            n = int(m.group(2))
            result.setdefault(n, pos)
            pos += 1
    return result


def season_start(target: date) -> date:
    """地方競馬の年度（4月1日始まり）。"""
    return date(target.year if target.month >= 4 else target.year - 1, 4, 1)


# ==========================================================
# レコード
# ==========================================================
class RaceRow:
    """1レースの条件。メモリを抑えるため __slots__ で持つ。"""

    __slots__ = (
        "place", "day", "rno", "dist", "turn", "surface", "baba",
        "tenko", "field_size", "condition", "race_name", "corner_positions",
    )

    def __init__(self, place, day, rno, dist=0, turn="", surface="", baba="",
                 tenko="", field_size=0, condition="", race_name="", corner_positions=None):
        self.place = place
        self.day = day
        self.rno = rno
        self.dist = dist
        self.turn = turn
        self.surface = surface
        self.baba = baba
        self.tenko = tenko
        self.field_size = field_size
        self.condition = condition
        self.race_name = race_name
        self.corner_positions = corner_positions or {}

    @property
    def key(self):
        return (self.place, self.day, self.rno)


class RunRow:
    """1頭ぶんの出走記録。"""

    __slots__ = (
        "place", "day", "rno", "umaban", "waku", "horse", "sex", "age",
        "jockey", "trainer", "owner", "carried", "body_weight", "body_diff",
        "rank", "time_sec", "agari", "pop", "dist", "field_size", "baba",
        "surface", "race_name", "condition", "pas",
    )

    def __init__(self, place, day, rno, umaban, waku, horse, sex, age, jockey,
                 trainer, owner, carried, body_weight, body_diff, rank, time_sec,
                 agari, pop, dist=0, field_size=0, baba="", surface="",
                 race_name="", condition="", pas=""):
        self.place = place
        self.day = day
        self.rno = rno
        self.umaban = umaban
        self.waku = waku
        self.horse = horse
        self.sex = sex
        self.age = age
        self.jockey = jockey
        self.trainer = trainer
        self.owner = owner
        self.carried = carried
        self.body_weight = body_weight
        self.body_diff = body_diff
        self.rank = rank
        self.time_sec = time_sec
        self.agari = agari
        self.pop = pop
        self.dist = dist
        self.field_size = field_size
        self.baba = baba
        self.surface = surface
        self.race_name = race_name
        self.condition = condition
        self.pas = pas

    @property
    def race_key(self):
        return (self.place, self.day, self.rno)

    @property
    def finished(self):
        return self.rank is not None


# ==========================================================
# 読み込み
# ==========================================================
def _read_csv_rows(raw: bytes) -> Iterable[Mapping[str, str]]:
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        return list(csv.DictReader(io.StringIO(text)))
    return []


def _collect_sources() -> Dict[str, Dict[str, Tuple[Path, str]]]:
    """月キー -> {'horselist': (ファイル, ZIP内メンバー名), ...}。同月が複数あれば新しい方。

    ここではバイト列を持たず場所だけ覚える（読み込み時のピークメモリを抑える）。
    """
    located: Dict[str, Dict[str, Tuple[Path, str]]] = {}
    stamps: Dict[Tuple[str, str], float] = {}

    def offer(month: str, kind: str, path: Path, member: str, mtime: float):
        prev = stamps.get((month, kind))
        if prev is not None and prev >= mtime:
            return
        stamps[(month, kind)] = mtime
        located.setdefault(month, {})[kind] = (path, member)

    for path in _iter_source_files():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(path) as zf:
                    members = zf.namelist()
            except (zipfile.BadZipFile, OSError):
                continue
            for member in members:
                low = member.lower()
                kind = "horselist" if "horselist" in low else "racelist" if "racelist" in low else ""
                if not kind:
                    continue
                m = re.search(r"(\d{6})", Path(member).name)
                if m:
                    offer(m.group(1), kind, path, member, mtime)
        else:
            low = path.name.lower()
            kind = "horselist" if "horselist" in low else "racelist" if "racelist" in low else ""
            m = re.search(r"(\d{6})", path.name)
            if kind and m:
                offer(m.group(1), kind, path, "", mtime)
    return located


def _load_bytes(location: Optional[Tuple[Path, str]]) -> bytes:
    if not location:
        return b""
    path, member = location
    try:
        if member:
            with zipfile.ZipFile(path) as zf:
                return zf.read(member)
        return path.read_bytes()
    except (zipfile.BadZipFile, OSError, KeyError):
        return b""


class NarHistory:
    """収録済みNAR出走のインデックス。"""

    def __init__(self, races: Dict[Tuple[str, date, int], RaceRow], runs: List[RunRow]):
        self.races = races
        self.runs = runs
        self.by_race: Dict[Tuple[str, date, int], List[RunRow]] = {}
        self.by_horse: Dict[str, List[RunRow]] = {}
        for run in runs:
            self.by_race.setdefault(run.race_key, []).append(run)
            self.by_horse.setdefault(run.horse, []).append(run)
        for rows in self.by_race.values():
            rows.sort(key=lambda r: r.umaban)
        for rows in self.by_horse.values():
            rows.sort(key=lambda r: (r.day, r.rno), reverse=True)
        days = [r.day for r in runs]
        self.first_day = min(days) if days else None
        self.last_day = max(days) if days else None

    # ---- 基本 ----
    def __bool__(self) -> bool:
        return bool(self.runs)

    def coverage(self) -> str:
        if not self.runs:
            return "収録なし"
        return f"{self.first_day:%Y-%m-%d}〜{self.last_day:%Y-%m-%d}／{len(self.runs):,}出走"

    def race(self, place: str, day: date, rno: int) -> Optional[RaceRow]:
        return self.races.get((_norm_place(place), day, int(rno)))

    def race_entries(self, place: str, day: date, rno: int) -> List[RunRow]:
        return list(self.by_race.get((_norm_place(place), day, int(rno)), []))

    def horse_runs(
        self,
        horse: str,
        before: Optional[date] = None,
        before_rno: Optional[int] = None,
        before_place: str = "",
        limit: Optional[int] = None,
        places: Optional[Sequence[str]] = None,
    ) -> List[RunRow]:
        """馬名の過去走（新しい順）。`before` 当日は `before_rno` より前のRのみ。"""
        rows = self.by_horse.get(_text(horse), [])
        out: List[RunRow] = []
        for run in rows:
            if before is not None:
                if run.day > before:
                    continue
                if run.day == before:
                    if before_rno is None:
                        continue
                    same_place = (not before_place) or run.place == _norm_place(before_place)
                    if not (same_place and run.rno < int(before_rno)):
                        continue
            if places and run.place not in places:
                continue
            out.append(run)
            if limit and len(out) >= limit:
                break
        return out

    def rows_before(self, day: date, since: Optional[date] = None) -> List[RunRow]:
        return [
            r for r in self.runs
            if r.day < day and (since is None or r.day >= since) and r.rank is not None
        ]


def _build(located: Mapping[str, Mapping[str, Tuple[Path, str]]],
           places: Optional[frozenset] = None,
           since: Optional[date] = None) -> NarHistory:
    races: Dict[Tuple[str, date, int], RaceRow] = {}
    runs: Dict[Tuple[str, date, int, int], RunRow] = {}

    for month in sorted(located):
        blob = located[month]
        for row in _read_csv_rows(_load_bytes(blob.get("racelist"))):
            place = _norm_place(row.get("競馬場"))
            day = parse_csv_date(row.get("競走年月日"))
            rno = _to_int(row.get("レース番号"))
            if not place or day is None or rno is None:
                continue
            if places and place not in places:
                continue
            if since and day < since:
                continue
            corner: Dict[int, List[int]] = {}
            for i in range(1, 9):
                order = row.get(f"コーナー通過順{i}") or ""
                if not order.strip():
                    continue
                for umaban, pos in parse_corner_order(order).items():
                    corner.setdefault(umaban, []).append(pos)
            races[(place, day, rno)] = RaceRow(
                place=place, day=day, rno=rno,
                dist=_to_int(row.get("距離"), 0) or 0,
                turn=_text(row.get("回り")),
                surface=_text(row.get("芝ダート区分")),
                baba=_text(row.get("馬場")),
                tenko=_text(row.get("天候")),
                field_size=_to_int(row.get("頭数"), 0) or 0,
                condition=_text(row.get("条件")),
                race_name=_text(row.get("レース名")),
                corner_positions=corner,
            )

        for row in _read_csv_rows(_load_bytes(blob.get("horselist"))):
            place = _norm_place(row.get("競馬場"))
            day = parse_csv_date(row.get("競走年月日"))
            rno = _to_int(row.get("レース番号"))
            umaban = _to_int(row.get("馬番"))
            horse = _text(row.get("馬名"))
            if not place or day is None or rno is None or umaban is None or not horse:
                continue
            if places and place not in places:
                continue
            if since and day < since:
                continue
            race = races.get((place, day, rno))
            pas_list = race.corner_positions.get(umaban, []) if race else []
            runs[(place, day, rno, umaban)] = RunRow(
                place=place, day=day, rno=rno, umaban=umaban,
                waku=_to_int(row.get("枠番"), 0) or 0,
                horse=horse,
                sex=_text(row.get("性")),
                age=_to_int(row.get("齢"), 0) or 0,
                jockey=_text(row.get("騎手名")),
                trainer=_text(row.get("調教師")),
                owner=_text(row.get("馬主氏名")),
                carried=_to_float(row.get("負担重量"), 0.0) or 0.0,
                body_weight=_to_int(row.get("馬体重")),
                body_diff=_to_int(str(row.get("馬体重増減") or "").replace("+", "")),
                rank=_to_int(row.get("着順")),
                time_sec=parse_csv_time(row.get("タイム")),
                agari=_to_float(row.get("上がり3F")),
                pop=_to_int(row.get("人気")),
                dist=race.dist if race else 0,
                field_size=race.field_size if race else 0,
                baba=race.baba if race else "",
                surface=race.surface if race else "",
                race_name=race.race_name if race else "",
                condition=race.condition if race else "",
                pas="-".join(str(p) for p in pas_list),
            )

    return NarHistory(races, list(runs.values()))


_CACHE_LOCK = threading.Lock()
_CACHE: Dict[Tuple, NarHistory] = {}
# ファイル一覧の走査は安くないので、しばらくは同じ結果を使い回す。
_SCAN_TTL_SECONDS = 60.0
_SCAN_CACHE: Dict[str, object] = {"at": 0.0, "files": None, "fingerprint": None}


def _scan(force: bool = False) -> Tuple:
    now = time.monotonic()
    with _CACHE_LOCK:
        fresh = (not force
                 and _SCAN_CACHE["fingerprint"] is not None
                 and (now - float(_SCAN_CACHE["at"])) < _SCAN_TTL_SECONDS)
        if fresh:
            return _SCAN_CACHE["fingerprint"]  # type: ignore[return-value]
    fingerprint = _fingerprint(_iter_source_files())
    with _CACHE_LOCK:
        _SCAN_CACHE["at"] = now
        _SCAN_CACHE["fingerprint"] = fingerprint
    return fingerprint


def _default_places() -> Optional[frozenset]:
    """既定は南関4場のみ。NAR_CSV_PLACES='all' で全場、カンマ区切りで任意指定。"""
    raw = os.environ.get("NAR_CSV_PLACES", "").strip()
    if not raw:
        return frozenset(NANKAN_PLACES)
    if raw.lower() == "all":
        return None
    return frozenset(_norm_place(x) for x in raw.split(",") if x.strip())


def _default_since() -> Optional[date]:
    """既定は全収録期間。NAR_CSV_SINCE='2026-04-01' で絞れる。"""
    raw = os.environ.get("NAR_CSV_SINCE", "").strip()
    return parse_csv_date(raw) if raw else None


def get_history(
    places: Optional[Iterable[str]] = None,
    since: Optional[date] = None,
    force: bool = False,
) -> NarHistory:
    """月次CSVを読み込んだ履歴を返す（ファイルが変わるまで再利用）。

    既定の収録範囲は南関4場。環境変数 NAR_CSV_PLACES / NAR_CSV_SINCE で変えられる。
    """
    place_set = frozenset(_norm_place(p) for p in places) if places else _default_places()
    since_day = since if since is not None else _default_since()
    key = (_scan(force=force), place_set, since_day)
    with _CACHE_LOCK:
        if not force and key in _CACHE:
            return _CACHE[key]
    history = _build(_collect_sources(), places=place_set, since=since_day)
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[key] = history
    return history


# ==========================================================
# 名前の突き合わせ
# ==========================================================
# CSVの騎手名・調教師名は「苗字2文字＋名前1文字」等に切り詰められている
# （藤本現世→藤本現、御神本訓史→御神訓）。スクレイピング側のフルネームから
# CSV表記を引くために、文字の包含でいちばん近いものを選ぶ。
def _resolve_short_name(full: str, pool: Mapping[str, int],
                        cache: Optional[Dict[str, str]] = None) -> str:
    """フルネームから、この母集団に実在するCSV表記を1つ選ぶ。

    キャッシュは母集団ごとに持つ（母集団が違えば答えも違うため、
    プロセス全体で使い回してはいけない）。
    """
    full = _text(full)
    if not full:
        return ""
    if cache is not None and full in cache:
        return cache[full]
    best = ""
    if full in pool:
        best = full
    else:
        chars = set(full)
        candidates = []
        for short in pool:
            if len(short) > len(full) or not short or short[0] != full[0]:
                continue
            if not set(short) <= chars:
                continue
            prefix = 0
            for a, b in zip(short, full):
                if a != b:
                    break
                prefix += 1
            candidates.append((-prefix, len(full) - len(short), -pool[short], short))
        if candidates:
            candidates.sort()
            best = candidates[0][3]
    if cache is not None:
        cache[full] = best
    return best


# ==========================================================
# 成績（母数補正つき）
# ==========================================================
class RateStats:
    __slots__ = ("starts", "wins", "seconds", "thirds", "top3", "win_rate",
                 "top3_rate", "adj_win", "adj_top3")

    def __init__(self, starts=0, wins=0, seconds=0, thirds=0,
                 win_rate=0.0, top3_rate=0.0, adj_win=0.0, adj_top3=0.0):
        self.starts = starts
        self.wins = wins
        self.seconds = seconds
        self.thirds = thirds
        self.top3 = wins + seconds + thirds
        self.win_rate = win_rate
        self.top3_rate = top3_rate
        self.adj_win = adj_win
        self.adj_top3 = adj_top3

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.seconds}-{self.thirds}-{self.starts - self.top3}"

    def as_dict(self) -> Dict[str, object]:
        return {
            "starts": self.starts, "wins": self.wins, "seconds": self.seconds,
            "thirds": self.thirds, "top3": self.top3, "record": self.record,
            "win_rate": round(self.win_rate, 4), "top3_rate": round(self.top3_rate, 4),
            "adj_win": round(self.adj_win, 4), "adj_top3": round(self.adj_top3, 4),
        }


def _rate_stats(rows: Sequence[RunRow], prior_win: float, prior_top3: float,
                prior_strength: float = PRIOR_STRENGTH) -> RateStats:
    valid = [r for r in rows if r.rank is not None]
    starts = len(valid)
    wins = sum(1 for r in valid if r.rank == 1)
    seconds = sum(1 for r in valid if r.rank == 2)
    thirds = sum(1 for r in valid if r.rank == 3)
    top3 = wins + seconds + thirds
    return RateStats(
        starts=starts, wins=wins, seconds=seconds, thirds=thirds,
        win_rate=wins / starts if starts else 0.0,
        top3_rate=top3 / starts if starts else 0.0,
        adj_win=(wins + prior_strength * prior_win) / (starts + prior_strength),
        adj_top3=(top3 + prior_strength * prior_top3) / (starts + prior_strength),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ConnectionModel:
    """ある開催日時点の、騎手・厩舎の当季成績。

    対象日より前（同日は対象Rより前）のデータしか使わない。
    ばんえい予想の `connection_stats` / `jockey_intent_signals` を南関へ移植した。
    """

    def __init__(self, history: NarHistory, target: date, places: Sequence[str] = NANKAN_PLACES,
                 rno: Optional[int] = None, place: str = "",
                 window_days: Optional[int] = None):
        """window_days を渡すと「対象日から遡ってN日」を母集団にする。

        ばんえいは当季（4月始まり）で足りるが、南関は年度初めの母数が薄く、
        検証では遡り365日のほうが当たった（scripts/backtest_jockey_model.py）。
        """
        self.history = history
        self.target = target
        self.places = tuple(places) if places else ()
        self.window_days = window_days
        self.season_from = (target - timedelta(days=window_days)) if window_days else season_start(target)
        place_set = set(self.places)
        rows: List[RunRow] = []
        for run in history.runs:
            if run.rank is None or run.day < self.season_from:
                continue
            if run.day > target:
                continue
            if run.day == target:
                # 同日は対象レースより前のRだけ（未来情報を使わない）。
                if rno is None or not (run.place == _norm_place(place) and run.rno < int(rno)):
                    continue
            if place_set and run.place not in place_set:
                continue
            rows.append(run)
        self.rows = rows
        starts = len(rows)
        self.prior_win = (sum(1 for r in rows if r.rank == 1) / starts) if starts else 0.1
        self.prior_top3 = (sum(1 for r in rows if r.rank <= 3) / starts) if starts else 0.3

        self._by_jockey: Dict[str, List[RunRow]] = {}
        self._by_trainer: Dict[str, List[RunRow]] = {}
        self._by_combo: Dict[Tuple[str, str], List[RunRow]] = {}
        for r in rows:
            self._by_jockey.setdefault(r.jockey, []).append(r)
            self._by_trainer.setdefault(r.trainer, []).append(r)
            self._by_combo.setdefault((r.jockey, r.trainer), []).append(r)
        self._jockey_pool = {k: len(v) for k, v in self._by_jockey.items()}
        self._trainer_pool = {k: len(v) for k, v in self._by_trainer.items()}
        self._jockey_names: Dict[str, str] = {}
        self._trainer_names: Dict[str, str] = {}
        self._by_venue: Dict[Tuple[str, str], List[RunRow]] = {}
        for r in rows:
            self._by_venue.setdefault((r.jockey, r.place), []).append(r)

    # ---- 名前 ----
    def jockey_key(self, name: str) -> str:
        return _resolve_short_name(name, self._jockey_pool, self._jockey_names)

    def trainer_key(self, name: str) -> str:
        return _resolve_short_name(name, self._trainer_pool, self._trainer_names)

    # ---- 成績 ----
    def jockey_stats(self, jockey: str) -> Optional[RateStats]:
        key = self.jockey_key(jockey)
        rows = self._by_jockey.get(key)
        if not rows:
            return None
        return _rate_stats(rows, self.prior_win, self.prior_top3)

    def trainer_stats(self, trainer: str) -> Optional[RateStats]:
        rows = self._by_trainer.get(self.trainer_key(trainer))
        if not rows:
            return None
        return _rate_stats(rows, self.prior_win, self.prior_top3)

    def combo_stats(self, jockey: str, trainer: str) -> Optional[RateStats]:
        rows = self._by_combo.get((self.jockey_key(jockey), self.trainer_key(trainer)))
        if not rows:
            return None
        return _rate_stats(rows, self.prior_win, self.prior_top3)

    def stable_stats(self, jockey: str, trainer: str) -> Optional[RateStats]:
        """「この騎手×この厩舎」を、その騎手自身の当季成績へ縮約して返す。

        騎乗数が薄いぶんは騎手単体へ寄せる。この厩舎での騎乗が無ければ騎手単体そのもの。
        乗り替わりの比較はこの値どうしで行う（ばんえいでAUCが上がった作り）。
        """
        base = self.jockey_stats(jockey)
        if base is None:
            return None
        rows = self._by_combo.get((self.jockey_key(jockey), self.trainer_key(trainer)), [])
        return _rate_stats(rows, base.adj_win, base.adj_top3)

    def venue_stats(self, jockey: str, place: str) -> Optional[RateStats]:
        """その騎手の、この競馬場での成績。薄いぶんは騎手単体へ縮約する。"""
        base = self.jockey_stats(jockey)
        if base is None:
            return None
        rows = self._by_venue.get((self.jockey_key(jockey), _norm_place(place)), [])
        return _rate_stats(rows, base.adj_win, base.adj_top3)

    def horse_jockey_stats(self, jockey: str, horse: str) -> Optional[RateStats]:
        jkey = self.jockey_key(jockey)
        hname = _text(horse)
        rows = [r for r in self._by_jockey.get(jkey, []) if r.horse == hname]
        if not rows:
            return None
        return _rate_stats(rows, self.prior_win, self.prior_top3)

    def recent_stats(self, jockey: str, days: int = RECENT_WINDOW_DAYS) -> Optional[RateStats]:
        base = self.jockey_stats(jockey)
        if base is None:
            return None
        edge = self.target - timedelta(days=days)
        rows = [r for r in self._by_jockey.get(self.jockey_key(jockey), []) if r.day >= edge]
        if len(rows) < MIN_RECENT_STARTS:
            return None
        return _rate_stats(rows, base.adj_win, base.adj_top3, prior_strength=RECENT_PRIOR_STRENGTH)

    # ---- 気配シグナル ----
    def signals(self, jockey: str, trainer: str, prev_jockey: str = "",
                horse: str = "") -> Dict[str, object]:
        """乗り替わり・騎手×厩舎の相性・騎手の調子を 50中心の成分へ。"""
        current = self.jockey_stats(jockey)
        current_stable = self.stable_stats(jockey, trainer)
        prev_stable = self.stable_stats(prev_jockey, trainer) if prev_jockey else None
        changed = bool(jockey and prev_jockey and self.jockey_key(jockey) != self.jockey_key(prev_jockey))

        upgrade_delta = None
        if changed and current_stable and prev_stable:
            upgrade_delta = round(current_stable.adj_win - prev_stable.adj_win, 4)

        if not changed:
            change_label = "騎手継続" if prev_jockey else "前走騎手不明"
            change_component = 52.0 if prev_jockey else 50.0
        elif upgrade_delta is None:
            change_label = "乗り替わり（比較不能）"
            change_component = 50.0
        else:
            if upgrade_delta >= 0.03:
                change_label = "乗り替わり強化"
            elif upgrade_delta <= -0.03:
                change_label = "乗り替わり弱化"
            else:
                change_label = "乗り替わり同格"
            change_component = 50.0 + _clamp(400.0 * upgrade_delta, -25.0, 25.0)

        # 騎手×厩舎: 厩舎平均との差＋主戦度
        trainer_rows = self._by_trainer.get(self.trainer_key(trainer), [])
        combo_rows = self._by_combo.get((self.jockey_key(jockey), self.trainer_key(trainer)), [])
        combo_delta = combo_share = None
        combo_component = 50.0
        if trainer_rows and combo_rows:
            t_stat = _rate_stats(trainer_rows, self.prior_win, self.prior_top3)
            c_stat = _rate_stats(combo_rows, self.prior_win, self.prior_top3)
            combo_delta = round(c_stat.adj_win - t_stat.adj_win, 4)
            combo_share = round(len(combo_rows) / len(trainer_rows), 3)
            if len(combo_rows) >= 3:
                combo_component = 50.0 + _clamp(300.0 * combo_delta, -20.0, 20.0)
            combo_component += _clamp(20.0 * (combo_share - 0.25), -5.0, 8.0)
        if not combo_rows:
            combo_label = "組み合わせ実績なし"
        elif combo_share is not None and combo_share >= 0.35:
            combo_label = f"主戦級（厩舎騎乗の{combo_share:.0%}）"
        else:
            combo_label = f"組み合わせ{len(combo_rows)}騎乗"

        # 騎手の調子（直近30日 − 当季）
        recent = self.recent_stats(jockey)
        trend = None
        trend_component = 50.0
        if recent and current:
            trend = round(recent.adj_win - current.adj_win, 4)
            trend_component = 50.0 + _clamp(300.0 * trend, -20.0, 20.0)
        trend_label = (
            "直近成績不足" if trend is None else
            "騎手が上昇中" if trend >= 0.03 else
            "騎手が下降中" if trend <= -0.03 else "騎手は平常"
        )

        horse_stat = self.horse_jockey_stats(jockey, horse) if horse else None

        return {
            "resolved_jockey": self.jockey_key(jockey),
            "resolved_trainer": self.trainer_key(trainer),
            "changed": changed,
            "upgrade_delta": upgrade_delta,
            "change_label": change_label,
            "combo_starts": len(combo_rows),
            "combo_delta": combo_delta,
            "combo_share": combo_share,
            "combo_label": combo_label,
            "trend": trend,
            "trend_label": trend_label,
            "jockey_stats": current.as_dict() if current else None,
            "stable_stats": current_stable.as_dict() if current_stable else None,
            "prev_stable_stats": prev_stable.as_dict() if prev_stable else None,
            "horse_jockey_stats": horse_stat.as_dict() if horse_stat else None,
            "components": {
                "jockey_change": round(_clamp(change_component, 20.0, 90.0), 1),
                "jockey_trainer_combo": round(_clamp(combo_component, 20.0, 90.0), 1),
                "jockey_trend": round(_clamp(trend_component, 20.0, 90.0), 1),
            },
            "summary": " / ".join([change_label, combo_label, trend_label]),
        }
