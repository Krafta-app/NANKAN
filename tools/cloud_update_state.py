"""実行完了記録を専用Gitブランチへ永続化する。認証情報・予想本文は保存しない。"""
import argparse
import base64
import json
import os
from pathlib import Path

import requests

BRANCH = "codex/auto-update-state"
REMOTE_PATH = "auto-update-state.json"
STATE = Path(__file__).resolve().parents[1] / "cache" / "auto_update_state.json"


def sync(action, session=None):
    session = session or requests.Session()
    session.headers.update({"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                            "Accept": "application/vnd.github+json"})
    base = "https://api.github.com/repos/" + os.environ["GITHUB_REPOSITORY"]
    response = session.get(f"{base}/contents/{REMOTE_PATH}", params={"ref": BRANCH}, timeout=30)
    if response.status_code != 404:
        response.raise_for_status()
        remote = response.json()
        content = base64.b64decode(remote["content"])
        json.loads(content)  # 壊れた記録なら、重複更新する前にエラーにする。
    else:
        remote, content = None, None
    if action == "download":
        if content is not None:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_bytes(content)
        print("クラウドの完了記録を復元しました" if remote else "初回実行：完了記録はまだありません")
        return
    if not STATE.exists():
        print("保存する完了記録はありません")
        return
    local = STATE.read_bytes()
    state = json.loads(local)
    # 公開ブランチには開催IDと完了情報だけを保存する。
    for pid, entry in state.items():
        if not (pid.isdigit() and len(pid) == 14 and set(entry) <= {"races", "completed_at", "backend"}):
            raise ValueError("完了記録に想定外の項目があります")
    if content == local:
        print("完了記録に変更なし")
        return
    branch = session.get(f"{base}/git/ref/heads/{BRANCH}", timeout=30)
    if branch.status_code == 404:
        created = session.post(f"{base}/git/refs", json={
            "ref": "refs/heads/" + BRANCH, "sha": os.environ["GITHUB_SHA"],
        }, timeout=30)
        created.raise_for_status()
    else:
        branch.raise_for_status()
    payload = {"message": "Record completed Nankan data updates", "branch": BRANCH,
               "content": base64.b64encode(local).decode("ascii")}
    if remote:
        payload["sha"] = remote["sha"]
    saved = session.put(f"{base}/contents/{REMOTE_PATH}", json=payload, timeout=30)
    saved.raise_for_status()
    print("クラウドの完了記録を保存しました")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["download", "upload"])
    sync(parser.parse_args().action)
