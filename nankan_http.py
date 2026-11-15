"""クラウド実行時だけ、署名付きVercel API経由で南関の公開ページを読む。"""
import base64
import hashlib
import hmac
import os
import time
from urllib.parse import urlsplit

import requests


class NankanSession(requests.Session):
    def request(self, method, url, **kwargs):
        gateway = os.environ.get("NANKAN_FETCH_GATEWAY", "")
        parsed = urlsplit(url)
        if not (gateway and method.upper() == "GET" and parsed.scheme == "https"
                and parsed.hostname == "www.nankankeiba.com"):
            return super().request(method, url, **kwargs)
        key = os.environ.get("SUPABASE_KEY", "")
        if not key:
            raise RuntimeError("公式データ取得用の署名キーが設定されていません")
        prepared = requests.Request("GET", url, params=kwargs.get("params")).prepare()
        target = urlsplit(prepared.url)
        path = target.path + ("?" + target.query if target.query else "")
        timestamp = str(int(time.time()))
        signature = hmac.new(key.encode(), f"nankan-fetch-v1\n{timestamp}\n{path}".encode(), hashlib.sha256).hexdigest()
        gateway_response = super().request("POST", gateway, json={"path": path}, headers={
            "x-nankan-time": timestamp, "x-nankan-signature": signature,
        }, timeout=25)
        gateway_response.raise_for_status()
        payload = gateway_response.json()
        response = requests.Response()
        response.status_code = int(payload["status"])
        response._content = base64.b64decode(payload["body"], validate=True)
        response.url = prepared.url
        response.request = prepared
        response.headers["Content-Type"] = payload.get("contentType", "text/html")
        response.encoding = "cp932"
        return response


def create_session():
    return NankanSession()
