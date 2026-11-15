"""Macログイン中に翌日分の更新を定期実行するLaunchAgentを登録・解除する。"""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.nankan-ai.auto-update"


def agent_config():
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-u", str(ROOT / "auto_update.py")],
        "WorkingDirectory": str(ROOT),
        "StartInterval": 3600,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": str(Path(sys.executable).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": str(ROOT / "logs" / "auto_update.log"),
        "StandardErrorPath": str(ROOT / "logs" / "auto_update_error.log"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall", "status", "preview"])
    args = parser.parse_args()
    domain = f"gui/{os.getuid()}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if args.action == "preview":
        print(plistlib.dumps(agent_config()).decode())
        return
    if args.action == "status":
        raise SystemExit(subprocess.call(["launchctl", "print", f"{domain}/{LABEL}"]))
    if args.action == "uninstall":
        if plist.exists():
            subprocess.run(["launchctl", "bootout", domain, str(plist)], check=False)
            plist.unlink()
        print("翌日データ自動更新を停止しました")
        return
    (ROOT / "logs").mkdir(exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    if plist.exists():
        subprocess.run(["launchctl", "bootout", domain, str(plist)], check=False)
    plist.write_bytes(plistlib.dumps(agent_config()))
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print("翌日データ自動更新を有効にしました（毎時確認、18〜23時JSTに更新）")


if __name__ == "__main__":
    main()
