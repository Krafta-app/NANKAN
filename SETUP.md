# 南関競馬AI セットアップ

予想をブラウザで見て、レース結果を自動で照合し、馬ごとにメモを残せます。

## 全体像（ハイブリッド構成）
- **新しい予想の生成** … Mac の `run_umai.command`（Seleniumで競馬ブックにログインするためMacで動かす）
- **閲覧・結果・メモ** … クラウド（Streamlit Cloud）でスマホからいつでも（PC不要）
- **データ共有** … Supabase（Macで生成 → 自動でクラウドに反映）

> まずは Mac だけでも完結します（下記）。スマホ完結にしたくなったら **DEPLOY.md** でクラウド化。

---

## A. Mac で使う（すぐ動く）
`run_umai.command` をダブルクリック → ブラウザで `http://localhost:8501`。

左メニュー：

| ページ | 内容 |
|---|---|
| **予想生成** | 今まで通り。生成すると自動保存され、画面内にタブ表示（DLも可） |
| **📚 アーカイブ** | 過去の予想を日付・競馬場で選んで画面内表示。結果照合・メモもここ |
| **🏁 結果成績** | 予想tier別の的中率（S/A/B/C/D/E）とレースごとの的中 |
| **📝 メモ** | 馬ごとのメモを横断検索・編集。その馬の過去走も表示 |

- メモは **馬ごと（uma_id）** に保存 → 同じ馬は別の日・別レースでも同じメモが出ます。
- アプリ起動中は、開催時間帯（16〜23時）に **結果を自動取得**します。
- 今すぐ結果を取りたいときは `update_results.command`（または `python3 fetch_results.py`）。
- 同じWi-Fiのスマホからも見られます（`http://<MacのIP>:8501`）。MacのIPは「システム設定→ネットワーク」で確認。

### データの場所（Macローカル）
- `data/keiba.db` … 予想台帳・結果・メモ（**メモはここにあるので消さない**）
- `cache/*_dify.json` … 予想本体（アーカイブ表示に使用）

---

## B. スマホ完結（PC不要で閲覧）にする
クラウド化が必要です。手順は **DEPLOY.md** を参照（Supabase + Streamlit Cloud、いずれも無料）。
クラウド化すると：
- スマホでいつでも閲覧・結果・メモ（Macの電源OFFでもOK）
- 新予想を作る時だけ Mac で `run_umai.command`（自動でクラウドに反映）

---

## 仕組み（メンテ用）
```
db.py        … ローカルSQLite実装
cloud_db.py  … Supabase実装（同じ関数API）
store.py     … secretsにSupabaseがあればcloud_db、無ければdb を採用（import store as db）
fetch_results.py … /result/{race_id}.do を requests で取得（selenium不要）
ui_common.py … DB初期化＋結果自動取得スレッド＋メモ欄など共通UI
main.py + pages/ … 画面（生成 / アーカイブ / 結果成績 / メモ）
```
- 結果取得チェーン：`build_nankan_race_id` → `/result/{id}.do` → `fetch_race_all_horses` → DB
- race_id は生成時に保存。旧キャッシュは `cache/taisen_*.html` のファイル名から復元。
