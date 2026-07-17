import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import keiba_bot as kb
import tools.update_nar_speed_reference as nar_updater


class NarSpeedReferenceUpdaterTests(unittest.TestCase):
    @staticmethod
    def _make_minimal_zip(path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("test_racelist.csv", "x")
            archive.writestr("test_horselist.csv", "x")

    def test_daily_refresh_failure_keeps_last_valid_daily_zip(self):
        def fake_download(url, target, refresh=False):
            if "type=daily" in url:
                raise OSError("temporary network failure")
            self._make_minimal_zip(target)
            return target, True

        with TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cached_daily = cache_dir / "daily" / "20260717_race.zip"
            self._make_minimal_zip(cached_daily)
            with patch.object(nar_updater, "download_archive", side_effect=fake_download):
                sources, warnings = nar_updater.prepare_archives(
                    date(2026, 7, 17), 1, cache_dir, workers=2,
                    today=date(2026, 7, 17),
                )
        daily = [source for source in sources if source.kind == "daily"]
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0].path, cached_daily)
        self.assertIn("直前キャッシュで継続", warnings[0])

    def test_partial_venue_snapshot_is_not_written(self):
        row = nar_updater.RaceClock(
            "浦和", "浦和", date(2026, 7, 1), 1, "ダ", "左", 1400,
            "良", "C2", "C2", 1, 1, 90.0,
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "reference.json"
            with patch.object(nar_updater, "prepare_archives", return_value=([], [])), \
                    patch.object(nar_updater, "load_race_clocks", return_value=([row], {})):
                with self.assertRaisesRegex(RuntimeError, "地方15場"):
                    nar_updater.update_reference(
                        date(2026, 7, 17), 5, root / "cache", output, offline=True,
                    )
            self.assertFalse(output.exists())


class TrainingComparisonTests(unittest.TestCase):
    def test_main_track_and_training_track_are_not_compared(self):
        result = kb._compute_prev_chokyo_comparison(
            ["川崎本馬場　一杯 39.3"],
            ["川崎調教場　馬なり 39.8"],
        )
        self.assertEqual(result["points"], 0)
        self.assertIsNone(result["delta"])


class RelativeTierConsistencyTests(unittest.TestCase):
    def test_equal_to_top_rescues_second_group_to_first_group(self):
        tiers, _ = kb._apply_matchup_tier_consistency(
            {"top": "S", "horse": "B"},
            {"horse": {"top": "="}},
            {"horse": {"top": "direct"}},
        )
        self.assertEqual(tiers["horse"], "A")

    def test_win_over_top_rescues_to_top(self):
        tiers, _ = kb._apply_matchup_tier_consistency(
            {"top": "S", "horse": "C"},
            {"horse": {"top": ">"}},
            {"horse": {"top": "mixed"}},
        )
        self.assertEqual(tiers["horse"], "S")

    def test_any_explicit_loss_to_top_blocks_rescue(self):
        tiers, _ = kb._apply_matchup_tier_consistency(
            {"top1": "S", "top2": "S", "horse": "B"},
            {"horse": {"top1": "=", "top2": "<"}},
            {"horse": {"top1": "direct", "top2": "bridge"}},
        )
        self.assertEqual(tiers["horse"], "B")


class CommonSpeedIndexTests(unittest.TestCase):
    def _meta_rows(self):
        rows = []
        # 5日分の通常基準。1400m=92.0秒、1500m=99.0秒。
        for day in range(1, 6):
            for suffix, dist, clock in (("01", 1400, 92.0), ("02", 1500, 99.0)):
                rows.append({
                    "race_id": f"2026060{day}210000{suffix}",
                    "date": f"2026.06.0{day}",
                    "place": "川崎", "going": "良", "dist": dist,
                    "winner_time": clock, "class_bucket": "C2", "title": "C2",
                })
        # 6/10は複数距離で一様に約1秒/1000m速い日。
        rows.extend([
            {"race_id": "2026061021000001", "date": "2026.06.10", "place": "川崎", "going": "良", "dist": 1400, "winner_time": 90.6, "class_bucket": "C2", "title": "C2"},
            {"race_id": "2026061021000002", "date": "2026.06.10", "place": "川崎", "going": "良", "dist": 1500, "winner_time": 97.5, "class_bucket": "C2", "title": "C2"},
            {"race_id": "2026061021000003", "date": "2026.06.10", "place": "川崎", "going": "良", "dist": 1400, "winner_time": 90.7, "class_bucket": "C2", "title": "C2"},
        ])
        return rows

    def test_winner_clock_parser(self):
        self.assertEqual(kb._winner_clock_to_seconds("549"), 54.9)
        self.assertEqual(kb._winner_clock_to_seconds("1324"), 92.4)
        self.assertEqual(kb._winner_clock_to_seconds("2163"), 136.3)

    def test_fullwidth_class_label_is_normalized(self):
        self.assertEqual(kb._speed_class_bucket("Ｃ２　三組"), "C2")
        self.assertEqual(kb._speed_class_bucket("３歳　未格付"), "3歳")

    def test_nar_official_course_scale_is_built(self):
        payload = {"courses": [{
            "place": "金沢", "surface": "ダ", "turn": "右", "distance": 1400,
            "average_time": 92.4, "record_time": 87.8, "samples": 1234, "races": 130,
            "confidence": "高", "reference_updated": "2026-07-15",
        }]}
        scales = kb._build_nar_speed_scales(payload)
        exact = scales[("金沢", "ダ", 1400, "右")]
        combined = scales[("金沢", "ダ", 1400, "")]
        self.assertEqual(exact["reference_provider"], "NAR公式CSV")
        self.assertEqual(combined["samples"], 1234)
        self.assertAlmostEqual(combined["average_time"], 92.4)

    def test_nar_official_snapshot_covers_all_15_venues(self):
        payload = kb._load_nar_speed_reference()
        scales = kb._build_nar_speed_scales(payload)
        self.assertEqual(payload["venue_count"], 15)
        self.assertGreaterEqual(payload["course_count"], 100)
        self.assertGreaterEqual(payload["race_count"], 70000)
        self.assertGreaterEqual(payload["finisher_count"], 700000)
        for key in (
            ("金沢", "ダ", 1400, ""),
            ("盛岡", "芝", 1000, ""),
            ("帯広", "ばんえい", 200, ""),
            ("大井", "ダ", 1200, ""),
        ):
            self.assertIn(key, scales)
            self.assertEqual(scales[key]["reference_provider"], "NAR公式CSV")

    def test_nar_daily_variant_respects_as_of(self):
        payload = {"daily_variants": [{
            "date": "2026.06.10", "place": "金沢", "surface": "ダ",
            "raw_per_1000": -1.1, "applied_per_1000": -0.7,
            "races": 9, "distances": [1300, 1400],
        }]}
        included, detail = kb._build_nar_daily_variants(payload, as_of=datetime(2026, 6, 11))
        self.assertEqual(included[("2026.06.10", "金沢", "ダ")], -0.7)
        self.assertEqual(detail[("2026.06.10", "金沢", "ダ")]["races"], 9)
        excluded, _ = kb._build_nar_daily_variants(payload, as_of=datetime(2026, 6, 10))
        self.assertNotIn(("2026.06.10", "金沢", "ダ"), excluded)

    def test_live_current_day_variant_is_exposed_without_changing_ability_axes(self):
        payload = {
            "as_of": "2026-07-17",
            "generated_at": "2026-07-17T06:00:00+00:00",  # 15:00 JST
            "courses": [{
                "place": "金沢", "surface": "ダ", "turn": "右", "distance": 1400,
                "average_time": 92.4, "record_time": 87.8, "samples": 1234,
            }],
            "daily_variants": [{
                "date": "2026-07-17", "place": "金沢", "surface": "ダ",
                "raw_per_1000": -0.6, "applied_per_1000": -0.2,
                "races": 4, "distances": [1300, 1400], "source": "NAR公式CSV",
            }],
        }
        horses = {"1": {"name": "当日確認馬", "hist": [{
            "time": 91.0, "dist": "1400", "place": "金沢", "date": "26.6.1",
        }]}}
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_nar_speed_reference", return_value=payload), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(
                horses, current_course="金沢", current_dist="1400",
                as_of=datetime(2026, 7, 17, 16, 0),
            )
        today = result["1"]["current_day_variant"]
        self.assertEqual(today["label"], "高速")
        self.assertEqual(today["races"], 4)
        self.assertAlmostEqual(today["applied_per_1000"], -0.2)
        self.assertEqual(result["1"]["index"], result["1"]["base_index"])

    def test_future_nar_snapshot_is_not_used_in_backtest(self):
        payload = {
            "as_of": "2026-07-17",
            "courses": [{
                "place": "金沢", "surface": "ダ", "turn": "右", "distance": 1400,
                "average_time": 92.4, "record_time": 87.8, "samples": 1234,
            }],
        }
        scales = kb._build_nar_speed_scales(payload, as_of=datetime(2026, 6, 20))
        self.assertEqual(scales, {})
        payload["daily_variants"] = [{
            "date": "2026.06.10", "place": "金沢", "surface": "ダ",
            "applied_per_1000": -0.7,
        }]
        variants, _ = kb._build_nar_daily_variants(payload, as_of=datetime(2026, 6, 20))
        self.assertEqual(variants, {})

    def test_same_day_nar_snapshot_uses_generation_time(self):
        payload = {
            "as_of": "2026-07-17",
            "generated_at": "2026-07-17T06:00:00+00:00",  # 15:00 JST
        }
        self.assertTrue(kb._nar_snapshot_is_future(payload, datetime(2026, 7, 17, 10, 0)))
        self.assertFalse(kb._nar_snapshot_is_future(payload, datetime(2026, 7, 17, 16, 0)))
        self.assertFalse(kb._nar_snapshot_is_future(payload, datetime(2026, 7, 18, 0, 0)))

    def test_date_only_jra_reference_is_rejected_on_same_day(self):
        reference_date = kb._speed_reference_date("2026/07/12")
        self.assertTrue(kb._reference_is_future(reference_date, datetime(2026, 7, 12, 10, 0)))
        self.assertFalse(kb._reference_is_future(reference_date, datetime(2026, 7, 13, 0, 0)))

    def test_future_horse_run_is_not_used_in_backtest(self):
        horses = {"1": {"name": "時系列馬", "hist": [
            {"time": 80.0, "dist": "1400", "place": "川崎", "date": "26.7.5"},
            {"time": 81.0, "dist": "1400", "place": "川崎", "date": "26.7.4"},
            {"time": 82.0, "dist": "1400", "place": "川崎", "date": "26.7.3"},
            {"time": 83.0, "dist": "1400", "place": "川崎", "date": "26.7.2"},
            {"time": 84.0, "dist": "1400", "place": "川崎", "date": "26.7.1"},
            # 日付しかない同日結果も、10時時点では未来情報として除外する。
            {"time": 85.0, "dist": "1400", "place": "川崎", "date": "26.6.20"},
            {"time": 91.0, "dist": "1400", "place": "川崎", "date": "26.6.1"},
        ]}}
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_nar_speed_reference", return_value={}), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20, 10, 0))
        self.assertEqual(result["1"]["runs"], 1)
        self.assertEqual(result["1"]["best_run"]["date"], "2026.06.01")

    def test_date_only_local_fallback_rows_are_excluded_on_as_of_day(self):
        meta = self._meta_rows() + [
            {"race_id": f"202606062100000{n}", "date": "2026.06.06", "place": "川崎",
             "going": "良", "dist": dist, "winner_time": clock,
             "class_bucket": "C2", "title": "C2"}
            for n, (dist, clock) in enumerate(((1400, 90.5), (1500, 97.4), (1600, 104.2)), 1)
        ]
        _pars, variants = kb._build_speed_reference(meta, as_of=datetime(2026, 6, 6, 10, 0))
        self.assertNotIn(("2026.06.06", "川崎"), variants)

    def test_current_horse_is_not_mixed_into_reference_scale(self):
        captured_names = []

        def capture_scales(rows, _variants, _as_of):
            captured_names.extend(row.get("horse_name") for row in rows)
            return {
                "exact_surface": {}, "exact_distance": {},
                "category_surface": {}, "category_distance": {},
                "jra_course_db": {}, "nar_official": {},
            }

        histories = [{
            "horse_name": "今回馬", "date": datetime(2026, 6, 1), "place": "川崎",
            "surface": "ダ", "dist": 1400, "time": 91.0,
        }, {
            "horse_name": "過去馬", "date": datetime(2026, 6, 1), "place": "川崎",
            "surface": "ダ", "dist": 1400, "time": 92.0,
        }]
        horses = {"1": {"name": "今回馬", "hist": [{
            "time": 91.0, "dist": "1400", "place": "川崎", "date": "26.6.1",
        }]}}
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_nar_speed_reference", return_value={}), \
                patch.object(kb, "_load_speed_history_rows", return_value=histories), \
                patch.object(kb, "_build_speed_scales", side_effect=capture_scales):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
        self.assertEqual(captured_names, ["過去馬"])
        self.assertIsNotNone(result["1"]["index"])

    def test_fast_day_variant_is_negative(self):
        _pars, variants = kb._build_speed_reference(self._meta_rows())
        # 過補正防止の10%縮小後でも、高速日として負方向に残ること。
        self.assertLess(variants[("2026.06.10", "川崎")], -0.08)

    def test_fast_day_clock_is_normalized_down(self):
        horses = {
            "1": {"hist": [{"time": 91.0, "dist": "1400", "place": "川崎", "date": "26.6.10", "url": "https://www.nankankeiba.com/result/2026061021000001.do"}]},
            "2": {"hist": [{"time": 91.0, "dist": "1400", "place": "川崎", "date": "26.6.5", "url": "https://www.nankankeiba.com/result/2026060521000001.do"}]},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=self._meta_rows()), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
        self.assertLess(result["1"]["index"], result["2"]["index"])
        self.assertEqual(result["1"]["best_run"]["variant_label"], "高速")

    def test_race_relative_100_index_is_not_calculated(self):
        horses = {
            "1": {"hist": [{"time": 90.0, "dist": "1400", "place": "川崎", "date": "26.6.5", "url": "https://www.nankankeiba.com/result/2026060521000001.do"}]},
            "2": {"hist": [{"time": 91.4, "dist": "1400", "place": "川崎", "date": "26.6.5", "url": "https://www.nankankeiba.com/result/2026060521000001.do"}]},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=self._meta_rows()), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
        self.assertNotIn("race_index", result["1"])
        self.assertNotIn("race_index", result["2"])
        self.assertGreater(result["1"]["index"], result["2"]["index"])

    def test_jra_transfer_is_indexed_without_daily_variant(self):
        horses = {
            "1": {"hist": [{"time": 70.0, "dist": "1200", "place": "JRA", "date": "26.6.5", "url": ""}]},
            "2": {"hist": [{"time": 72.0, "dist": "1200", "place": "JRA", "date": "26.6.5", "url": ""}]},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
        self.assertGreater(result["1"]["index"], result["2"]["index"])
        self.assertEqual(result["1"]["best_run"]["variant_per_1000"], 0.0)
        self.assertTrue(result["1"]["best_run"]["is_transfer"])

    def test_jra_marked_exchange_horse_gets_level_bonus(self):
        shared_run = {
            "time": 91.0, "dist": "1400", "place": "川崎", "date": "26.6.5", "url": "",
        }
        horses = {
            "1": {"name": "[J]中央馬", "hist": [dict(shared_run)]},
            "2": {"name": "地方馬", "hist": [dict(shared_run)]},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
        self.assertTrue(kb._is_jra_exchange_race(horses))
        self.assertAlmostEqual(result["1"]["index"] - result["2"]["index"], 14.0, places=1)
        self.assertAlmostEqual(result["1"]["highest_index"] - result["2"]["highest_index"], 14.0, places=1)
        self.assertEqual(result["1"]["jra_exchange_bonus"], 14.0)

    def test_course_db_all_102_courses_are_available(self):
        payload = kb._load_jra_course_db_reference()
        scales = kb._build_jra_course_db_scales(payload)
        self.assertEqual(payload["course_count"], 102)
        tokyo = scales[("東京", "ダ", 1400, "")]
        self.assertGreaterEqual(tokyo["samples"], 400)
        self.assertEqual(tokyo["reference_provider"], "course-db.com")
        self.assertIn("過去5年", tokyo["source"])

    def test_course_db_reference_overrides_sparse_jra_history(self):
        sparse = {
            "exact_surface": {("東京", "ダ", 1400): {
                "average_time": 99.0, "record_time": 95.0, "samples": 1,
            }},
            "exact_distance": {}, "category_surface": {}, "category_distance": {},
            "jra_course_db": kb._build_jra_course_db_scales(),
        }
        scale = kb._speed_scale_for_run("東京", "ダ", 1400, sparse)
        self.assertNotEqual(scale["average_time"], 99.0)
        self.assertEqual(scale["reference_provider"], "course-db.com")

    def test_average_70_record_grade_100_scale(self):
        scale = kb._speed_record_scale([70.0, 72.0, 74.0, 76.0], 1200)
        self.assertEqual(scale["average_time"], 73.0)
        self.assertEqual(scale["record_time"], 70.0)
        average_index = 70.0 + 30.0 * (73.0 - 73.0) / (73.0 - 70.0)
        record_index = 70.0 + 30.0 * (73.0 - 70.0) / (73.0 - 70.0)
        self.assertEqual(average_index, 70.0)
        self.assertEqual(record_index, 100.0)

    def test_same_course_distance_index_is_exposed(self):
        horses = {
            "1": {"hist": [
                {"time": 91.0, "dist": "1400", "place": "川崎", "date": "26.6.5", "url": ""},
                {"time": 92.0, "dist": "1500", "place": "川崎", "date": "26.5.5", "url": ""},
            ]},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(
                horses, current_course="川崎", current_dist="1400", as_of=datetime(2026, 6, 20)
            )
        self.assertIsNotNone(result["1"]["course_index"])
        self.assertEqual(result["1"]["course_runs"], 1)
        self.assertIsNotNone(result["1"]["highest_index"])

    def test_current_draw_side_changes_main_index(self):
        histories = [
            {"time": 88.0, "dist": "1400", "place": "川崎", "date": "26.6.5", "field_size": 8, "gate_no": 1},
            {"time": 89.0, "dist": "1400", "place": "川崎", "date": "26.5.5", "field_size": 8, "gate_no": 2},
            {"time": 94.0, "dist": "1400", "place": "川崎", "date": "26.4.5", "field_size": 8, "gate_no": 7},
            {"time": 95.0, "dist": "1400", "place": "川崎", "date": "26.3.5", "field_size": 8, "gate_no": 8},
        ]
        horses = {
            "1": {"name": "内枠馬", "hist": histories},
            "8": {"name": "外枠馬", "hist": histories},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
            urawa_result = kb.calculate_common_speed_indices(
                horses, current_course="浦和", current_dist="1400", as_of=datetime(2026, 6, 20)
            )
        self.assertEqual(result["1"]["current_draw_side"], "内")
        self.assertEqual(result["8"]["current_draw_side"], "外")
        self.assertGreater(result["1"]["index"], result["8"]["index"])
        self.assertGreater(result["1"]["inside_index"], result["1"]["outside_index"])
        # 浦和は2日20競走の検証値に従い、枠側差を50%に抑えて過適合を避ける。
        self.assertLess(
            urawa_result["1"]["index"] - urawa_result["8"]["index"],
            result["1"]["index"] - result["8"]["index"],
        )

    def test_speed_table_displays_three_index_types(self):
        horses = {"1": {"name": "テストホース"}}
        scored = {"1": {
            "common_speed_index": 82.5,
            "common_speed_highest_index": 90.1,
            "common_speed_course_index": 86.1,
            "common_speed_course_runs": 2,
            "common_speed_inside_index": 82.5,
            "common_speed_outside_index": 79.4,
            "common_speed_inside_runs": 3,
            "common_speed_outside_runs": 2,
            "common_speed_draw_side": "内",
            "common_speed_rank": 1,
            "common_speed_runs": 5,
            "common_speed_best_run": {},
        }}
        table = kb.build_speed_index_table(horses, scored)
        self.assertIn("<th>指数</th>", table)
        self.assertIn("<th>最高指数</th>", table)
        self.assertIn("<th>コース指数</th>", table)
        self.assertIn("今回内側", table)
        self.assertNotIn("内82.5/外79.4", table)
        self.assertEqual(table.count('class="speed-top3"'), 2)
        self.assertNotIn("今の指数", table)
        self.assertNotIn("<th>レース内</th>", table)
        self.assertNotIn("100.0", table)

    def test_highest_and_course_top3_are_bold_independently(self):
        horses = {str(i): {"name": f"馬{i}"} for i in range(1, 5)}
        highest = [91.0, 90.0, 89.0, 88.0]
        course = [70.0, None, 90.0, 80.0]
        scored = {}
        for i in range(1, 5):
            scored[str(i)] = {
                "common_speed_index": 85.0 - i,
                "common_speed_highest_index": highest[i - 1],
                "common_speed_course_index": course[i - 1],
                "common_speed_course_runs": 1 if course[i - 1] is not None else 0,
                "common_speed_draw_side": "内" if i <= 2 else "外",
                "common_speed_rank": i,
                "common_speed_runs": 5,
                "common_speed_best_run": {},
            }
        table = kb.build_speed_index_table(horses, scored)
        # 最高指数3頭＋コース指数が存在する3頭を、それぞれ独立して太字にする。
        self.assertEqual(table.count('class="speed-top3"'), 6)


class JraExchangeScoringTests(unittest.TestCase):
    def test_exchange_race_removes_jockey_p_and_jockey_fallback(self):
        horses = {
            "1": {
                "name": "[J]中央馬", "jockey": "中央騎手", "prev_jockey": "前騎手",
                "display_power": "【騎手】P:15(勝30.0%/50.0%)(前P:5)、 相性:勝40.0%(4/10)",
                "hist": [],
            },
            "2": {
                "name": "地方馬", "jockey": "地方騎手", "prev_jockey": "前騎手",
                "display_power": "【騎手】P:10(勝15.0%/30.0%)(前P:5)、 相性:勝20.0%(2/10)",
                "hist": [],
            },
        }
        relative_result = (
            {"1": 20, "2": 20}, {"1": None, "2": None}, "相対なし", [], {},
        )
        with patch.object(kb, "calculate_relative_scores", return_value=relative_result), \
                patch.object(kb, "calculate_common_speed_indices", return_value={}):
            scored, _html, _locks, _verdicts = kb.calculate_horse_scores(
                horses, {}, "川崎", "1400", False, "C2", 1, pace_speed_list=[], is_newcomer=False,
            )
        for info in scored.values():
            self.assertEqual(info["score_2"], 0)
            self.assertFalse(info["jockey_p_enabled"])
            self.assertEqual(info["score_5"], 35)
            self.assertEqual(info["no_relative_grade"], "相対不明(交流中立)")

    def test_exchange_race_ignores_jockey_p_grade_downgrade(self):
        horses = {"1": {"name": "[J]中央馬"}}
        scored = {"1": {
            "jockey_current": "新騎手", "jockey_prev": "前騎手",
            "jockey_p_base": 3, "jockey_p_prev": 12,
            "jockey_p_enabled": False,
        }}
        grades = {kb._norm_horse_name("[J]中央馬"): "S"}
        result = kb.apply_display_grade_overrides(grades, horses, scored)
        self.assertEqual(result[kb._norm_horse_name("[J]中央馬")], "S")

    def test_normal_race_keeps_jockey_p_grade_downgrade(self):
        horses = {"1": {"name": "地方馬"}}
        scored = {"1": {
            "jockey_current": "新騎手", "jockey_prev": "前騎手",
            "jockey_p_base": 3, "jockey_p_prev": 12,
            "jockey_p_enabled": True,
        }}
        grades = {kb._norm_horse_name("地方馬"): "S"}
        result = kb.apply_display_grade_overrides(grades, horses, scored)
        self.assertEqual(result[kb._norm_horse_name("地方馬")], "A")


class RelativeTierConsistencyAdditionalTests(unittest.TestCase):
    def test_missing_comparison_does_not_count_as_unbeaten(self):
        tiers, _ = kb._apply_matchup_tier_consistency(
            {"top": "S", "horse": "B"},
            {"horse": {}},
            {"horse": {}},
        )
        self.assertEqual(tiers["horse"], "B")

    def test_win_over_first_group_rescues_second_group(self):
        tiers, _ = kb._apply_matchup_tier_consistency(
            {"top": "S", "first": "A", "horse": "B"},
            {"horse": {"first": ">"}},
            {"horse": {"first": "direct"}},
        )
        self.assertEqual(tiers["horse"], "A")
        self.assertEqual(kb._rel_label_to_score(tiers["horse"], 3), 40)

    def test_existing_relative_point_table_is_unchanged(self):
        self.assertEqual(
            {tier: kb._rel_label_to_score(tier, 3) for tier in ("S", "A", "B", "C")},
            {"S": 50, "A": 40, "B": 25, "C": 15},
        )

    def test_mixed_results_against_first_group_stay_second_group(self):
        tiers, _ = kb._apply_matchup_tier_consistency(
            {"first1": "A", "first2": "A", "horse": "B"},
            {"horse": {"first1": ">", "first2": "<"}},
            {"horse": {"first1": "direct", "first2": "direct"}},
        )
        self.assertEqual(tiers["horse"], "B")

    def test_third_group_win_over_second_group_rescues_to_second(self):
        tiers, _ = kb._apply_matchup_tier_consistency(
            {"second": "B", "horse": "C"},
            {"horse": {"second": ">"}},
            {"horse": {"second": "bridge"}},
        )
        self.assertEqual(tiers["horse"], "B")


if __name__ == "__main__":
    unittest.main()
