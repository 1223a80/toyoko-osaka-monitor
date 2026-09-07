import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import lambda_handler as aws_monitor
import toyoko_monitor as monitor


def full_inventory(status=monitor.Status.UNAVAILABLE):
    return {
        code: monitor.HotelResult(code, status, 0, False)
        for code in monitor.all_hotel_codes()
    }


class LambdaHandlerTests(unittest.TestCase):
    @patch.object(aws_monitor, "datetime")
    @patch.object(aws_monitor, "publish_notification")
    @patch.object(monitor, "confirm_candidates")
    @patch.object(monitor, "fetch_all")
    @patch.object(monitor, "build_session")
    def test_full_inventory_only_logs(
        self, build_session, fetch_all, confirm_candidates, publish, mocked_datetime
    ):
        now = datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        mocked_datetime.now.return_value = now
        inventory = full_inventory()
        fetch_all.return_value = inventory
        confirm_candidates.return_value = (inventory, {})

        result = aws_monitor.lambda_handler({}, Mock())

        self.assertEqual("full", result["status"])
        self.assertFalse(result["notified"])
        publish.assert_not_called()

    @patch.object(aws_monitor, "datetime")
    @patch.object(aws_monitor, "publish_notification")
    @patch.object(monitor, "confirm_candidates")
    @patch.object(monitor, "fetch_all")
    @patch.object(monitor, "build_session")
    def test_vacancy_sends_email_and_prioritizes_honmachi_2(
        self, build_session, fetch_all, confirm_candidates, publish, mocked_datetime
    ):
        now = datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        mocked_datetime.now.return_value = now
        inventory = full_inventory()
        inventory["00266"] = monitor.HotelResult(
            "00266", monitor.Status.AVAILABLE, 16000, False
        )
        fetch_all.return_value = inventory
        confirm_candidates.return_value = (inventory, {})

        result = aws_monitor.lambda_handler({}, Mock())

        self.assertEqual("available", result["status"])
        self.assertTrue(result["notified"])
        subject, body = publish.call_args.args
        self.assertIn("本町2号店", subject)
        self.assertIn("朋友同店·最高优先", body)

    @patch.object(aws_monitor, "datetime")
    @patch.object(aws_monitor, "publish_notification")
    @patch.object(monitor, "confirm_candidates")
    @patch.object(monitor, "fetch_all")
    @patch.object(monitor, "build_session")
    def test_over_budget_vacancy_only_logs_without_email(
        self, build_session, fetch_all, confirm_candidates, publish, mocked_datetime
    ):
        now = datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        mocked_datetime.now.return_value = now
        inventory = full_inventory()
        inventory["00266"] = monitor.HotelResult(
            "00266", monitor.Status.AVAILABLE, 16001, False
        )
        fetch_all.return_value = inventory
        confirm_candidates.return_value = (inventory, {})

        result = aws_monitor.lambda_handler({}, Mock())

        self.assertEqual("over_budget", result["status"])
        self.assertEqual(1, result["over_budget"])
        self.assertFalse(result["notified"])
        publish.assert_not_called()

    @patch.object(aws_monitor, "datetime")
    @patch.object(aws_monitor, "publish_notification")
    @patch.object(monitor, "confirm_candidates")
    @patch.object(monitor, "fetch_all")
    @patch.object(monitor, "build_session")
    def test_unknown_sends_failure_email_without_raising_for_retry(
        self, build_session, fetch_all, confirm_candidates, publish, mocked_datetime
    ):
        now = datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        mocked_datetime.now.return_value = now
        inventory = full_inventory()
        inventory["00071"] = monitor.HotelResult(
            "00071", monitor.Status.UNKNOWN, reason="API 请求超时"
        )
        fetch_all.return_value = inventory
        confirm_candidates.return_value = (inventory, {})

        result = aws_monitor.lambda_handler({}, Mock())

        self.assertEqual("unknown", result["status"])
        self.assertTrue(result["notified"])
        self.assertIn("查询失败", publish.call_args.args[0])

    @patch.object(aws_monitor, "datetime")
    @patch.object(aws_monitor, "publish_notification")
    def test_stop_date_makes_no_request_or_notification(self, publish, mocked_datetime):
        mocked_datetime.now.return_value = datetime(
            2026, 10, 30, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo")
        )
        with patch.object(monitor, "build_session") as build_session:
            result = aws_monitor.lambda_handler({}, Mock())

        self.assertEqual("ended", result["status"])
        build_session.assert_not_called()
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
