import datetime as dt
import base64
import json
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import auto_update as auto
from nankan_schedule import Meeting, parse_meetings, discover_meetings, discover_races
from tools import cloud_update_state as cloud_state


class ScheduleTests(unittest.TestCase):
    def test_exact_date_and_nankan_only(self):
        html = ''.join(f'<a href="/program/{pid}.do">出走表</a>' for pid in [
            "00000000000000", "20261116201201", "20261116201201",
            "20261117191002", "20261116031605", "20261116180801"])
        meetings = parse_meetings(html, dt.date(2026, 11, 16))
        self.assertEqual([m.place_name for m in meetings], ["浦和", "大井"])
        self.assertEqual((meetings[1].place_code, meetings[1].kai, meetings[1].nichi),
                         ("10", 12, 1))

    def test_conflicting_ids_rejected(self):
        with self.assertRaises(ValueError):
            parse_meetings('<a href="/program/20261116201201.do"></a>'
                           '<a href="/program/20261116201301.do"></a>', dt.date(2026, 11, 16))

    def test_quarter_url_and_year_boundary(self):
        with patch("nankan_schedule.fetch_html", return_value="<title>年間開催日程</title>") as fetch:
            discover_meetings(dt.date(2026, 11, 16), object())
            self.assertEqual(fetch.call_args.args[1], "/calendar/202610.do")
            discover_meetings(dt.date(2027, 1, 1), object())
            self.assertEqual(fetch.call_args.args[1], "/calendar/202701.do")

    def test_bad_page_is_error_not_no_meetings(self):
        with patch("nankan_schedule.fetch_html", return_value="<title>メンテナンス</title>"):
            with self.assertRaises(ValueError):
                discover_meetings(dt.date(2026, 11, 16))

    def test_races_match_meeting_not_navigation(self):
        html = ''.join(f'<a href="/syousai/{pid}.do"></a>' for pid in [
            "2026111620120101", "2026111620120112", "2026111720120202"])
        with patch("nankan_schedule.fetch_html", return_value=html):
            self.assertEqual(discover_races(Meeting("20261116201201")), [1, 12])
        with patch("nankan_schedule.fetch_html", return_value=""):
            with self.assertRaises(ValueError):
                discover_races(Meeting("20261116201201"))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_patch = patch.object(auto, "STATE", Path(self.temp.name) / "cache/state.json")
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.meeting = Meeting("20261116201201")

    def test_runner_requires_every_race_and_no_errors(self):
        def runner(*args, **kwargs):
            self.assertEqual(kwargs["manual_kai_nichi"], {"kai": 12, "nichi": 1})
            self.assertTrue(kwargs["bypass_cache"])
            self.assertTrue(kwargs["strict_persistence"])
            yield {"type": "result", "race_num": 1}
        with self.assertRaises(RuntimeError):
            auto.update_meeting(self.meeting, dt.date(2026, 11, 16), [1, 2], runner)
        auto.update_meeting(self.meeting, dt.date(2026, 11, 16), [1], runner)
        def failed(*args, **kwargs):
            yield {"type": "result", "race_num": 1}
            yield {"type": "error", "data": "DB failure after final scoring"}
        with self.assertRaises(RuntimeError):
            auto.update_meeting(self.meeting, dt.date(2026, 11, 16), [1], failed)

    def test_daytime_skips_network(self):
        with patch.object(auto, "discover_meetings") as discover:
            auto.run(dt.datetime(2026, 11, 15, 17, tzinfo=auto.JST))
            discover.assert_not_called()

    def test_cloud_never_falls_back_to_sqlite(self):
        with patch.dict("sys.modules", {"store": SimpleNamespace(BACKEND="sqlite")}), \
             patch.object(auto, "discover_meetings") as discover:
            with self.assertRaisesRegex(RuntimeError, "Supabase"):
                auto.run(run_now=True, require_cloud=True)
            discover.assert_not_called()

    def test_cloud_requires_credentials_before_network(self):
        with patch.dict("sys.modules", {"store": SimpleNamespace(BACKEND="supabase")}), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(auto, "discover_meetings") as discover:
            with self.assertRaisesRegex(RuntimeError, "認証"):
                auto.run(run_now=True, require_cloud=True)
            discover.assert_not_called()

    def test_run_now_preserves_skip_for_completed_meeting(self):
        with auto.run_lock():
            auto.write_state({self.meeting.program_id: {"races": [1]}})
        with patch.object(auto, "discover_meetings", return_value=[self.meeting]), \
             patch.object(auto, "discover_races", return_value=[1]), \
             patch.object(auto, "update_meeting") as update:
            self.assertEqual(auto.run(dt.datetime(2026, 11, 15, 12, tzinfo=auto.JST), run_now=True), 0)
            update.assert_not_called()

    def test_check_uses_jst_tomorrow_and_never_generates(self):
        with patch.object(auto, "discover_meetings", return_value=[self.meeting]) as discover, \
             patch.object(auto, "discover_races", return_value=[1]), \
             patch.object(auto, "update_meeting") as update:
            self.assertEqual(auto.run(dt.datetime(2026, 12, 31, 15, tzinfo=dt.timezone.utc), check_only=True), 0)
            discover.assert_called_once_with(dt.date(2027, 1, 2))
            update.assert_not_called()
            self.assertFalse(auto.STATE.exists())

    def test_completed_meeting_is_skipped(self):
        with auto.run_lock():
            auto.write_state({self.meeting.program_id: {"races": [1, 2]}})
        with patch.object(auto, "discover_meetings", return_value=[self.meeting]), \
             patch.object(auto, "discover_races", return_value=[1, 2]), \
             patch.object(auto, "update_meeting") as update:
            self.assertEqual(auto.run(dt.datetime(2026, 11, 15, 20, tzinfo=auto.JST)), 0)
            update.assert_not_called()

    def test_lock_prevents_duplicate_execution(self):
        with auto.run_lock() as first:
            self.assertTrue(first)
            with auto.run_lock() as second:
                self.assertFalse(second)

    def test_failed_save_is_retried_and_success_is_recorded(self):
        store = SimpleNamespace(init_db=lambda: None, BACKEND="sqlite")
        bot = SimpleNamespace(run_races_iter=lambda *args, **kwargs: iter([]))
        with patch.object(auto, "discover_meetings", return_value=[self.meeting]), \
             patch.object(auto, "discover_races", return_value=[1]), \
             patch.dict("sys.modules", {"keiba_bot": bot, "store": store}), \
             patch.object(auto, "update_meeting", side_effect=[RuntimeError("DB failed"), None]) as update:
            now = dt.datetime(2026, 11, 15, 20, tzinfo=auto.JST)
            with self.assertLogs("auto_update", level="ERROR"):
                self.assertEqual(auto.run(now), 1)
            self.assertFalse(auto.STATE.exists())
            self.assertEqual(auto.run(now), 0)
            self.assertEqual(update.call_count, 2)
            self.assertEqual(auto.read_state()[self.meeting.program_id]["races"], [1])
            self.assertEqual(auto.run(now), 0)
            self.assertEqual(update.call_count, 2)

    def test_force_refreshes_completed_meeting_outside_evening(self):
        with auto.run_lock():
            auto.write_state({self.meeting.program_id: {"races": [1]}})
        with patch.object(auto, "discover_meetings", return_value=[self.meeting]), \
             patch.object(auto, "discover_races", return_value=[1]), \
             patch.dict("sys.modules", {
                 "keiba_bot": SimpleNamespace(run_races_iter=None),
                 "store": SimpleNamespace(init_db=lambda: None, BACKEND="sqlite"),
             }), patch.object(auto, "update_meeting") as update:
            self.assertEqual(auto.run(dt.datetime(2026, 11, 15, 12, tzinfo=auto.JST), force=True), 0)
            update.assert_called_once()


class CloudStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for item in [patch.object(cloud_state, "STATE", Path(self.temp.name) / "state.json"),
                     patch.dict("os.environ", {"GH_TOKEN": "test-token", "GITHUB_REPOSITORY": "example/test", "GITHUB_SHA": "test-sha"})]:
            item.start()
            self.addCleanup(item.stop)
        self.session = Mock()
        self.session.headers = {}
        self.state = {"20261116201201": {"races": [1], "backend": "supabase", "completed_at": "2026-11-15"}}

    def response(self, code=200, data=None):
        return SimpleNamespace(status_code=code, json=lambda: data,
                               raise_for_status=Mock())

    def test_download_restores_persistent_state(self):
        payload = json.dumps(self.state).encode()
        self.session.get.return_value = self.response(data={"sha": "old", "content": base64.b64encode(payload).decode()})
        cloud_state.sync("download", self.session)
        self.assertEqual(json.loads(cloud_state.STATE.read_text()), self.state)
        self.session.put.assert_not_called()

    def test_first_run_without_state_is_read_only(self):
        self.session.get.return_value = self.response(404)
        cloud_state.sync("download", self.session)
        self.assertFalse(cloud_state.STATE.exists())
        self.session.post.assert_not_called()

    def test_upload_creates_branch_and_file(self):
        cloud_state.STATE.write_text(json.dumps(self.state))
        self.session.get.return_value = self.response(404)
        cloud_state.sync("upload", self.session)
        self.assertEqual(self.session.post.call_args.kwargs["json"]["ref"], "refs/heads/codex/auto-update-state")
        payload = self.session.put.call_args.kwargs["json"]
        self.assertEqual(json.loads(base64.b64decode(payload["content"])), self.state)
        self.assertNotIn("sha", payload)

    def test_unchanged_state_does_not_create_commit(self):
        content = json.dumps(self.state).encode()
        cloud_state.STATE.write_bytes(content)
        self.session.get.return_value = self.response(data={"sha": "old", "content": base64.b64encode(content).decode()})
        cloud_state.sync("upload", self.session)
        self.session.put.assert_not_called()

    def test_unexpected_sensitive_field_is_rejected(self):
        self.state["20261116201201"]["password"] = "must-never-upload"
        cloud_state.STATE.write_text(json.dumps(self.state))
        self.session.get.return_value = self.response(404)
        with self.assertRaises(ValueError):
            cloud_state.sync("upload", self.session)
        self.session.post.assert_not_called()
        self.session.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
