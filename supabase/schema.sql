-- 南関AI クラウドDB スキーマ（Supabase / PostgreSQL）
-- Supabase ダッシュボード → SQL Editor に貼り付けて実行してください。
-- ローカルSQLite(db.py)と同じ構造をPostgresで再現したものです。

create table if not exists races (
    race_key       text primary key,
    date           text,
    place_code     text,
    place_name     text,
    race_num       integer,
    race_id        text,
    course         text,
    dist           text,
    race_name      text,
    grades_json    text,
    eval_list_text text,
    uma_ids_json   text,
    generated_at   text,
    has_result     integer default 0
);

-- アーカイブ表示用の整形HTML（サイズが大きいので別テーブル）
create table if not exists race_pages (
    race_key  text primary key references races(race_key) on delete cascade,
    data_html text,
    data_text text
);

create table if not exists race_results (
    race_key    text,
    umaban      integer,
    horse_name  text,
    uma_id      text,
    finish_rank integer,
    popularity  integer,
    time_diff   double precision,
    fetched_at  text,
    primary key (race_key, umaban)
);

create table if not exists horse_notes (
    uma_id       text primary key,
    horse_name   text,
    note_text    text,
    pattern_json text,           -- 好走パターン {逃げ/番手/内枠/中枠/外枠: ◯/△/✕}
    updated_at   text
);
-- 既にテーブルがある場合の列追加（無ければ追加）
alter table horse_notes add column if not exists pattern_json text;

create table if not exists horse_marks (
    race_key   text,
    uma_id     text,
    mark       text,
    updated_at text,
    primary key (race_key, uma_id)
);

create index if not exists idx_races_date   on races(date);
create index if not exists idx_results_uma  on race_results(uma_id);
create index if not exists idx_results_name on race_results(horse_name);

-- このアプリは MacBook の予想生成画面と Vercel Functions から接続し、service_role キーを使う想定です。
-- service_role キーは RLS をバイパスするので、まずは RLS 無効のままでOK（鍵はブラウザに出ません）。
-- もし anon キーで運用したい場合は各テーブルで RLS を有効化し、全許可ポリシーを作ってください。
