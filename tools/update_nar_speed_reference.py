#!/usr/bin/env python3
"""NAR公式CSVから地方競馬の基準時計と日別馬場差を生成する。

月次レース情報ZIPを直近指定年数分取得し、実行日には約2分ごとに
更新される当日ZIPを重ねる。ZIPはローカルに保存し、過去月は再利用する。
出力は予測時に通信不要で読める JSON スナップショット。

公式データ説明:
https://www.keiba.go.jp/pdf/manual/data_pdf_manual.pdf
"""

import argparse
import concurrent.futures
import csv
import io
import json
import math
import os
import re
import statistics
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


BASE_URL = "https://www.keiba.go.jp/KeibaWeb/DataDownload/RaceDataDownload"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "cache" / "nar_speed_csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "nar_speed_reference.json"
USER_AGENT = "NankanAI-NAR-speed-reference/1.0"

HALF_LIFE_DAYS = 730.0
RECORD_QUANTILE = 0.05
MIN_CLASS_REFERENCE_RACES = 12
MIN_COURSE_REFERENCE_RACES = 8
MIN_DAILY_APPLY_RACES = 3
DAILY_SHRINKAGE_K = 8.0
FLAT_DAILY_CAP_PER_1000 = 2.0
BANEI_DAILY_CAP_PER_1000 = 120.0

PLACE_ORDER = (
    "帯広", "門別", "盛岡", "水沢", "浦和", "船橋", "大井", "川崎",
    "金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀",
)
PLACE_ALIASES = {"帯広ば": "帯広"}
REQUIRED_PLACES = frozenset(PLACE_ORDER)

CourseKey = Tuple[str, str, str, int]
ClassKey = Tuple[str, str, str, int, str]
RaceKey = Tuple[str, str, int]
FinisherKey = Tuple[str, str, int, int]


@dataclass(frozen=True)
class RaceClock:
    """完走馬1頭の時計。rank=1 の行は日別馬場差にも使う。"""

    place: str
    official_place: str
    race_date: date
    race_no: int
    surface: str
    turn: str
    distance: int
    going: str
    class_bucket: str
    condition: str
    horse_no: int
    rank: int
    finish_time: float

    @property
    def race_key(self) -> RaceKey:
        return (self.place, self.race_date.strftime("%Y%m%d"), self.race_no)

    @property
    def course_key(self) -> CourseKey:
        return (self.place, self.surface, self.turn, self.distance)

    @property
    def class_key(self) -> ClassKey:
        return self.course_key + (self.class_bucket,)

    @property
    def finisher_key(self) -> FinisherKey:
        return self.race_key + (self.horse_no,)


@dataclass(frozen=True)
class ArchiveSource:
    kind: str
    period: str
    path: Path
    url: str
    downloaded: bool = False


