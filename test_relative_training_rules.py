import unittest
from datetime import datetime
from unittest.mock import patch

import keiba_bot as kb


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

    def test_race_fastest_index_is_always_100(self):
        horses = {
            "1": {"hist": [{"time": 90.0, "dist": "1400", "place": "川崎", "date": "26.6.5", "url": "https://www.nankankeiba.com/result/2026060521000001.do"}]},
            "2": {"hist": [{"time": 91.4, "dist": "1400", "place": "川崎", "date": "26.6.5", "url": "https://www.nankankeiba.com/result/2026060521000001.do"}]},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=self._meta_rows()), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
        self.assertEqual(max(info["race_index"] for info in result.values()), 100.0)
        self.assertAlmostEqual(
            result["1"]["race_index"] - result["2"]["race_index"],
            result["1"]["index"] - result["2"]["index"],
        )

    def test_jra_transfer_is_indexed_without_daily_variant(self):
        horses = {
            "1": {"hist": [{"time": 70.0, "dist": "1200", "place": "JRA", "date": "26.6.5", "url": ""}]},
            "2": {"hist": [{"time": 72.0, "dist": "1200", "place": "JRA", "date": "26.6.5", "url": ""}]},
        }
        with patch.object(kb, "_load_speed_race_meta", return_value=[]), \
                patch.object(kb, "_load_speed_history_rows", return_value=[]):
            result = kb.calculate_common_speed_indices(horses, as_of=datetime(2026, 6, 20))
        self.assertEqual(result["1"]["race_index"], 100.0)
        self.assertLess(result["2"]["race_index"], 100.0)
        self.assertEqual(result["1"]["best_run"]["variant_per_1000"], 0.0)
        self.assertTrue(result["1"]["best_run"]["is_transfer"])

    def test_average_70_record_grade_100_scale(self):
        scale = kb._speed_record_scale([70.0, 72.0, 74.0, 76.0], 1200)
        self.assertEqual(scale["average_time"], 73.0)
        self.assertEqual(scale["record_time"], 70.0)
        average_index = 70.0 + 30.0 * (73.0 - 73.0) / (73.0 - 70.0)
        record_index = 70.0 + 30.0 * (73.0 - 70.0) / (73.0 - 70.0)
        self.assertEqual(average_index, 70.0)
        self.assertEqual(record_index, 100.0)

    def test_same_course_distance_max_is_exposed(self):
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
        self.assertIsNotNone(result["1"]["same_condition_max"])
        self.assertEqual(result["1"]["same_condition_runs"], 1)


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
