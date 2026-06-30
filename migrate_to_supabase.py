# -*- coding: utf-8 -*-
"""ローカル(SQLite + cache) の既存データを Supabase へ一括アップロードする。
Supabase の接続情報（環境変数 or .streamlit/secrets.toml の [supabase]）が必要。

  python3 migrate_to_supabase.py

- races（予想台帳）と data_html は cache から
- race_results（着順）と horse_notes（メモ）はローカルSQLiteから
冪等（何度実行しても重複しない upsert）。
"""
import json
import sqlite3
from collections import defaultdict

import db
import cloud_db


def main():
    if not cloud_db.is_configured():
        print("❌ Supabaseの接続情報がありません（SUPABASE_URL/KEY か secrets[supabase]）。中止。")
        return
    cloud_db.init_db()

    # 1) races + data_html（cacheから）
    n_races = cloud_db.backfill_from_cache()
    print(f"✅ races(+HTML) を {n_races} 件アップロード")

    # 2) race_results / horse_notes（ローカルSQLiteから）
    try:
        conn = sqlite3.connect(db.DB_PATH)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"⚠️ ローカルSQLiteを開けません（{e}）。results/notes はスキップ。")
        return

    grouped = defaultdict(list)
    for r in conn.execute("SELECT * FROM race_results").fetchall():
        grouped[r["race_key"]].append({
            "umaban": r["umaban"], "horse_name": r["horse_name"], "uma_id": r["uma_id"],
            "finish_rank": r["finish_rank"], "popularity": r["popularity"], "time_diff": r["time_diff"],
        })
    for rk, rows in grouped.items():
        cloud_db.upsert_results(rk, rows)
    print(f"✅ race_results を {len(grouped)} レース分アップロード")

    notes = conn.execute("SELECT * FROM horse_notes").fetchall()
    for nt in notes:
        cloud_db.set_note(nt["uma_id"], nt["horse_name"], nt["note_text"])
        cols = nt.keys()
        pj = nt["pattern_json"] if "pattern_json" in cols else None
        if pj:
            try:
                cloud_db.set_pattern(nt["uma_id"], nt["horse_name"], json.loads(pj))
            except Exception:
                pass
    print(f"✅ horse_notes を {len(notes)} 件アップロード")

    print("🎉 完了。Vercelサイトから見えるはずです。")


if __name__ == "__main__":
    main()
