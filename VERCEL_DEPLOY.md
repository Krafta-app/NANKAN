# 南関競馬AIをVercelサイトにする手順

## できること

- Streamlitではなく、普通のWebサイトとして予想を見る
- VercelのURL、またはCloudflareで持っている独自ドメインで開く
- Supabaseに保存済みの予想・結果・馬メモを表示する
- スマホから馬メモと好走パターンを保存する

## 役割

| 役割 | 使うもの |
|---|---|
| 新しい予想を作る | Macの `MacBookで予想を作る.command` |
| 予想・結果・メモの保管 | Supabase |
| 普通のWebサイト画面 | Vercel |
| 独自ドメイン | Cloudflare DNS |

## 1. Supabaseは今のものを使う

すでにSupabaseを作っている場合は、そのまま使えます。

Vercelに入れる値はこの2つです。

```txt
SUPABASE_URL=https://.....supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ.....
```

`service_role` キーは秘密です。Vercelの環境変数に入れるだけで、画面側には出ません。

## 2. Vercelに置く

1. GitHubにこのリポジトリを置く
2. Vercelで **Add New Project**
3. このリポジトリを選ぶ
4. **Root Directory** を `vercel_site` にする
5. Build Command / Output Directory は空のままでOK
6. Environment Variables に以下を入れる

```txt
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
NANKAN_SITE_PIN
```

`NANKAN_SITE_PIN` はスマホでメモ保存するときのPINです。不要なら空でも動きます。

## 3. CloudflareのドメインをVercelへ向ける

VercelのProject Settings → Domainsで使いたいドメインを追加します。

Cloudflare DNSでは、Vercel画面に表示された指示どおりに入れます。

| 使うURL | Cloudflareで入れるもの |
|---|---|
| `nankan.example.com` などのサブドメイン | CNAME |
| `example.com` のようなルートドメイン | Aレコード |

まずは `nankan.example.com` のようなサブドメインがおすすめです。

## 4. 日常の使い方

1. Macで予想を作る
2. 予想がSupabaseへ保存される
3. スマホでVercel/独自ドメインのURLを開く
4. メモを書く場合はPINを入れて保存

Mac側の `.streamlit/secrets.toml` にSupabase設定が入っていれば、予想生成時にVercelサイトへ反映されます。

## 5. ローカル確認

画面だけ確認する場合:

```bash
cd vercel_site
python3 -m http.server 4173 --directory public
```

ブラウザで `http://localhost:4173/?demo=1` を開くと、デモデータで見た目を確認できます。

API込みで確認する場合は、Vercel CLIで:

```bash
cd vercel_site
vercel dev
```

その場合は `.env.local` に `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` / `NANKAN_SITE_PIN` を入れてください。
