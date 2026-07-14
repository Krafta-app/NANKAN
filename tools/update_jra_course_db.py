#!/usr/bin/env python3
"""course-db.comのJRA全コース平均時計をローカル参照JSONへ更新する。"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


HOME_URL = "https://course-db.com/"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "jra_course_db_times.json"
JRA_PLACES = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
COURSE_TITLE_RE = re.compile(
    rf"^({'|'.join(JRA_PLACES)})(芝|ダート)(\d{{3,4}})m(?:(内|外)|（(内|外)回り）)?の最新傾向$"
)
CLASS_LABELS = ("重賞・OP", "3勝", "2勝", "1勝", "未勝利", "新馬")


def _fetch(url):
    request = Request(url, headers={"User-Agent": "NankanAI-course-reference/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _clock_to_seconds(value):
    text = str(value or "").strip()
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}(?:\.\d+)?)", text)
    if not match:
        return None
    return int(match.group(1) or 0) * 60 + float(match.group(2))


def _course_urls(home_html):
    soup = BeautifulSoup(home_html, "html.parser")
    urls = []
    seen = set()
    for anchor in soup.select("a[href]"):
        label = re.sub(r"\s+", "", anchor.get_text(" ", strip=True))
        if not re.fullmatch(r"(?:芝|ダート)\d{3,4}m(?:内|外)?", label):
            continue
        url = urljoin(HOME_URL, anchor.get("href"))
        canonical = unquote(url).lower()
        if canonical in seen:
            continue
        seen.add(canonical)
        urls.append(url)
    return urls


def _parse_course(url, page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    title = soup.find("h1")
    title_text = re.sub(r"\s+", "", title.get_text(" ", strip=True) if title else "")
    match = COURSE_TITLE_RE.match(title_text)
    if not match:
        raise ValueError(f"コース名を解析できません: {title_text or url}")
    place, surface_raw, dist, short_variant, long_variant = match.groups()
    variant = short_variant or long_variant or ""

    heading = next(
        (h for h in soup.find_all("h2") if "平均タイム" in h.get_text(" ", strip=True)),
        None,
    )
    table = heading.find_next("table") if heading else None
    if table is None:
        raise ValueError(f"平均タイム表がありません: {url}")

    period = ""
    for text in heading.find_all_next(string=re.compile(r"期間："), limit=2):
        period_match = re.search(r"期間：\s*([^\s]+)", str(text))
        if period_match:
            period = period_match.group(1)
            break

    updated = ""
    page_text = soup.get_text(" ", strip=True)
    updated_match = re.search(r"更新日：\s*(\d{4}/\d{2}/\d{2})", page_text)
    if updated_match:
        updated = updated_match.group(1)

    classes = {}
    for row in table.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th, td")]
        if len(cells) < 3 or cells[0] not in CLASS_LABELS:
            continue
        try:
            races = int(cells[1].replace(",", ""))
        except ValueError:
            continue
        seconds = _clock_to_seconds(cells[2])
        if races <= 0 or seconds is None:
            continue
        classes[cells[0]] = {
            "races": races,
            "time": cells[2],
            "seconds": seconds,
            "front_3f": cells[3] if len(cells) >= 4 else "",
            "last_3f": cells[4] if len(cells) >= 5 else "",
        }
    if not classes:
        raise ValueError(f"クラス別時計を解析できません: {url}")

    total_races = sum(item["races"] for item in classes.values())
    weighted_average = sum(item["races"] * item["seconds"] for item in classes.values()) / total_races
    return {
        "place": place,
        "surface": "芝" if surface_raw == "芝" else "ダ",
        "distance": int(dist),
        "variant": variant,
        "period": period,
        "updated": updated,
        "url": url,
        "total_races": total_races,
        "weighted_average_time": round(weighted_average, 3),
        "classes": classes,
    }


def update(output, delay=0.12):
    urls = _course_urls(_fetch(HOME_URL))
    if len(urls) != 102:
        raise RuntimeError(f"全102コースのはずが{len(urls)}件でした。サイト構造変更の可能性があります。")
    courses = []
    for index, url in enumerate(urls, 1):
        courses.append(_parse_course(url, _fetch(url)))
        if index < len(urls) and delay:
            time.sleep(delay)
    courses.sort(key=lambda item: (
        JRA_PLACES.index(item["place"]), item["surface"], item["distance"], item["variant"]
    ))
    payload = {
        "schema_version": 1,
        "source": HOME_URL,
        "source_note": "過去5年間のクラス別1着馬平均タイム",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "course_count": len(courses),
        "courses": courses,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(output)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--delay", type=float, default=0.12)
    args = parser.parse_args()
    payload = update(args.output, max(0.0, args.delay))
    print(f"saved {payload['course_count']} courses: {args.output}")


if __name__ == "__main__":
    main()
