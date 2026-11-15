"""keiba_bot から NAR月次CSVの実績を使うための橋渡し。

keiba_bot.py 側の変更を最小にするため、既存の関数と同じ形（引数・戻り値）で
CSVからの答えを返す。CSVに無ければ None を返すので、呼び出し側は
これまで通りスクレイピングへ落ちる。

CSVが効くところ:
  * 過去レースの全出走馬（相対評価の物差し馬）を通信なしで完全に取れる
  * 過去走が5走のスクレイピング上限を超えて遡れる
  * 騎手評価を2025年の静的表ではなく直近365日の実績から作れる
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

import nar_history as nh

CSV_SCHEME = "nar://"

# 騎手評価(0〜15)の目盛り。2026-04〜09の南関で、現行の2025騎手パワーと
# だいたい同じ分布になるよう合わせた（中央3点・上位1割12点）。
JOCKEY_POINT_FLOOR = 0.02
JOCKEY_POINT_SLOPE = 65.0
JOCKEY_POINT_MAX = 15

_RACE_ID_RE = re.compile(r"/(?:result|syousai)/(\d{16})\.do")
_CSV_KEY_RE = re.compile(r"^nar://([^/]+)/(\d{8})/(\d{1,2})$")


def enabled() -> bool:
    """CSV利用の有無。NANKAN_CSV=0 で丸ごと止められる。"""
    return os.environ.get("NANKAN_CSV", "1").strip() not in ("0", "false", "False", "")


def history() -> Optional[nh.NarHistory]:
    if not enabled():
        return None
    try:
        hist = nh.get_history()
    except Exception as exc:  # 読めなければ従来動作へ
        print(f"  [NAR CSV] 読み込み失敗のためスクレイピングのみで動作します: {exc}")
        return None
    return hist or None


# ==========================================================
# レースキー
# ==========================================================
def csv_key(place: str, day: date, rno: int) -> str:
    return f"{CSV_SCHEME}{place}/{day:%Y%m%d}/{int(rno)}"


def is_csv_key(url: str) -> bool:
    return str(url or "").startswith(CSV_SCHEME)


def safe_href(url: str) -> str:
    """画面のリンク用。CSV由来の内部キーはリンクにしない。"""
    return "" if is_csv_key(url) else str(url or "")


def parse_key(url: str) -> Optional[Tuple[str, date, int]]:
    """URL（南関16桁ID）またはCSVキーから (競馬場, 日付, R) を取り出す。"""
    text = str(url or "")
    m = _CSV_KEY_RE.match(text)
    if m:
        day = nh.parse_csv_date(m.group(2))
        return (m.group(1), day, int(m.group(3))) if day else None
    m = _RACE_ID_RE.search(text)
    if not m:
        return None
    rid = m.group(1)
    place = nh.NK_CODE_TO_PLACE.get(rid[8:10])
    day = nh.parse_csv_date(rid[0:8])
    if not place or not day:
        return None
    try:
        rno = int(rid[14:16])
    except ValueError:
        return None
    return (place, day, rno)


# ==========================================================
# 過去レースの全出走馬（fetch_race_all_horses の代替）
# ==========================================================
def race_all_horses(url: str):
    """既存 fetch_race_all_horses と同じ6要素を返す。CSVに無ければ None。

    タイムは「勝ち時計との差（秒）」。既存実装と同じ約束。
    """
    hist = history()
    if hist is None:
        return None
    key = parse_key(url)
    if not key:
        return None
    place, day, rno = key
    entries = hist.race_entries(place, day, rno)
    if not entries:
        return None
    finishers = [e for e in entries if e.rank is not None and e.time_sec]
    if len(finishers) < 2:
        return None
    winner = min(finishers, key=lambda e: (e.rank if e.rank else 99, e.time_sec))
    base = winner.time_sec or min(e.time_sec for e in finishers)

    horses, ranks, pops, gates = {}, {}, {}, {}
    for e in finishers:
        horses[e.horse] = round(e.time_sec - base, 1)
        ranks[e.horse] = e.rank
        if e.pop:
            pops[e.horse] = e.pop
        gates[e.horse] = e.umaban
    race = hist.race(place, day, rno)
    dist = str(race.dist) if race and race.dist else ""
    return horses, place, dist, ranks, pops, gates


# ==========================================================
# 過去走（hist）の追加
# ==========================================================
def _hist_row(run: nh.RunRow) -> Dict[str, object]:
    rank_part = f"{run.rank}着" if run.rank else "着不明"
    pop_part = f"({run.pop}人)" if run.pop else ""
    agari_part = f"({run.agari})" if run.agari else ""
    date_str = f"{run.day:%Y.%m.%d}"
    text = (f"{date_str} {run.place}{run.dist} {run.jockey} "
            f"{run.pas}{agari_part}→{rank_part}{pop_part}")
    return {
        "text": text,
        "url": csv_key(run.place, run.day, run.rno),
        "race_name": run.race_name,
        "place": run.place,
        "source_place": run.place,
        "surface": "ダ",
        "course_variant": "",
        "dist": str(run.dist or ""),
        "jockey": run.jockey,
        "pas": run.pas,
        "rank": run.rank,
        "pop": run.pop,
        "frame": run.waku or None,
        "field_size": run.field_size or None,
        "gate_no": run.umaban,
        "weight": run.carried or 0.0,
        "time": run.time_sec or 0.0,
        "date": date_str,
        "from_csv": True,
    }


def extra_hist_rows(
    horse: str,
    target_day: date,
    target_rno: Optional[int] = None,
    target_place: str = "",
    known_urls: Optional[Sequence[str]] = None,
    oldest_known: Optional[datetime] = None,
    limit: int = 12,
) -> List[Dict[str, object]]:
    """スクレイピング済みの過去走に足せる、CSV由来の古い走りを返す。

    スクレイピングは直近5走までなので、それより前だけを足す（重複させない）。
    """
    hist = history()
    if hist is None:
        return []
    known = set()
    for url in known_urls or []:
        key = parse_key(url)
        if key:
            known.add(key)
    rows: List[Dict[str, object]] = []
    for run in hist.horse_runs(horse, before=target_day, before_rno=target_rno,
                              before_place=target_place):
        if (run.place, run.day, run.rno) in known:
            continue
        if oldest_known is not None and run.day >= oldest_known.date():
            continue
        if run.rank is None or not run.time_sec or not run.pas:
            continue
        rows.append(_hist_row(run))
        if len(rows) >= limit:
            break
    return rows


# ==========================================================
# 騎手評価
# ==========================================================
_MODEL_CACHE: Dict[Tuple, nh.ConnectionModel] = {}


def connection_model(target_day: date, place: str = "", rno: Optional[int] = None,
                     window_days: int = 365) -> Optional[nh.ConnectionModel]:
    hist = history()
    if hist is None:
        return None
    key = (target_day, place, rno, window_days, id(hist))
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = nh.ConnectionModel(hist, target_day, rno=rno, place=place,
                                   window_days=window_days)
        if len(model.rows) < 300:
            return None
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = model
    return model


def jockey_core(model: nh.ConnectionModel, jockey: str, trainer: str, place: str) -> Optional[float]:
    """騎手×競馬場 と 騎手×厩舎 の（母数補正つき）勝率を半々で混ぜた値。

    2026-04〜09の南関16,552出走で、1着AUC 0.696 / 3着内AUC 0.658。
    現行の2025騎手パワー（0.681 / 0.648）より上。
    """
    base = model.jockey_stats(jockey)
    if base is None:
        return None
    venue = model.venue_stats(jockey, place)
    stable = model.stable_stats(jockey, trainer)
    v = venue.adj_win if venue else base.adj_win
    s = stable.adj_win if stable else base.adj_win
    return 0.5 * v + 0.5 * s


def jockey_points(model: nh.ConnectionModel, jockey: str, trainer: str, place: str) -> Optional[int]:
    """騎手評価を0〜15点へ。現行の騎手パワーと同じ目盛りに合わせてある。"""
    core = jockey_core(model, jockey, trainer, place)
    if core is None:
        return None
    return int(max(0, min(JOCKEY_POINT_MAX,
                          round((core - JOCKEY_POINT_FLOOR) * JOCKEY_POINT_SLOPE))))


def jockey_detail(model: nh.ConnectionModel, jockey: str, trainer: str, place: str,
                  prev_jockey: str = "", horse: str = "") -> Dict[str, object]:
    """画面に出す騎手まわりの根拠一式。点には入れない材料も含む。"""
    base = model.jockey_stats(jockey)
    venue = model.venue_stats(jockey, place)
    stable = model.stable_stats(jockey, trainer)
    signals = model.signals(jockey, trainer, prev_jockey=prev_jockey, horse=horse)
    return {
        "jockey": base.as_dict() if base else None,
        "venue": venue.as_dict() if venue else None,
        "stable": stable.as_dict() if stable else None,
        "core": jockey_core(model, jockey, trainer, place),
        "points": jockey_points(model, jockey, trainer, place),
        "window_from": model.season_from,
        "signals": signals,
    }


def jockey_line(detail: Dict[str, object], jockey: str, trainer: str) -> str:
    """既存 display_power と同じ「【騎手】…」1行。点の根拠を数字で出す。"""
    if not detail or detail.get("points") is None:
        return ""
    pts = detail["points"]
    j = detail.get("jockey") or {}
    v = detail.get("venue") or {}
    s = detail.get("stable") or {}
    sig = detail.get("signals") or {}
    parts = [f"P:{pts}(勝{j.get('win_rate', 0) * 100:.1f}%/{j.get('top3_rate', 0) * 100:.1f}%)"]
    if v:
        parts.append(f"当場{v.get('record', '')}")
    if s and sig.get("combo_starts"):
        parts.append(f"×{trainer}{sig.get('combo_label', '')}")
    note = sig.get("change_label", "")
    if note:
        parts.append(note)
    return "【騎手】" + "、 ".join(parts)


# ==========================================================
# A案 能力材料（近走・速度・前走）
# ==========================================================
# 総合最下位が3着内に来ない配分の探索（scripts/backtest_bottom_allocation.py、
# 2026-07〜09 南関699R）で選んだ配分。相対のみだと最下位3着内13.6%、A案で7.2%。
ABILITY_WEIGHTS = {"rel": 0.30, "jockey": 0.10, "form": 0.10, "speed": 0.30, "last": 0.20}
ABILITY_POINTS = 65          # ②騎手15 + ⑤相対50 の枠をそのまま使う
FORM_CLIP_SECONDS = 3.0      # 近走: レース中央値との時計差の頭打ち
FORM_DECAY_DAYS = 90.0       # 近走: 新しさの重み（90日で1/e）
SPEED_CLIP = 6.0             # 速度: レース内で正規化する前の頭打ち（1600m換算秒）
FEATURE_RUNS = 8             # 近走・速度に使う直近走数

_PAR_CACHE: Dict[Tuple, Dict[Tuple, float]] = {}


def _race_median_time(hist: nh.NarHistory, run: nh.RunRow) -> Optional[float]:
    rows = [r.time_sec for r in hist.race_entries(run.place, run.day, run.rno) if r.time_sec]
    if len(rows) < 3:
        return None
    rows.sort()
    n = len(rows)
    return rows[n // 2] if n % 2 else (rows[n // 2 - 1] + rows[n // 2]) / 2.0


def form_value(hist: nh.NarHistory, past: Sequence[nh.RunRow], target_day: date) -> Optional[float]:
    """近走評価。レース中央値 − 自分の時計（正が速い）を±3秒で頭打ちし、新しさで加重平均。"""
    import math
    num = den = 0.0
    for run in list(past)[:FEATURE_RUNS]:
        if not run.time_sec:
            continue
        med = _race_median_time(hist, run)
        if med is None:
            continue
        diff = max(-FORM_CLIP_SECONDS, min(FORM_CLIP_SECONDS, med - run.time_sec))
        w = math.exp(-(target_day - run.day).days / FORM_DECAY_DAYS)
        num += diff * w
        den += w
    return num / den if den else None


def build_par_table(hist: nh.NarHistory, until: date) -> Dict[Tuple, float]:
    """場×距離×馬場 の基準タイム（中央値、30走以上）。until より前だけで作る。"""
    key = (id(hist), until)
    if key in _PAR_CACHE:
        return _PAR_CACHE[key]
    buckets: Dict[Tuple, List[float]] = {}
    for r in hist.runs:
        if r.day >= until or not r.time_sec or not r.dist:
            continue
        buckets.setdefault((r.place, r.dist, r.baba), []).append(r.time_sec)
        buckets.setdefault((r.place, r.dist, ""), []).append(r.time_sec)
    par = {}
    for k, v in buckets.items():
        if len(v) >= 30:
            v.sort()
            n = len(v)
            par[k] = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0
    _PAR_CACHE.clear()
    _PAR_CACHE[key] = par
    return par


def speed_value(par: Dict[Tuple, float], past: Sequence[nh.RunRow]) -> Optional[float]:
    """基礎スピード指数。基準より何秒速いか（1600m換算）。近8走、5走以上なら最も遅い1走を外して中央値。"""
    vals = []
    for run in list(past)[:FEATURE_RUNS]:
        if not run.time_sec or not run.dist:
            continue
        p = par.get((run.place, run.dist, run.baba)) or par.get((run.place, run.dist, ""))
        if p is None:
            continue
        vals.append((p - run.time_sec) * 1600.0 / run.dist)
    if not vals:
        return None
    if len(vals) >= 5:
        vals.remove(min(vals))
    vals.sort()
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def ability_features(horses_data, target_day: date, place: str, rno: Optional[int]) -> Dict[str, Dict[str, object]]:
    """出走各馬の 近走(form)・速度(speed)・前走着順(last_rank) をCSVから。無ければ None。"""
    hist = history()
    out: Dict[str, Dict[str, object]] = {}
    if hist is None:
        return out
    par = build_par_table(hist, date(target_day.year, target_day.month, 1))
    for umaban, horse in (horses_data or {}).items():
        past = hist.horse_runs(horse.get("name", ""), before=target_day, before_rno=rno, before_place=place)
        finished = [r for r in past if r.rank is not None]
        out[str(umaban)] = {
            "form": form_value(hist, finished, target_day),
            "speed": speed_value(par, finished),
            "last_rank": finished[0].rank if finished else None,
            "runs": len(finished),
        }
    return out


def normalize_in_race(values: Sequence[Optional[float]], cap: Optional[float] = None) -> List[float]:
    """レース内で0〜1へ。欠損と全馬同値は0.5（バックテストと同じ扱い）。"""
    present = [v for v in values if v is not None]
    if cap is not None:
        present = [max(-cap, min(cap, v)) for v in present]
    if not present:
        return [0.5] * len(values)
    lo, hi = min(present), max(present)
    out = []
    for v in values:
        if v is None or hi == lo:
            out.append(0.5)
        else:
            if cap is not None:
                v = max(-cap, min(cap, v))
            out.append((v - lo) / (hi - lo))
    return out
