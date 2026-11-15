"""南関公式の開催リンクから、日付・場・開催回・日目を取得する。"""
import re
from dataclasses import dataclass
import requests
from bs4 import BeautifulSoup
from nankan_http import create_session

BASE_URL = "https://www.nankankeiba.com"
PLACES = {"20": ("10", "大井"), "21": ("11", "川崎"),
          "19": ("12", "船橋"), "18": ("13", "浦和")}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class Meeting:
    program_id: str

    @property
    def place_code(self):
        return PLACES[self.program_id[8:10]][0]

    @property
    def place_name(self):
        return PLACES[self.program_id[8:10]][1]

    @property
    def kai(self):
        return int(self.program_id[10:12])

    @property
    def nichi(self):
        return int(self.program_id[12:14])


def parse_meetings(html, target_date):
    found = {}
    for a in BeautifulSoup(html, "html.parser").select("a[href]"):
        match = re.search(r"/program/(\d{14})\.do(?:[?#]|$)", a["href"])
        if not match:
            continue
        pid = match.group(1)
        if (pid[:8] == target_date.strftime("%Y%m%d") and pid[8:10] in PLACES
                and int(pid[10:12]) > 0 and int(pid[12:14]) > 0):
            place = pid[8:10]
            if place in found and found[place].program_id != pid:
                raise ValueError("同じ日・競馬場に複数の開催IDがあります")
            found[place] = Meeting(pid)
    return sorted(found.values(), key=lambda m: m.program_id)


def fetch_html(session, path):
    response = session.get(BASE_URL + path, timeout=30, headers=HEADERS)
    if response.status_code == 403:
        # HTTP取得だけが拒否される環境では、既存の予想処理と同じChromeで確認する。
        # ログインが必要なページには使用しない。
        return fetch_browser_html(BASE_URL + path)
    response.raise_for_status()
    response.encoding = "cp932"
    return response.text


def fetch_browser_html(url):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    for option in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--lang=ja-JP"):
        options.add_argument(option)
    driver = webdriver.Chrome(options=options)
    try:
        driver.set_page_load_timeout(30)
        driver.get(url)
        html = driver.page_source
        title = driver.title or ""
        if any(word in title.lower() for word in ("403", "forbidden", "access denied")):
            raise requests.HTTPError(f"公式サイトがChromeからの接続も拒否しました: {title}")
        return html
    finally:
        driver.quit()


def discover_meetings(target_date, session=None):
    session = session or create_session()
    # 年間日程は四半期の先頭月を指定する（11月なら10月）。
    quarter_month = ((target_date.month - 1) // 3) * 3 + 1
    html = fetch_html(session, f"/calendar/{target_date.year}{quarter_month:02}.do")
    soup = BeautifulSoup(html, "html.parser")
    if not soup.title or "開催日程" not in soup.title.get_text():
        title = soup.title.get_text(strip=True) if soup.title else "タイトルなし"
        message = soup.get_text(" ", strip=True)[:160]
        raise ValueError(f"公式開催日程を取得できません: {title} / {message}")
    return parse_meetings(html, target_date)


def discover_races(meeting, session=None):
    html = fetch_html(session or create_session(), f"/program/{meeting.program_id}.do")
    races = set()
    for a in BeautifulSoup(html, "html.parser").select("a[href]"):
        match = re.search(r"/syousai/(\d{16})\.do(?:[?#]|$)", a["href"])
        if match and match.group(1).startswith(meeting.program_id):
            number = int(match.group(1)[14:])
            if 1 <= number <= 12:
                races.add(number)
    if not races:
        raise ValueError(f"{meeting.place_name}: 出走表未公開または取得失敗")
    return sorted(races)
