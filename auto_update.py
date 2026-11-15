"""翌日の南関開催を自動更新。Macまたはクラウドで18〜23時(JST)に実行。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import logging
import os
from pathlib import Path

from nankan_schedule import discover_meetings, discover_races

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "cache" / "auto_update_state.json"
JST = timezone(timedelta(hours=9))
log = logging.getLogger("auto_update")


@contextmanager
def run_lock():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with (STATE.parent / "auto_update.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_state():
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text(encoding="utf-8"))


def write_state(state):
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE)


def update_meeting(meeting, target_date, races, runner):
    completed, errors = set(), []
    for event in runner(
        target_date.year, f"{target_date.month:02}", f"{target_date.day:02}",
        meeting.place_code, set(races), mode="dify",
        manual_kai_nichi={"kai": meeting.kai, "nichi": meeting.nichi},
        bypass_cache=True, strict_persistence=True,
    ):
        kind = event.get("type")
        if kind == "result":
            completed.add(event["race_num"])
        elif kind == "error":
            errors.append(str(event.get("data")))
        log.info("%s %s", meeting.place_name, event.get("data", kind))
    missing = set(races) - completed
    if errors or missing:
        raise RuntimeError(f"未完了R={sorted(missing)}, エラー={errors}")


def run(now=None, check_only=False, force=False, run_now=False, require_cloud=False):
    now = now or datetime.now(JST)
    now = now.astimezone(JST)
    if not (check_only or force or run_now) and not 18 <= now.hour <= 23:
        return 0
    if require_cloud:
        import store
        if store.BACKEND != "supabase":
            raise RuntimeError("クラウド更新にはSupabase設定が必要です。ローカル保存へ切り替えず中止します")
        if not check_only and not (os.environ.get("KEIBABOOK_LOGIN_ID") and os.environ.get("KEIBABOOK_PASSWORD")):
            raise RuntimeError("競馬ブックのクラウド用認証設定がありません")
    target = now.date() + timedelta(days=1)
    with run_lock() as acquired:
        if not acquired:
            log.info("別の自動更新が実行中です")
            return 0
        meetings = discover_meetings(target)
        if not meetings:
            # 未公開・休催いずれも完了扱いにせず、次回も公式を確認する。
            log.info("%s: 公開済みの南関開催なし。次回も確認します", target)
            return 0
        state = read_state()
        failed = False
        for meeting in meetings:
            try:
                races = discover_races(meeting)
                log.info("%s %s 第%s回 %s日目: %sR", target, meeting.place_name,
                         meeting.kai, meeting.nichi, races)
                if check_only:
                    continue
                previous = state.get(meeting.program_id, {})
                if (not force and previous.get("races") == races
                        and (not require_cloud or previous.get("backend") == "supabase")):
                    log.info("更新済みのためスキップ")
                    continue
                import keiba_bot
                import store
                store.init_db()
                update_meeting(meeting, target, races, keiba_bot.run_races_iter)
                state[meeting.program_id] = {
                    "races": races, "completed_at": datetime.now(JST).isoformat(),
                    "backend": store.BACKEND,
                }
                cutoff = (target - timedelta(days=7)).strftime("%Y%m%d")
                state = {k: v for k, v in state.items() if k[:8] >= cutoff}
                write_state(state)
                log.info("全レースの生成・保存が完了しました (%s)", store.BACKEND)
            except Exception:
                failed = True
                log.exception("%s 更新失敗。次回に再試行します", meeting.place_name)
        return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="翌日の開催・レースを確認するだけ")
    parser.add_argument("--force", action="store_true", help="時間帯・完了状態によらず翌日分を更新")
    parser.add_argument("--run-now", action="store_true", help="時間帯によらず実行（完了済みはスキップ）")
    parser.add_argument("--require-cloud", action="store_true", help="Supabase未設定なら中止する")
    args = parser.parse_args()
    os.chdir(ROOT)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run(check_only=args.check, force=args.force, run_now=args.run_now, require_cloud=args.require_cloud)
    except Exception:
        log.exception("開催確認に失敗しました。次回に再試行します")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
