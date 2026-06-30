# 南関競馬AI Vercelサイト

Streamlitの閲覧部分を置き換える、Vercel用の軽量Webサイトです。

- `public/` ... スマホ・PCで見る画面
- `api/` ... Supabaseを読む/メモを保存するVercel Functions
- `vercel.json` ... Vercel用設定

Vercelでこのフォルダを **Root Directory** にしてください。

必要な環境変数:

```txt
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
NANKAN_SITE_PIN=好きなPIN
```

`SUPABASE_SERVICE_ROLE_KEY` はブラウザには出ません。Vercel Functionsの中だけで使います。
