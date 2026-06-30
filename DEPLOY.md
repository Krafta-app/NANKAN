# 📱 スマホで見られるようにする手順（やさしい版）

やりたいこと：**スマホだけで、いつでも予想・結果・メモを見る**（Macの電源OFFでもOK）。

そのために3つのサービスを使います（全部無料・あなたは持っている）：
- **Supabase** … データの保管庫（予想・結果・メモを入れておく場所）
- **GitHub** … アプリの本体（プログラム）を置く場所
- **Streamlit Cloud** … アプリを動かして、URLで開けるようにする場所（GitHubでログインするだけ。新規登録不要）

> ※ Cloudflare はこの方法では使いません（無くて大丈夫）。
> ※ 「新しい予想を作る」のは今まで通り Mac の `run_umai.command`。作ると自動でスマホ側にも出ます。

全部で **30〜40分** くらい。落ち着いて1ステップずつどうぞ。

---

## STEP 1　Supabase に「データの保管庫」を作る（約10分）

1. https://supabase.com/dashboard を開く → 緑の **New project** をクリック
2. 名前は何でもOK（例 `nankan`）、**Region** は `Northeast Asia (Tokyo)` を選ぶ → **Create new project**
   （数分待つと出来上がります）
3. 出来たら、左の細いメニューから **SQL Editor**（紙にペンのアイコン）をクリック
4. このフォルダの中の **`supabase` フォルダ → `schema.sql`** をダブルクリックで開く
   → 中身を**ぜんぶ選択してコピー**（⌘A → ⌘C）
5. Supabase の SQL Editor の白い入力欄に**貼り付け**（⌘V）→ 右下の **Run**（または ⌘+Enter）
   → 「Success」と出ればOK（これでデータの入れ物＝表が出来ました）
6. 鍵を2つ控えます。左メニュー下の **⚙ Project Settings → API** を開く：
   - **Project URL**（`https://…….supabase.co`）をコピーしてメモ
     ⚠️ 末尾の **`/rest/v1/` は付けない**（`.supabase.co` まで）。付けて貼っても自動で除去します。
   - 少し下の **Project API keys** の中の **`service_role`**（`secret` と書いてある長い文字列）の
     コピーボタンを押してメモ
     ※ `anon` ではなく **`service_role`** の方です。これは秘密のパスワードなので人に見せない。

> 💡 後から機能追加（好走パターン等）した時は、SQL Editor で `supabase/schema.sql` を**もう一度 Run**してください（`if not exists` なので列だけ安全に追加されます）。

---

## STEP 2　Mac に鍵を入れる（約3分）

1. このフォルダの **「① 設定を開く.command」** をダブルクリック
   - 「開けません」と出たら：ファイルを**右クリック → 開く → 開く** でOK（初回だけ）
2. テキストエディタが開きます。下の方の `[supabase]` のところを、STEP1で控えた値に書き換える：
   ```
   [supabase]
   url = "https://…….supabase.co"      ← Project URL を貼る
   key = "eyJhbGci……（とても長い文字列）"  ← service_role を貼る
   ```
   `"` （ダブルクオート）は消さず、その**中身だけ**書き換えてください。
3. **⌘S で保存**して、エディタを閉じる。

---

## STEP 3　これまでのデータをクラウドへ送る（約2分）

1. このフォルダの **「② データをクラウドへ.command」** をダブルクリック
2. 黒い画面が出て「✅ ……アップロード」と流れて「終わりました」と出れば成功
   → これで今までの予想・結果・メモがクラウドに入りました。
   （今後は `run_umai.command` で予想を作るたび、自動でクラウドにも入ります）

---

## STEP 4　アプリ本体を GitHub に置く（約10分／GitHub Desktop が一番ラク）

パソコン操作に不慣れな場合は **GitHub Desktop**（ボタンで操作できるアプリ）がおすすめです。

1. https://desktop.github.com/ から **GitHub Desktop** をダウンロードして開き、自分のGitHubでログイン
2. 上のメニュー **File → Add Local Repository…**
3. **Choose…** で、このフォルダ（`2603_NANKAN AI`）を選ぶ
   - 「これはGitリポジトリではありません」と出たら、青い文字の **create a repository** をクリック → **Create Repository**
4. 右上あたりの **Publish repository**（公開）をクリック
   - **Keep this code private**（非公開）に**チェックを入れたまま** → **Publish Repository**
   - ※ パスワードや鍵（secrets）は自動で除外されるので、誤って公開されません（安心）

これで GitHub にアプリ本体が上がりました。

---

## STEP 5　Streamlit Cloud で公開する（約7分）

1. https://share.streamlit.io/ を開く → **Continue with GitHub**（GitHubでログイン）
2. **Create app**（または「New app」）→ **Deploy a public app from a repository** を選ぶ
3. 入力欄：
   - **Repository** … さっき作ったもの（`あなたの名前/2603_NANKAN AI`）を選ぶ
   - **Branch** … `main`
   - **Main file path** … `main.py`
4. **Advanced settings**（詳細設定）を開く → **Secrets** の欄に、次を**そのまま貼り付けて**
   url と key を STEP1 の値に書き換える：
   ```
   is_cloud = true

   [supabase]
   url = "https://…….supabase.co"
   key = "eyJhbGci……（service_role）"
   ```
5. **Deploy!** を押す → 数分で `https://…….streamlit.app` というURLができます。

---

## 🎉 スマホで開く

1. スマホのブラウザ（Safari/Chrome）でそのURLを開く
2. **共有ボタン → ホーム画面に追加** → アプリのアイコンとして使えます
3. 下/横のメニューから 📚アーカイブ・🏁結果成績・📝メモ

結果はクラウド側が開催時間帯に自動で取りに行きます。メモもスマホからその場で書けます。

---

## これからの使い方（まとめ）
- **新しい予想を作る** → Mac で `run_umai.command`（自動でスマホ側にも反映）
- **見る・結果・メモ** → スマホのアイコンからいつでも（Macは消えててOK）
- うまく出ないとき → まず Mac で `run_umai.command` を1回動かして予想を作る／少し待つ

## 困ったら
- スマホで真っ白／エラー → Streamlit Cloud の画面で **Manage app → 再起動(Reboot)**。Secrets の貼り間違いがないか確認
- データが出ない → STEP2の鍵（url/key）が正しいか、STEP3を実行したか確認
- 鍵を入れ直したい → もう一度 **「① 設定を開く.command」**
