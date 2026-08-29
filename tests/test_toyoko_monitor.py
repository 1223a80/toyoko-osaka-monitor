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
    def test_hotel_inventory_is_exactly_33_unique_codes(self):
        codes = monitor.all_hotel_codes()
        self.assertEqual(33, len(codes))
        self.assertEqual(33, len(set(codes)))
        self.assertEqual(set(codes), set(monitor.HOTEL_NAMES))
        self.assertEqual([10, 10, 10, 3], [len(batch) for batch in monitor.HOTEL_BATCHES])

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
            "00351": monitor.HotelResult("00351", monitor.Status.AVAILABLE, 10000, False)
        }
        smoking = {"00351": monitor.HotelResult("00351", monitor.Status.AVAILABLE, 10000, False)}
        unavailable = {"00351": monitor.HotelResult("00351", monitor.Status.UNAVAILABLE, 0, False)}
        with patch.object(monitor, "HOTEL_BATCHES", [["00351"]]), patch.object(
            monitor, "fetch_batch", side_effect=[smoking, unavailable, smoking]
        ):
            final, _ = monitor.confirm_candidates(Mock(), initial, 0)
        self.assertEqual(monitor.Status.UNAVAILABLE, final["00351"].status)

    def test_smoking_label_requires_two_positive_checks(self):
        initial = {
            "00351": monitor.HotelResult("00351", monitor.Status.AVAILABLE, 10000, False)
        }
        available = {"00351": monitor.HotelResult("00351", monitor.Status.AVAILABLE, 10000, False)}
        with patch.object(monitor, "HOTEL_BATCHES", [["00351"]]), patch.object(
            monitor, "fetch_batch", side_effect=[available, available, available]
        ):
            final, smoking = monitor.confirm_candidates(Mock(), initial, 0)
        self.assertEqual(monitor.Status.AVAILABLE, final["00351"].status)
        self.assertEqual(monitor.Status.AVAILABLE, smoking["00351"].status)

    def test_monitor_stops_at_japan_checkin_midnight(self):
        before = datetime(2026, 10, 29, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
        at_stop = datetime(2026, 10, 30, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.assertFalse(monitor.monitor_has_ended(before))
        self.assertTrue(monitor.monitor_has_ended(at_stop))

    def test_step_summary_is_written_when_path_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(path)}):
                monitor.append_step_summary("hello\n")
            self.assertEqual("hello\n", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
