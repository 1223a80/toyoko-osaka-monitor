import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import requests

import toyoko_monitor as monitor


def raw_result(available=False, price=0, maintenance=False):
    return {
        "existEnoughVacantRooms": available,
        "lowestPrice": price,
        "isUnderMaintenance": maintenance,
    }


class ToyokoMonitorTests(unittest.TestCase):
    def test_hotel_inventory_is_exactly_15_user_approved_unique_codes(self):
        codes = monitor.all_hotel_codes()
        self.assertEqual(15, len(codes))
        self.assertEqual(15, len(set(codes)))
        self.assertEqual(set(codes), set(monitor.HOTEL_NAMES))
        self.assertEqual(set(codes), set(monitor.HOTEL_PRIORITY))
        self.assertEqual([5, 5, 5], [len(batch) for batch in monitor.HOTEL_BATCHES])
        self.assertIn("00039", codes)  # Senba East was explicitly restored.
        self.assertLess(
            monitor.HOTEL_PRIORITY.index("00039"),
            monitor.HOTEL_PRIORITY.index("00131"),
        )
        self.assertNotIn("00351", codes)  # Namba tourist area is excluded.

    def test_build_input_preserves_japan_midnight_utc_serialization(self):
        payload = monitor.build_input(["00351"], "smoking")["0"]
        self.assertEqual("2026-10-29T16:00:00.000Z", payload["json"]["checkinDate"])
        self.assertEqual("2026-10-31T16:00:00.000Z", payload["json"]["checkoutDate"])
        self.assertEqual("smoking", payload["json"]["smokingType"])
        self.assertEqual(["Date"], payload["meta"]["values"]["checkinDate"])

    def test_maintenance_is_unknown_not_unavailable(self):
        result = monitor.parse_hotel_result("00351", raw_result(maintenance=True))
        self.assertEqual(monitor.Status.UNKNOWN, result.status)

    def test_missing_field_is_unknown(self):
        result = monitor.parse_hotel_result("00351", {"lowestPrice": 0})
        self.assertEqual(monitor.Status.UNKNOWN, result.status)

    def test_available_with_invalid_price_is_unknown_not_silently_over_budget(self):
        for invalid_price in (0, -1, float("nan"), float("inf")):
            with self.subTest(price=invalid_price):
                result = monitor.parse_hotel_result(
                    "00266", raw_result(available=True, price=invalid_price)
                )
                self.assertEqual(monitor.Status.UNKNOWN, result.status)
                self.assertIn("价格无效", result.reason or "")

    def test_fetch_batch_http_error_is_unknown_for_every_hotel(self):
        response = Mock(status_code=403)
        error = requests.HTTPError(response=response)
        session = Mock()
        session.get.return_value.raise_for_status.side_effect = error
        results = monitor.fetch_batch(session, ["00351", "00349"], "all")
        self.assertTrue(all(item.status is monitor.Status.UNKNOWN for item in results.values()))
        self.assertTrue(all("HTTP 403" in (item.reason or "") for item in results.values()))

    def test_candidate_must_survive_second_all_query(self):
        initial = {
            "00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 10000, False)
        }
        smoking = {"00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 10000, False)}
        unavailable = {"00266": monitor.HotelResult("00266", monitor.Status.UNAVAILABLE, 0, False)}
        with patch.object(monitor, "HOTEL_BATCHES", [["00266"]]), patch.object(
            monitor, "fetch_batch", side_effect=[smoking, unavailable, smoking]
        ):
            final, _ = monitor.confirm_candidates(Mock(), initial, 0)
        self.assertEqual(monitor.Status.UNAVAILABLE, final["00266"].status)

    def test_candidate_price_rising_over_cap_is_confirmed_but_not_notifiable(self):
        initial = {
            "00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 16000, False)
        }
        smoking = {
            "00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 16000, False)
        }
        over_budget = {
            "00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 16001, False)
        }
        with patch.object(monitor, "HOTEL_BATCHES", [["00266"]]), patch.object(
            monitor, "fetch_batch", side_effect=[smoking, over_budget, smoking]
        ):
            final, _ = monitor.confirm_candidates(Mock(), initial, 0)

        self.assertEqual(monitor.Status.AVAILABLE, final["00266"].status)
        self.assertEqual([], monitor.notifiable_results_in_priority_order(final))
        self.assertEqual(
            ["00266"],
            [result.code for result in monitor.over_budget_results_in_priority_order(final)],
        )

    def test_smoking_label_requires_two_positive_checks(self):
        initial = {
            "00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 10000, False)
        }
        available = {"00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 10000, False)}
        with patch.object(monitor, "HOTEL_BATCHES", [["00266"]]), patch.object(
            monitor, "fetch_batch", side_effect=[available, available, available]
        ):
            final, smoking = monitor.confirm_candidates(Mock(), initial, 0)
        self.assertEqual(monitor.Status.AVAILABLE, final["00266"].status)
        self.assertEqual(monitor.Status.AVAILABLE, smoking["00266"].status)

    def test_exact_price_cap_is_notifiable_but_one_yen_over_is_not(self):
        at_cap = monitor.HotelResult(
            "00266", monitor.Status.AVAILABLE, monitor.MAX_TOTAL_PRICE, False
        )
        over_cap = monitor.HotelResult(
            "00047", monitor.Status.AVAILABLE, monitor.MAX_TOTAL_PRICE + 1, False
        )
        self.assertTrue(monitor.is_within_budget(at_cap))
        self.assertFalse(monitor.is_within_budget(over_cap))

        results = {"00266": at_cap, "00047": over_cap}
        self.assertEqual(
            ["00266"],
            [item.code for item in monitor.notifiable_results_in_priority_order(results)],
        )
        self.assertEqual(
            ["00047"],
            [item.code for item in monitor.over_budget_results_in_priority_order(results)],
        )

    def test_over_budget_candidate_does_not_trigger_confirmation_requests(self):
        initial = {
            "00266": monitor.HotelResult(
                "00266", monitor.Status.AVAILABLE, monitor.MAX_TOTAL_PRICE + 1, False
            )
        }
        with patch.object(monitor, "fetch_batch") as fetch_batch:
            final, smoking = monitor.confirm_candidates(Mock(), initial, 0)
        self.assertEqual(initial, final)
        self.assertEqual({}, smoking)
        fetch_batch.assert_not_called()

    def test_monitor_stops_at_japan_checkin_midnight(self):
        before = datetime(2026, 10, 29, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
        at_stop = datetime(2026, 10, 30, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.assertFalse(monitor.monitor_has_ended(before))
        self.assertTrue(monitor.monitor_has_ended(at_stop))

    def test_quiet_hours_use_actual_china_execution_time(self):
        zone = ZoneInfo("Asia/Shanghai")
        self.assertTrue(monitor.is_quiet_hours(datetime(2026, 8, 31, 0, 44, tzinfo=zone)))
        self.assertTrue(monitor.is_quiet_hours(datetime(2026, 8, 31, 5, 59, tzinfo=zone)))
        self.assertFalse(monitor.is_quiet_hours(datetime(2026, 8, 31, 6, 0, tzinfo=zone)))
        self.assertFalse(monitor.is_quiet_hours(datetime(2026, 8, 31, 23, 59, tzinfo=zone)))

    def test_step_summary_is_written_when_path_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(path)}):
                monitor.append_step_summary("hello\n")
            self.assertEqual("hello\n", path.read_text(encoding="utf-8"))

    def test_preferred_honmachi_2_is_highlighted_before_other_vacancies(self):
        results = {
            "00034": monitor.HotelResult("00034", monitor.Status.AVAILABLE, 15500, False),
            "00266": monitor.HotelResult("00266", monitor.Status.AVAILABLE, 16000, False),
        }
        smoking = {
            code: monitor.HotelResult(code, monitor.Status.AVAILABLE, result.lowest_price, False)
            for code, result in results.items()
        }

        ordered = monitor.available_results_in_priority_order(results)
        self.assertEqual(["00266", "00034"], [result.code for result in ordered])

        alert = monitor.build_alert_message(
            results,
            smoking,
            datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertIn("🌟 朋友同店·最高优先", alert)
        self.assertIn("大阪本町2号店有房，和朋友住同一家最方便", alert)
        self.assertLess(alert.index("大阪本町2号店"), alert.index("大阪梅田中津1号店"))

    def test_other_vacancies_still_notify_when_preferred_hotel_is_full(self):
        results = {
            "00266": monitor.HotelResult("00266", monitor.Status.UNAVAILABLE, 0, False),
            "00034": monitor.HotelResult("00034", monitor.Status.AVAILABLE, 15000, False),
        }
        alert = monitor.build_alert_message(
            results,
            {},
            datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertNotIn("朋友同店·最高优先", alert)
        self.assertIn("大阪梅田中津1号店", alert)

    def test_over_budget_vacancy_is_excluded_from_alert(self):
        results = {
            "00266": monitor.HotelResult("00266", monitor.Status.UNAVAILABLE, 0, False),
            "00047": monitor.HotelResult("00047", monitor.Status.AVAILABLE, 16001, False),
        }
        alert = monitor.build_alert_message(
            results,
            {},
            datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertNotIn("大阪本町1号店", alert)


if __name__ == "__main__":
    unittest.main()
