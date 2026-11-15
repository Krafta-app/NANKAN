"""公開ページへの接続を確認する（ログイン情報は使用しない）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import requests
from bs4 import BeautifulSoup
from nankan_schedule import HEADERS, fetch_browser_html


for url in ("https://www.nankankeiba.com/calendar/202610.do",
            "https://s.keibabook.co.jp/login/login"):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.encoding = "cp932" if "nankankeiba" in url else response.apparent_encoding
    soup = BeautifulSoup(response.text, "html.parser")
    print(url, "HTTP", response.status_code,
          "title:", soup.title.get_text(strip=True) if soup.title else "none", flush=True)
    if response.status_code == 403:
        print("Response:", soup.get_text(" ", strip=True)[:160], flush=True)
        if "nankankeiba" in url:
            try:
                html = fetch_browser_html(url)
                page = BeautifulSoup(html, "html.parser")
                print("Chrome title:", page.title.get_text(strip=True) if page.title else "none",
                      "body:", page.get_text(" ", strip=True)[:160], flush=True)
            except Exception as exc:
                print("Chrome error:", type(exc).__name__, str(exc)[:250], flush=True)