def normalize_text(value: object) -> str:
    """全角英数字・全角空白を正規化する。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def normalize_place(value: object) -> Tuple[str, str]:
    official = normalize_text(value)
    return PLACE_ALIASES.get(official, official), official


def normalize_surface(place: str, value: object) -> str:
    if place == "帯広":
        return "ばんえい"
    text = normalize_text(value)
    if text.startswith("芝"):
        return "芝"
    if text.startswith("ダ"):
        return "ダ"
    return ""


def normalize_class_bucket(
    condition: object = "", race_name: object = "", race_type: object = ""
) -> str:
    """全角のＡ２・Ｃ３・３歳などを安定した粗いクラス帯にする。"""
    text = normalize_text(" ".join(map(str, (condition, race_name, race_type)))).upper()
    compact = re.sub(r"\s+", "", text)
    if "重賞" in compact or re.search(r"(?:^|[^A-Z])(J?G[123])(?:[^A-Z0-9]|$)", compact):
        return "重賞"
    if "オープン" in compact or re.search(r"(?:^|[^A-Z])OP(?:[^A-Z]|$)", compact):
        return "OP"
    # 空白を消すと「C2 3歳以上」が C23... に連結されるため、クラスは空白保持文字列で先に拾う。
    match = re.search(r"(?:^|[^A-Z0-9])([ABC][123])(?=$|[^0-9])", text)
    if not match:
        match = re.search(r"(?:^|[^A-Z0-9])([ABC][123])(?:[^0-9]|$)", compact)
    if match:
        return match.group(1)
    # 「3歳以上」は3歳限定ではなく古馬混合。年齢限定判定より先に除外する。
    if re.search(r"[234]歳以上", compact):
        return "その他"
    if "新馬" in compact and "2歳" in compact:
        return "2歳新馬"
    if "新馬" in compact and "3歳" in compact:
        return "3歳新馬"
    if "未勝利" in compact:
        return "未勝利"
    if "2歳" in compact:
        return "2歳"
    if "3歳" in compact:
        return "3歳"
    return "その他"


def parse_clock_to_seconds(value: object) -> Optional[float]:
    """NAR CSVの 482/1151/2043 を 48.2/75.1/124.3秒へ変換する。"""
    text = normalize_text(value)
    if not text:
        return None
    formatted = re.fullmatch(r"(?:(\d+):)?(\d{1,2})(?:\.(\d))?", text)
    if formatted and (":" in text or "." in text):
        minutes = int(formatted.group(1) or 0)
        seconds = int(formatted.group(2))
        tenths = int(formatted.group(3) or 0)
        if seconds >= 60:
            return None
        return minutes * 60.0 + seconds + tenths / 10.0
    digits = re.sub(r"\D", "", text)
    if len(digits) < 2:
        return None
    tenths = int(digits[-1])
    whole = int(digits[:-1])
    minutes, seconds = divmod(whole, 100)
    if seconds >= 60:
        return None
    parsed = minutes * 60.0 + seconds + tenths / 10.0
    return parsed if parsed > 0 else None


def parse_race_date(value: object) -> Optional[date]:
    digits = re.sub(r"\D", "", normalize_text(value))
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def parse_positive_int(value: object) -> Optional[int]:
    match = re.search(r"\d+", normalize_text(value))
    if not match:
        return None
    number = int(match.group(0))
    return number if number > 0 else None


def parse_rank(value: object) -> Optional[int]:
    """取消・中止・失格を除き、数値着順だけを返す。"""
    match = re.match(r"(\d+)", normalize_text(value))
    if not match:
        return None
    rank = int(match.group(1))
    return rank if rank > 0 else None


def parse_as_of(value: Optional[str]) -> date:
    if not value:
        return datetime.now().astimezone().date()
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise argparse.ArgumentTypeError("--as-of は YYYY-MM-DD または YYYYMMDD で指定してください")
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def rolling_window_start(as_of: date, years: int) -> date:
    if years < 1:
        raise ValueError("years must be at least 1")
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:  # 2月29日
        return as_of.replace(year=as_of.year - years, day=28)


def iter_months(start: date, end: date) -> Iterator[Tuple[int, int]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month == 13:
            year += 1
            month = 1


def monthly_url(year: int, month: int) -> str:
    query = urllib.parse.urlencode({"type": "monthly", "k_year": year, "k_month": month})
    return f"{BASE_URL}?{query}"


def daily_url() -> str:
    return f"{BASE_URL}?type=daily"


def _zip_has_race_files(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name.lower() for name in archive.namelist()]
            return (
                any(name.endswith("_racelist.csv") for name in names)
                and any(name.endswith("_horselist.csv") for name in names)
            )
    except (OSError, zipfile.BadZipFile):
        return False


def download_archive(url: str, target: Path, refresh: bool = False) -> Tuple[Path, bool]:
    """取得後にZIP構造を検査して原子的にキャッシュを入れ替える。"""
    if target.exists() and _zip_has_race_files(target) and not refresh:
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/zip,application/octet-stream,*/*"},
    )
    temp_name = None
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=target.name + ".", suffix=".tmp", dir=str(target.parent), delete=False
            ) as temp_file:
                temp_name = temp_file.name
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())
        temp_path = Path(temp_name)
        if not _zip_has_race_files(temp_path):
            raise ValueError(f"NAR公式応答が想定するレース情報ZIPではありません: {url}")
        os.replace(str(temp_path), str(target))
        temp_name = None
        return target, True
    except Exception:
        # 過去月はキャッシュで継続できるが、現月/当日のrefresh失敗を
        # 握り潰すと中間日が欠落した新snapshotを作るため、旧ファイルへ戻さない。
        if not refresh and target.exists() and _zip_has_race_files(target):
            return target, False
        raise
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def prepare_archives(
    as_of: date,
    years: int,
    cache_dir: Path,
    offline: bool = False,
    workers: int = 4,
    today: Optional[date] = None,
) -> Tuple[List[ArchiveSource], List[str]]:
    """必要な月次ZIPと当日ZIPを用意する。当日取得失敗時は直前の有効ZIPへ戻す。"""
    cache_dir = Path(cache_dir)
    today = today or datetime.now().astimezone().date()
    start = rolling_window_start(as_of, years)
    months = list(iter_months(start, as_of))
    warnings: List[str] = []

    def one_month(item: Tuple[int, int]) -> ArchiveSource:
        year, month = item
        period = f"{year:04d}{month:02d}"
        target = cache_dir / "monthly" / f"{period}_race.zip"
        url = monthly_url(year, month)
        if offline:
            if not _zip_has_race_files(target):
                raise FileNotFoundError(f"オフライン用キャッシュがありません: {target}")
            return ArchiveSource("monthly", period, target, url, False)
        refresh = (year, month) == (as_of.year, as_of.month)
        path, downloaded = download_archive(url, target, refresh=refresh)
        return ArchiveSource("monthly", period, path, url, downloaded)

    month_sources: Dict[Tuple[int, int], ArchiveSource] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        future_map = {executor.submit(one_month, item): item for item in months}
        for future in concurrent.futures.as_completed(future_map):
            item = future_map[future]
            month_sources[item] = future.result()
    sources = [month_sources[item] for item in months]

    daily_target = cache_dir / "daily" / f"{as_of:%Y%m%d}_race.zip"
    if as_of == today and not offline:
        try:
            path, downloaded = download_archive(daily_url(), daily_target, refresh=True)
            sources.append(ArchiveSource("daily", f"{as_of:%Y%m%d}", path, daily_url(), downloaded))
        except Exception as exc:
            if _zip_has_race_files(daily_target):
                sources.append(ArchiveSource(
                    "daily", f"{as_of:%Y%m%d}", daily_target, daily_url(), False
                ))
                warnings.append(f"当日CSVの再取得に失敗したため直前キャッシュで継続: {exc}")
            else:
                warnings.append(f"当日CSVを取得できず月次データで継続: {exc}")
    elif _zip_has_race_files(daily_target):
        sources.append(ArchiveSource("daily", f"{as_of:%Y%m%d}", daily_target, daily_url(), False))
    elif offline and as_of == today:
        warnings.append(f"当日CSVキャッシュなし（月次データのみ）: {daily_target}")
    return sources, warnings


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _csv_name(archive: zipfile.ZipFile, suffix: str) -> str:
    suffix = suffix.lower()
    matches = [name for name in archive.namelist() if name.lower().endswith(suffix)]
    if not matches:
        raise ValueError(f"ZIP内に {suffix} がありません")
    return sorted(matches)[0]


def parse_race_archive(
    path: Path,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Tuple[List[RaceClock], Dict[str, int]]:
    """1つのNAR公式ZIPから、勝馬が確定したレースの全完走時計を読む。"""
    path = Path(path)
    stats = Counter()
    with zipfile.ZipFile(path) as archive:
        race_name = _csv_name(archive, "_racelist.csv")
        horse_name = _csv_name(archive, "_horselist.csv")
        race_reader = csv.DictReader(io.StringIO(_decode_csv(archive.read(race_name))))
        race_meta: Dict[RaceKey, Dict[str, object]] = {}
        for raw in race_reader:
            stats["racelist_rows"] += 1
            place, official_place = normalize_place(raw.get("競馬場"))
            race_date = parse_race_date(raw.get("競走年月日"))
            race_no = parse_positive_int(raw.get("レース番号"))
            distance = parse_positive_int(raw.get("距離"))
            surface = normalize_surface(place, raw.get("芝ダート区分"))
            turn = normalize_text(raw.get("回り"))
            if not place or race_date is None or race_no is None or distance is None or not surface or not turn:
                stats["invalid_race_meta"] += 1
                continue
            if start_date and race_date < start_date:
                continue
            if end_date and race_date > end_date:
                continue
            condition = normalize_text(raw.get("条件"))
            class_bucket = normalize_class_bucket(
                condition, raw.get("レース名"), raw.get("競走種類名称")
            )
            key = (place, race_date.strftime("%Y%m%d"), race_no)
            race_meta[key] = {
                "place": place,
                "official_place": official_place,
                "race_date": race_date,
                "race_no": race_no,
                "surface": surface,
                "turn": turn,
                "distance": distance,
                "going": normalize_text(raw.get("馬場")),
                "condition": condition,
                "class_bucket": class_bucket,
            }

        parsed_finishers: List[RaceClock] = []
        horse_reader = csv.DictReader(io.StringIO(_decode_csv(archive.read(horse_name))))
        for raw in horse_reader:
            stats["horselist_rows"] += 1
            place, _official_place = normalize_place(raw.get("競馬場"))
            race_date = parse_race_date(raw.get("競走年月日"))
            race_no = parse_positive_int(raw.get("レース番号"))
            horse_no = parse_positive_int(raw.get("馬番"))
            rank = parse_rank(raw.get("着順"))
            finish_time = parse_clock_to_seconds(raw.get("タイム"))
            if not place or race_date is None or race_no is None:
                stats["invalid_finisher_key"] += 1
                continue
            key = (place, race_date.strftime("%Y%m%d"), race_no)
            meta = race_meta.get(key)
            if meta is None:
                continue
            if horse_no is None or rank is None or finish_time is None:
                stats["invalid_or_nonfinisher_clock"] += 1
                continue
            parsed_finishers.append(RaceClock(
                horse_no=horse_no, rank=rank, finish_time=finish_time, **meta
            ))

    completed_keys = {row.race_key for row in parsed_finishers if row.rank == 1}
    races = [row for row in parsed_finishers if row.race_key in completed_keys]
    stats["unfinished_or_missing_winner"] = len(race_meta) - len(completed_keys)
    stats["completed_races"] = len(completed_keys)
    stats["valid_finishers"] = len(races)
    return races, dict(stats)


def load_race_clocks(
    sources: Sequence[ArchiveSource], start_date: date, end_date: date
) -> Tuple[List[RaceClock], Dict[str, int]]:
    """月次を読んだ後に当日ZIPを重ね、同一レースは最新側の全完走馬で置き換える。"""
    by_finisher: Dict[FinisherKey, RaceClock] = {}
    total_stats = Counter()
    for source in sorted(sources, key=lambda item: (0 if item.kind == "monthly" else 1, item.period)):
        races, stats = parse_race_archive(source.path, start_date=start_date, end_date=end_date)
        total_stats.update(stats)
        if source.kind == "daily":
            replacing = {race.race_key for race in races}
            by_finisher = {
                key: value for key, value in by_finisher.items()
                if value.race_key not in replacing
            }
        for race in races:
            by_finisher[race.finisher_key] = race
    rows = list(by_finisher.values())
    total_stats["deduplicated_completed_races"] = len({row.race_key for row in rows})
    total_stats["deduplicated_valid_finishers"] = len(rows)
    return sorted(
        rows,
        key=lambda row: (row.race_date, row.place, row.race_no, row.rank, row.horse_no),
    ), dict(total_stats)


def time_decay_weight(race_date: date, as_of: date, half_life_days: float = HALF_LIFE_DAYS) -> float:
    age_days = max(0, (as_of - race_date).days)
    return math.exp(-math.log(2.0) * age_days / max(1.0, float(half_life_days)))


def weighted_quantile(values: Sequence[float], weights: Sequence[float], quantile: float) -> float:
    if not values or len(values) != len(weights):
        raise ValueError("values and weights must be non-empty and have equal length")
    ordered = sorted((float(value), max(0.0, float(weight))) for value, weight in zip(values, weights))
    total = sum(weight for _value, weight in ordered)
    if total <= 0:
        return statistics.median(value for value, _weight in ordered)
    target = min(1.0, max(0.0, float(quantile))) * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _confidence(races: int) -> str:
    if races >= 100:
        return "高"
    if races >= 30:
        return "中"
    return "暫定"


def minimum_record_gap(rows: Sequence[RaceClock]) -> float:
    """70点と100点の分母が少数標本で潰れない最小時計差。"""
    if not rows:
        raise ValueError("rows must not be empty")
    first = rows[0]
    if first.surface == "ばんえい":
        return 8.0
    return max(0.8, float(first.distance) / 1000.0)


def summarize_times(rows: Sequence[RaceClock], as_of: date) -> Dict[str, object]:
    """全完走馬の加重平均を70点、頑健な上位5%時計を100点の基準にする。"""
    if not rows:
        raise ValueError("rows must not be empty")
    values = [float(row.finish_time) for row in rows]
    weights = [time_decay_weight(row.race_date, as_of) for row in rows]
    total_weight = sum(weights)
    average = sum(value * weight for value, weight in zip(values, weights)) / total_weight
    quantile_record = weighted_quantile(values, weights, RECORD_QUANTILE)
    gap_floor = minimum_record_gap(rows)
    record = min(quantile_record, average - gap_floor)
    race_count = len({row.race_key for row in rows})
    effective_samples = total_weight * total_weight / sum(weight * weight for weight in weights)
    return {
        "average_time": round(average, 3),
        "record_time": round(record, 3),
        "fastest_time": round(min(values), 3),
        "median_time": round(weighted_quantile(values, weights, 0.5), 3),
        "samples": len(rows),
        "races": race_count,
        "effective_samples": round(effective_samples, 2),
        "record_gap_floor": round(gap_floor, 3),
        "confidence": _confidence(race_count),
    }


def build_course_entries(races: Sequence[RaceClock], as_of: date, years: int) -> List[Dict[str, object]]:
    groups: Dict[CourseKey, List[RaceClock]] = defaultdict(list)
    for race in races:
        groups[race.course_key].append(race)
    entries: List[Dict[str, object]] = []
    for key, group in groups.items():
        place, surface, turn, distance = key
        summary = summarize_times(group, as_of)
        class_groups: Dict[str, List[RaceClock]] = defaultdict(list)
        for race in group:
            class_groups[race.class_bucket].append(race)
        class_times = {
            label: summarize_times(rows, as_of)
            for label, rows in sorted(class_groups.items())
        }
        race_going = {race.race_key: race.going or "不明" for race in group}
        going_counts = Counter(race_going.values())
        latest_date = max(race.race_date for race in group)
        official_places = sorted({race.official_place for race in group})
        entry = {
            "place": place,
            "surface": surface,
            "turn": turn,
            "distance": distance,
            **summary,
            "source": f"NAR公式CSV 直近{years}年 半減期{int(HALF_LIFE_DAYS)}日",
            "reference_provider": "NAR公式CSV",
            "reference_updated": latest_date.isoformat(),
            "class_times": class_times,
            "going_counts": dict(sorted(going_counts.items())),
        }
        if official_places != [place]:
            entry["official_places"] = official_places
        entries.append(entry)

    place_rank = {place: index for index, place in enumerate(PLACE_ORDER)}
    surface_rank = {"ばんえい": 0, "ダ": 1, "芝": 2}
    return sorted(
        entries,
        key=lambda item: (
            place_rank.get(str(item["place"]), len(place_rank)),
            str(item["place"]),
            surface_rank.get(str(item["surface"]), 9),
            str(item["turn"]),
            int(item["distance"]),
        ),
    )


def _weighted_mean_excluding_date(
    rows: Sequence[RaceClock], excluded_date: date, as_of: date
) -> Optional[Tuple[float, int]]:
    kept = [row for row in rows if row.race_date != excluded_date]
    if not kept:
        return None
    weights = [time_decay_weight(row.race_date, as_of) for row in kept]
    total_weight = sum(weights)
    if total_weight <= 0:
        return None
    average = sum(row.finish_time * weight for row, weight in zip(kept, weights)) / total_weight
    return average, len({row.race_key for row in kept})


def _build_weighted_group_stats(groups, as_of: date):
    """leave-date-out平均をO(1)で引けるよう、全体と日別の加重合計を先に作る。"""
    result = {}
    for key, rows in groups.items():
        total_weight = 0.0
        total_weighted_time = 0.0
        total_races = 0
        by_date = defaultdict(lambda: [0.0, 0.0, 0])
        for row in rows:
            weight = time_decay_weight(row.race_date, as_of)
            weighted_time = row.finish_time * weight
            total_weight += weight
            total_weighted_time += weighted_time
            total_races += 1
            day = by_date[row.race_date]
            day[0] += weight
            day[1] += weighted_time
            day[2] += 1
        result[key] = (total_weight, total_weighted_time, total_races, dict(by_date))
    return result


def _weighted_mean_from_group_stats(stats, excluded_date: date) -> Optional[Tuple[float, int]]:
    if not stats:
        return None
    total_weight, total_weighted_time, total_races, by_date = stats
    day_weight, day_weighted_time, day_races = by_date.get(excluded_date, (0.0, 0.0, 0))
    kept_weight = total_weight - day_weight
    kept_races = total_races - day_races
    if kept_weight <= 0 or kept_races <= 0:
        return None
    return (total_weighted_time - day_weighted_time) / kept_weight, kept_races


def robust_location(values: Sequence[float]) -> Tuple[float, List[float], float]:
    """MADで3.5倍を超える外れを除き、中央値を返す。"""
    vals = [float(value) for value in values if math.isfinite(float(value))]
    if not vals:
        raise ValueError("values must not be empty")
    center = statistics.median(vals)
    deviations = [abs(value - center) for value in vals]
    mad = statistics.median(deviations)
    if mad <= 1e-12:
        return center, vals, mad
    robust_sigma = 1.4826 * mad
    kept = [value for value in vals if abs(value - center) <= 3.5 * robust_sigma]
    if not kept:
        kept = vals
    return statistics.median(kept), kept, mad


def build_daily_variants(races: Sequence[RaceClock], as_of: date) -> List[Dict[str, object]]:
    # コース平均は全完走馬だが、日別馬場差は1レース1件の勝時計だけで推定する。
    # 同着1着が複数いる場合も同一レースを重複カウントしない。
    winners_by_race: Dict[RaceKey, RaceClock] = {}
    for row in races:
        if row.rank != 1:
            continue
        previous = winners_by_race.get(row.race_key)
        if previous is None or row.finish_time < previous.finish_time:
            winners_by_race[row.race_key] = row
    winners = list(winners_by_race.values())

    course_groups: Dict[CourseKey, List[RaceClock]] = defaultdict(list)
    class_groups: Dict[ClassKey, List[RaceClock]] = defaultdict(list)
    for race in winners:
        course_groups[race.course_key].append(race)
        class_groups[race.class_key].append(race)

    residuals: Dict[Tuple[date, str, str], List[Tuple[float, RaceClock, str]]] = defaultdict(list)
    course_reference_stats = _build_weighted_group_stats(course_groups, as_of)
    class_reference_stats = _build_weighted_group_stats(class_groups, as_of)

    for race in winners:
        reference = _weighted_mean_from_group_stats(
            class_reference_stats.get(race.class_key), race.race_date
        )
        source = "class"
        if reference is None or reference[1] < MIN_CLASS_REFERENCE_RACES:
            reference = _weighted_mean_from_group_stats(
                course_reference_stats.get(race.course_key), race.race_date
            )
            source = "course"
        if reference is None or reference[1] < MIN_COURSE_REFERENCE_RACES:
            continue
        expected, _samples = reference
        per_1000 = (race.finish_time - expected) * 1000.0 / race.distance
        residuals[(race.race_date, race.place, race.surface)].append((per_1000, race, source))

    entries: List[Dict[str, object]] = []
    for (race_date, place, surface), rows in residuals.items():
        raw, kept_values, mad = robust_location([item[0] for item in rows])
        kept_pool = list(rows)
        if len(kept_values) < len(rows) and mad > 0:
            center = statistics.median(item[0] for item in rows)
            robust_sigma = 1.4826 * mad
            kept_pool = [item for item in rows if abs(item[0] - center) <= 3.5 * robust_sigma]
        race_count = len(kept_pool)
        distance_values = sorted({item[1].distance for item in kept_pool})
        turns = sorted({item[1].turn for item in kept_pool})
        if race_count < MIN_DAILY_APPLY_RACES:
            applied = 0.0
        else:
            cap = BANEI_DAILY_CAP_PER_1000 if surface == "ばんえい" else FLAT_DAILY_CAP_PER_1000
            reliability = race_count / (race_count + DAILY_SHRINKAGE_K)
            diversity = 1.0 if surface == "ばんえい" or len(distance_values) >= 2 else 0.75
            applied = max(-cap, min(cap, raw)) * reliability * diversity
        if race_count >= 8:
            confidence = "高"
        elif race_count >= 4:
            confidence = "中"
        else:
            confidence = "暫定"
        entries.append({
            "date": race_date.strftime("%Y.%m.%d"),
            "place": place,
            "surface": surface,
            "raw_per_1000": round(raw, 4),
            "applied_per_1000": round(applied, 4),
            "races": race_count,
            "distances": len(distance_values),
            "distance_values": distance_values,
            "turns": turns,
            "confidence": confidence,
            "source": "NAR公式CSV 同日・同場・同芝ダ別勝時計",
            "mad_per_1000": round(mad, 4),
            "discarded_outliers": len(rows) - race_count,
            "class_reference_races": sum(item[2] == "class" for item in kept_pool),
            "course_reference_races": sum(item[2] == "course" for item in kept_pool),
        })
    return sorted(entries, key=lambda item: (str(item["date"]), str(item["place"]), str(item["surface"])))


def build_reference_payload(
    races: Sequence[RaceClock],
    as_of: date,
    years: int,
    source_count: Optional[Dict[str, int]] = None,
    parse_stats: Optional[Dict[str, int]] = None,
) -> Dict[str, object]:
    start_date = rolling_window_start(as_of, years)
    courses = build_course_entries(races, as_of, years)
    daily_variants = build_daily_variants(races, as_of)
    race_count = len({race.race_key for race in races})
    places = sorted({race.place for race in races}, key=lambda p: (PLACE_ORDER.index(p) if p in PLACE_ORDER else 99, p))
    return {
        "schema_version": 1,
        "source": BASE_URL,
        "source_note": (
            "NAR公式レース情報CSVの全完走時計でコース基準を作成し、"
            "確定1着時計で開催日馬場差を作成"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "window": {
            "start": start_date.isoformat(),
            "end": as_of.isoformat(),
            "years": years,
            "half_life_days": int(HALF_LIFE_DAYS),
            "record_quantile": RECORD_QUANTILE,
        },
        "race_count": race_count,
        "finisher_count": len(races),
        "course_count": len(courses),
        "daily_variant_count": len(daily_variants),
        "venue_count": len(places),
        "reference_updated": max((race.race_date for race in races), default=as_of).isoformat(),
        "places": places,
        "source_archives": source_count or {},
        "parse_stats": parse_stats or {},
        "courses": courses,
        "daily_variants": daily_variants,
    }


def write_json_atomic(payload: Dict[str, object], output: Path) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=output.name + ".", suffix=".tmp",
            dir=str(output.parent), delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, output)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def update_reference(
    as_of: date,
    years: int,
    cache_dir: Path,
    output: Path,
    offline: bool = False,
    workers: int = 4,
) -> Dict[str, object]:
    start_date = rolling_window_start(as_of, years)
    sources, warnings = prepare_archives(
        as_of=as_of, years=years, cache_dir=cache_dir, offline=offline, workers=workers
    )
    races, parse_stats = load_race_clocks(sources, start_date=start_date, end_date=as_of)
    if not races:
        raise RuntimeError("確定勝時計を1件も読み込めませんでした")
    source_count = dict(Counter(source.kind for source in sources))
    payload = build_reference_payload(
        races, as_of=as_of, years=years, source_count=source_count, parse_stats=parse_stats
    )
    missing_places = REQUIRED_PLACES.difference(payload.get("places") or [])
    if missing_places:
        missing_label = "、".join(place for place in PLACE_ORDER if place in missing_places)
        raise RuntimeError(
            f"地方15場の基準が揃わないため更新を中止しました（不足: {missing_label}）"
        )
    if warnings:
        payload["warnings"] = warnings
    payload["downloaded_archives"] = sum(source.downloaded for source in sources)
    payload["cached_archives"] = sum(not source.downloaded for source in sources)
    write_json_atomic(payload, output)
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=parse_as_of, default=parse_as_of(None), help="基準日 YYYY-MM-DD")
    parser.add_argument("--years", type=int, default=5, help="集計年数（既定: 5）")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offline", action="store_true", help="通信せず保存済みZIPだけで再生成")
    parser.add_argument("--workers", type=int, default=4, help="月次ZIP並列取得数")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.years < 1:
        parser.error("--years は1以上で指定してください")
    if args.workers < 1:
        parser.error("--workers は1以上で指定してください")
    payload = update_reference(
        as_of=args.as_of,
        years=args.years,
        cache_dir=args.cache_dir,
        output=args.output,
        offline=args.offline,
        workers=args.workers,
    )
    print(
        f"saved {payload['course_count']} courses / {payload['race_count']} races / "
        f"{payload['daily_variant_count']} daily variants: {args.output}"
    )
    for warning in payload.get("warnings", []):
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
