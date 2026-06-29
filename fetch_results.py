# -*- coding: utf-8 -*-
"""
レース結果 自動取得スクリプト（スタンドアロン）

DBの台帳(races)で has_result=0 のレースについて
  https://www.nankankeiba.com/result/{race_id}.do
を取得し、着順・人気・馬番・着差を race_results へ保存する。

- race_id が未保存（旧キャッシュ由来）なら build_nankan_race_id で補完。
- 着順がまだ確定していない（0件）レースは has_result=0 のまま次回再試行。
- uma_id は予想生成時に保存した uma_ids（馬名→uma_id）から補完してメモ連携可能にする。

使い方:
  python3 fetch_results.py                 # 今日以前の未取得を全部
  python3 fetch_results.py --date 20260628 # 指定日のみ
  python3 fetch_results.py --all           # 日付制限なし（過去全部）
launchd から定期実行される（SETUP.md 参照）。
"""
import sys
import json
import time
import argparse
import datetime

import store as db
import keiba_bot as kb


def _today():
    return datetime.date.today().strftime("%Y%m%d")


def _race_id_for(race, kai_nichi_cache):
    """race(dict) の race_id を返す。無ければ build_nankan_race_id で補完しDBへ保存。"""
    rid = race.get("race_id")
    if rid:
        return rid
    date = race["date"]  # YYYYMMDD
    place_code = race["place_code"]
    # まず既存の taisen キャッシュ名からオフライン復元（過去日でも確実）
    rid = db.race_id_from_taisen_cache(date, place_code, race["race_num"])
    if rid:
        db.set_race_id(race["race_key"], rid)
        return rid
    y, m, d = date[:4], date[4:6], date[6:8]
    key = (date, place_code)
    if key not in kai_nichi_cache:
        # kai/nichi を一度だけネット取得してキャッシュ
        nk_place = kb.NK_CODE_MAP.get(str(place_code))
        kai_nichi_cache[key] = kb.get_nankan_kai_nichi(y, m, d, nk_place) if nk_place else (None, None)
    kai, nichi = kai_nichi_cache[key]
    if not kai:
        return ""
    rid = kb.build_nankan_race_id(y, m, d, place_code, race["race_num"], kai=kai, nichi=nichi)
    if rid:
        db.set_race_id(race["race_key"], rid)
    return rid


def fetch_one(race, kai_nichi_cache):
    """1レース分の結果を取得して保存。保存件数を返す（0=未確定/失敗）。"""
    race_key = race["race_key"]
    rid = _race_id_for(race, kai_nichi_cache)
    if not rid:
        print(f"  [skip] {race_key}: race_id 不明（開催特定失敗）")
        return 0

    url = f"https://www.nankankeiba.com/result/{rid}.do"
    try:
        times, _place, _dist, ranks, pops, gates = kb.fetch_race_all_horses(url)
    except Exception as e:
        print(f"  [err ] {race_key}: {e}")
        return 0

    if not ranks:
        print(f"  [wait] {race_key}: 着順未確定")
        return 0

    # 予想時に保存した 馬名→uma_id を引いてメモ連携。無ければ結果ページ由来で補完。
    uma_ids = {}
    try:
        uma_ids = json.loads(race.get("uma_ids_json") or "{}")
    except Exception:
        uma_ids = {}
    result_ids = kb.result_uma_ids(url)

    rows = []
    for name, rank in ranks.items():
        rows.append({
            "umaban": gates.get(name),
            "horse_name": name,
            "uma_id": uma_ids.get(name) or result_ids.get(name, ""),
            "finish_rank": rank,
            "popularity": pops.get(name),
            "time_diff": times.get(name),
        })
    n = db.upsert_results(race_key, rows)
    winner = next((r["horse_name"] for r in rows if r["finish_rank"] == 1), "?")
    print(f"  [ok  ] {race_key}: {n}頭 取得（1着 {winner}）")
    return n


def run_once(date=None, all_dates=False):
    """未取得レースの結果を1巡取得する。CLIからもアプリ内スレッドからも呼べる。
    戻り値: (保存できたレース数, 対象レース数)"""
    db.init_db()
    added = db.backfill_from_cache()
    if added:
        print(f"[backfill] {added} races 追加")

    if date:
        pending = [r for r in db.races_pending_result() if r["date"] == date]
    elif all_dates:
        pending = db.races_pending_result()
    else:
        pending = db.races_pending_result(on_or_before=_today())

    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] 未取得 {len(pending)} レースを処理")
    kai_nichi_cache = {}
    ok = 0
    for race in pending:
        if fetch_one(race, kai_nichi_cache):
            ok += 1
        time.sleep(0.3)
    print(f"[done] {ok}/{len(pending)} レースの結果を保存")
    return ok, len(pending)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="対象日 YYYYMMDD")
    ap.add_argument("--all", action="store_true", help="日付制限なし")
    args = ap.parse_args()
    run_once(date=args.date, all_dates=args.all)


if __name__ == "__main__":
    main()
