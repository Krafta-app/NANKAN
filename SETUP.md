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
