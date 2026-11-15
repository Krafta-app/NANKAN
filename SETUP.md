# 南関競馬AI セットアップ

このリポジトリは、役割を2つに分けています。

| 役割 | 使う場所 |
|---|---|
| 新しい予想を作る | MacBook |
| 予想・結果・メモを見る | Vercelサイト |

## ふだんの使い方

1. MacBookで **`MacBookで予想を作る.command`** をダブルクリック
2. ブラウザで予想生成画面が開く
3. 開催日・競馬場・レースを選んで実行
4. 画面内のHTMLダウンロードボタンで保存できる
5. Supabase設定済みなら、Vercelサイトにも自動で反映される

古い名前の **`run_umai.command`** も同じ予想生成画面を開きます。

## 翌日のデータを自動更新する

### Macの電源OFF・スリープ中も更新する（GitHub Actions）

`.github/workflows/auto-update.yml` がGitHubのクラウド上で毎日18:17〜23:17（日本時間）、
約1時間ごとに翌日の南関開催を確認し、全レースを生成してSupabaseへ保存します。
Macもアプリ画面も起動する必要はありません。GitHub側の混雑で開始が遅れる場合があります。

南関公式はGitHub標準実行環境からの接続を拒否するため、公開レースページの取得は
既存Vercelサイト `https://nankan.vercel.app/api/nankan-source` の東京リージョン経由です。
呼び出しはSupabaseキーから生成した2分有効の署名で認証し、キー自体は送信しません。
取得先とパスを南関公式の公開レース情報に限定し、ログイン情報・Cookieは転送しません。
Vercel側は既存の `SUPABASE_SERVICE_ROLE_KEY`（または `SUPABASE_KEY`）を使用し、
GitHub Secretsの `SUPABASE_KEY` と同じ値である必要があります。
`NANKAN_FETCH_GATEWAY` 環境変数が空なら従来どおり直接取得します。

リポジトリの **Settings → Secrets and variables → Actions** に、次の4項目を登録します。
値をソースコードへ書き込まないでください。

| GitHub Actions Secret | 既存のMac設定との対応 |
|---|---|
| `SUPABASE_URL` | `.streamlit/secrets.toml` の `[supabase] url` |
| `SUPABASE_KEY` | `[supabase] key`（サーバー側のservice_roleキー） |
| `KEIBABOOK_LOGIN_ID` | `[keibabook] login_id` |
| `KEIBABOOK_PASSWORD` | `[keibabook] password` |

ワークフローをデフォルトブランチに配置すると定期実行が有効になります。
**Actions → 翌日の南関データ自動更新 → Run workflow** で即時実行も可能です。
「開催確認のみ」を選ぶと、生成・保存は行いません。
クラウド用Supabase設定や競馬ブックの認証が無ければエラーで停止します。

開催ごとの完了記録は専用の `codex/auto-update-state` ブランチへ保存します。
一時キャッシュが消えても、完了済みの開催は再生成しません。
実行失敗時は次の定期実行で再試行し、ログはActionsで7日間確認できます。
GitHub側でActionsやワークフローを停止した場合、更新も止まります。
実行時間はGitHub Actionsの利用枠に算入されます。

クラウドで生成・保存できることを確認した後、Macとの重複を避けるため、Mac側は
`python3 tools/manage_auto_update.py uninstall` で停止してください。
クラウド側を停止する場合は、Actions画面からワークフローを無効にします。

### Macで実行する場合

**`④ 翌日データ自動更新を設定.command`** を一度開くと、Macの定期実行に登録します。
画面を開いていなくても、日本時間18〜23時の間、約1時間ごとに公式開催日程を確認します。

- 翌日の南関4場の開催を判定し、開催回・何日目かと対象レースを自動取得
- 開催がある競馬場の全レースを、通常の相対評価予想モードで生成・保存
- Supabase設定済みならVercelにも反映。未設定ならローカルSQLite・キャッシュへ保存
- 全レースの生成・保存が完了した開催はその晩の再実行をスキップ
- 未公開・取得失敗・保存失敗は次回確認時に再試行（失敗時は開催全体を再計算）

Macにログインしていて、スリープしていないこと・インターネット接続が必要です。
電源OFF・スリープ中は動きません。更新開始後の処理は23時を過ぎても継続します。
毎時の起動なので、確認は18時ちょうどとは限りません。競馬ブックなどの設定は通常の予想生成と共通です。
当日のオッズ・馬体重・出走取消などを追跡する機能ではありません。

確認・停止はプロジェクトのフォルダで次を実行します。

```sh
python3 auto_update.py --check                 # 翌日の開催とレースのみ確認（保存しない）
python3 tools/manage_auto_update.py status     # 定期実行の状態
python3 tools/manage_auto_update.py uninstall  # 自動更新を停止
```

ログは `logs/auto_update.log` と `logs/auto_update_error.log`、完了記録は
`cache/auto_update_state.json` に保存します。強制再取得は `python3 auto_update.py --force` です。

## 指数の基準タイム更新

**`③ 指数基準タイムを更新.command`** を開くと、速度指数の基準を最新化します。

- 地方競馬: NAR公式CSVの直近5年＋当日の終了レース
- JRA: course-dbの全102コース
- 地方は競馬場×芝/ダート×回り×距離で基準化
- 過去走は、その開催日全体が高速/低速だったかを馬場差として補正
- 当日は前半3R以上の確定後にもう一度更新すると、後半レースの指数欄へ本日の高速/低速、秒/1000m、根拠R数を表示

最初の1回は過去5年分を取得するため時間がかかります。2回目以降は保存済みの月次データを再利用します。更新に失敗しても、直前の基準データは消されません。

## Vercelサイト

スマホや別PCで見る画面はVercelです。設定手順は **`VERCEL_DEPLOY.md`** を見てください。

Vercel側でできること:

- 予想を見る
- 結果を見る
- 成績を見る
- 馬メモ・好走パターンを保存する

## Supabase設定

MacBookから作った予想をVercelに出すには、Mac側にもSupabase設定が必要です。

1. **`① 設定を開く.command`** を開く
2. `.streamlit/secrets.toml` に以下を入れる

```toml
[supabase]
url = "https://xxxxx.supabase.co"
key = "service_role の長い鍵"
```

3. 保存して閉じる

過去データをまとめてクラウドへ送る時は **`② データをクラウドへ.command`** を使います。

## 仕組み

```txt
main.py       MacBook用の予想生成画面
keiba_bot.py 予想生成の本体
store.py     Supabase設定があればcloud_db、なければローカルSQLiteを使う
cloud_db.py  Supabase保存
db.py        ローカルSQLite保存
fetch_results.py レース結果取得
vercel_site/ Vercelで見るWebサイト
```

## NAR月次CSVの実績を使う

騎手評価と相対評価の材料を、NAR公式の月次レースCSVから作れるようになりました。
仕組み・検証結果・止め方は [docs/NAR_CSV_MODEL.md](docs/NAR_CSV_MODEL.md) を見てください。

CSVは `cache/nar_speed_csv/monthly/*.zip`（`tools/update_nar_speed_reference.py` が取得）と
`data/nar_csv/`、`../../ばんえい予想/data/` から自動で読みます。何も無ければ
これまで通りスクレイピングだけで動きます。
