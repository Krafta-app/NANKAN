# -*- coding: utf-8 -*-
"""DBバックエンドの切替レイヤ。

- Supabase の接続情報（環境変数 or Streamlit secrets の [supabase]）があれば **クラウド(Supabase)**
- 無ければ **ローカル(SQLite)**

他モジュールは `import store as db` として使う。関数APIは db.py / cloud_db.py で共通。
"""
import cloud_db

if cloud_db.is_configured():
    BACKEND = "supabase"
    from cloud_db import *  # noqa: F401,F403
else:
    BACKEND = "sqlite"
    from db import *  # noqa: F401,F403
