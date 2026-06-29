#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相対評価(tier)バックテスト土台 — 順位精度のA/B検証用(回収率=払戻は対象外)。

考え方: 出馬表(syousai)の近走は「レース直前まで」＝本質的に as-of(リーク無し)。
これを実結果(result)とセットで capture して保存すれば、以後オフラインで何度でも
calculate_relative_scores を再採点でき、_REL_NEW_RULE フラグで新旧bridgeロジックを A/B できる。

  python backtest_relative.py capture --date 20260616 --idplace 21 --kai 03 --nichi 02 --races 1-12
  python backtest_relative.py eval
"""
import os, sys, json, argparse, math, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keiba_bot as kb

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_data")
BASE = "https://www.nankankeiba.com"

# 結果ページ会場2桁コード → 場名(parse_nankankeiba_detail / 相対評価が使う日本語名)
IDCODE_TO_PLACE = {"20": "大井", "21": "川崎", "19": "船橋", "18": "浦和"}


def _race_id(date, idplace, kai, nichi, r):
    return f"{date}{idplace}{int(kai):02d}{int(nichi):02d}{int(r):02d}"


def _parse_races(spec):
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def capture(args):
    os.makedirs(DATA_DIR, exist_ok=True)
    resources = kb.load_resources()
    place_name = IDCODE_TO_PLACE.get(args.idplace, "")
    for r in _parse_races(args.races):
        rid = _race_id(args.date, args.idplace, args.kai, args.nichi, r)
        out_path = os.path.join(DATA_DIR, f"{rid}.json")
        if os.path.exists(out_path) and not args.force:
            print(f"[skip] {rid} (既存)"); continue
        try:
            # --- 実結果(着順・人気・馬番・場・距離) ---
            result_url = f"{BASE}/result/{rid}.do"
            horses_t, res_place, res_dist, ranks, pops, gates = kb.fetch_race_all_horses(result_url)
            if not ranks:
                print(f"[ng] {rid} 結果取得失敗"); continue
            place = res_place or place_name
            dist = res_dist or ""

            # --- as-of 出馬表(近走付き) ---
            sess = kb.get_http_session()
            nk_data = {"horses": {}}
            for cand in (f"{BASE}/syousai/{rid}.do",
                         f"{BASE}/syousai/{rid}.do?type=uma_shosai",
                         f"{BASE}/uma_shosai/{rid}.do"):
                try:
                    rr = sess.get(cand, timeout=15); rr.encoding = "cp932"
                    if rr.status_code >= 400:
                        continue
                    nk_data = kb.parse_nankankeiba_detail(rr.text, place, resources)
                    if nk_data.get("horses"):
                        break
                except Exception as e:
                    print(f"   syousai err {cand}: {e}")
            if not nk_data.get("horses"):
                print(f"[ng] {rid} 出馬表取得失敗"); continue

            umaban_to_name = {str(u): h.get("name", "") for u, h in nk_data["horses"].items()}

            # --- 相対評価を一度走らせて過去レース結果キャッシュを温める ---
            kb.calculate_relative_scores(nk_data["horses"], place, dist, r)

            # 結果(着順/人気/馬番=name基準 → gate基準に正規化)
            by_gate = {}
            for name, rk in ranks.items():
                g = gates.get(name)
                if g is None:
                    continue
                by_gate[str(g)] = {"name": name, "rank": rk, "pop": pops.get(name)}

            rec = {
                "race_id": rid, "place": place, "dist": dist, "r": r,
                "horses": nk_data["horses"],
                "umaban_to_name": umaban_to_name,
                "result_by_gate": by_gate,
                "result_cache": {u: list(v) for u, v in kb._RACE_RESULT_CACHE.items()},
            }
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, default=str)
            print(f"[ok] {rid} {place}{dist} 馬{len(umaban_to_name)} 結果{len(by_gate)}")
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[err] {rid}: {e}")


# ---------- eval ----------
def _tier_score(tier):
    """tierラベル→順位相関用スコア(高位ほど大)。多層ラベル(D,E…)とNone(=0,最下)に対応。"""
    if not tier:
        return 0
    idx = kb._TIER_LABELS.find(tier)
    return (len(kb._TIER_LABELS) - idx) if idx >= 0 else 0


def _spearman(xs, ys):
    """タイ平均順位を使う簡易スピアマン。"""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        rk = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk
    if len(xs) < 3:
        return float("nan")
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def _load_records():
    recs = []
    if not os.path.isdir(DATA_DIR):
        return recs
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                recs.append(json.load(f))
    return recs


def _restore_cache(rec):
    kb._RACE_RESULT_CACHE.clear()
    for u, v in rec.get("result_cache", {}).items():
        # JSONはタプルをリスト化するので6要素リストに復元(fetch_race_all_horsesと同形)
        kb._RACE_RESULT_CACHE[u] = tuple(v) if isinstance(v, list) else v


def _safe_int(x, d=99):
    try:
        return int(x)
    except (TypeError, ValueError):
        return d


def evaluate(recs, use_new_rule, multi_tier, equal_promote, lead_min=0.05, loose=False, s_floor=True, c_demote=False):
    kb._REL_NEW_RULE = use_new_rule
    kb._REL_MULTI_TIER = multi_tier
    kb._REL_EQUAL_LEAD_PROMOTE = equal_promote
    kb._REL_EQUAL_LEAD_MIN = lead_min
    kb._REL_EQUAL_LEAD_LOOSE = loose
    kb._REL_S_FLOOR = s_floor
    kb._REL_C_DEMOTE = c_demote
    pool = []          # (tier_score, finish_rank) 全馬プール
    tier_stats = {}    # tier -> [n, place(<=3)数, rank合計]
    top_place = top_n = 0
    fav_place = fav_n = 0
    isolated = 0; total_horses = 0
    # 穴馬視点: 4番人気以下(人気薄)で高tier(S/A)が実際に好走(複勝圏)したか。
    ana_base_n = ana_base_pl = 0       # 人気薄ぜんぶ(ベース複勝率)
    ana_hit_n = ana_hit_pl = 0         # 人気薄 かつ S/A
    ana_top_n = ana_top_pl = 0         # 人気薄 かつ そのレース最上位tier群

    for rec in recs:
        _restore_cache(rec)
        horses = {str(u): h for u, h in rec["horses"].items()}
        scores, tiers, *_ = kb.calculate_relative_scores(horses, rec["place"], rec["dist"], rec["r"])
        by_gate = rec["result_by_gate"]

        race_top_score = max((_tier_score(tiers.get(str(u))) for u in horses), default=0)
        for u in horses:
            u = str(u)
            res = by_gate.get(u)
            if not res:
                continue
            rank = _safe_int(res.get("rank"))
            if rank >= 99:
                continue
            tier = tiers.get(u)
            ts = _tier_score(tier)
            total_horses += 1
            if tier is None:
                isolated += 1
            pool.append((ts, rank))
            st = tier_stats.setdefault(tier, [0, 0, 0])
            st[0] += 1; st[1] += (1 if rank <= 3 else 0); st[2] += rank
            is_top = (ts == race_top_score and race_top_score > 0)
            if is_top:
                top_n += 1; top_place += (1 if rank <= 3 else 0)
            pop = _safe_int(res.get("pop"))
            if pop == 1:
                fav_n += 1; fav_place += (1 if rank <= 3 else 0)
            # 穴馬視点(4番人気以下)
            if 4 <= pop < 99:
                placed = 1 if rank <= 3 else 0
                ana_base_n += 1; ana_base_pl += placed
                if tier in ("S", "A"):
                    ana_hit_n += 1; ana_hit_pl += placed
                if is_top:
                    ana_top_n += 1; ana_top_pl += placed

    xs = [p[0] for p in pool]; ys = [-p[1] for p in pool]  # tierが高い×着順が良い→正
    rho = _spearman(xs, ys)
    return {
        "rho": rho, "n_horse": len(pool),
        "tier_stats": tier_stats,
        "top": (top_place, top_n),
        "fav": (fav_place, fav_n),
        "isolated": (isolated, total_horses),
        "ana_base": (ana_base_pl, ana_base_n),
        "ana_hit": (ana_hit_pl, ana_hit_n),
        "ana_top": (ana_top_pl, ana_top_n),
    }


def _fmt(label, m):
    print(f"\n===== {label} =====")
    print(f"  対象馬数: {m['n_horse']}   順位相関(正が良い): {m['rho']:+.3f}")
    iso, tot = m["isolated"]
    print(f"  孤立(tier=None): {iso}/{tot} ({(iso/tot*100 if tot else 0):.0f}%)")
    order = sorted([t for t in m["tier_stats"] if t],
                   key=lambda t: kb._TIER_LABELS.find(t)) + [None]
    print(f"  {'tier':>5} {'頭数':>4} {'複勝率':>7} {'平均着順':>7}")
    for t in order:
        st = m["tier_stats"].get(t)
        if not st:
            continue
        n, pl, rsum = st
        print(f"  {str(t):>5} {n:>4} {pl/n*100:>6.0f}% {rsum/n:>7.2f}")
    tp, tn = m["top"]; fp, fn = m["fav"]
    print(f"  最上位tier群 複勝率: {(tp/tn*100 if tn else 0):.0f}% ({tp}/{tn})")
    print(f"  1番人気(市場)複勝率: {(fp/fn*100 if fn else 0):.0f}% ({fp}/{fn})")
    bp, bn = m["ana_base"]; hp, hn = m["ana_hit"]; op, on = m["ana_top"]
    base = bp / bn * 100 if bn else 0
    print(f"  【穴馬視点 4番人気以下】 ベース複勝率: {base:.0f}% ({bp}/{bn})")
    print(f"    └ うち S/A tier : {(hp/hn*100 if hn else 0):.0f}% ({hp}/{hn})  [ベース比 {(hp/hn*100 - base) if hn else 0:+.0f}pt]")
    print(f"    └ うち最上位群   : {(op/on*100 if on else 0):.0f}% ({op}/{on})  [ベース比 {(op/on*100 - base) if on else 0:+.0f}pt]")


def eval_cmd(args):
    recs = _load_records()
    if not recs:
        print("backtest_data が空です。先に capture してください。"); return
    print(f"レース数: {len(recs)}")
    m_a = evaluate(recs, True, False, False)                                          # ① 現行(救済off/Sフロアon)
    m_b = evaluate(recs, True, False, True, lead_min=0.0, loose=True, s_floor=False)  # ② 本番(＝救済+Sフロアoff)
    _fmt("① 現行4段階 (救済off/Sフロアon)", m_a)
    _fmt("② 本番 (4段階+＝救済+Sフロアoff)", m_b)
    print("\n----- まとめ -----")
    print(f"  順位相関: ①{m_a['rho']:+.3f} → ②{m_b['rho']:+.3f}")
    def _rate(st, t):
        s = st.get(t)
        return f"{s[1]/s[0]*100:.0f}%({s[0]})" if s else "-"
    for lab, m in [("① 現行", m_a), ("② 本番", m_b)]:
        st = m["tier_stats"]
        print(f"  {lab} S:{_rate(st,'S')} A:{_rate(st,'A')} B:{_rate(st,'B')} C:{_rate(st,'C')} None:{_rate(st,None)}")
    kb._REL_NEW_RULE = True
    kb._REL_MULTI_TIER = False
    kb._REL_EQUAL_LEAD_PROMOTE = True
    kb._REL_EQUAL_LEAD_MIN = 0.0
    kb._REL_EQUAL_LEAD_LOOSE = True
    kb._REL_S_FLOOR = False
    kb._REL_C_DEMOTE = False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("capture")
    c.add_argument("--date", required=True)
    c.add_argument("--idplace", required=True, help="結果頁会場2桁(川崎21/船橋19/大井20/浦和18)")
    c.add_argument("--kai", required=True)
    c.add_argument("--nichi", required=True)
    c.add_argument("--races", default="1-12")
    c.add_argument("--force", action="store_true")
    sub.add_parser("eval")
    a = ap.parse_args()
    if a.cmd == "capture":
        capture(a)
    elif a.cmd == "eval":
        eval_cmd(a)
    else:
        ap.print_help()
