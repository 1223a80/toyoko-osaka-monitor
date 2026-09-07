"""AWS Lambda entry point for the Toyoko Inn Osaka monitor."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import toyoko_monitor as monitor


def publish_notification(subject: str, message: str) -> None:
    topic_arn = os.environ.get("SNS_TOPIC_ARN")
    if not topic_arn:
        raise RuntimeError("SNS_TOPIC_ARN is not configured")

    # boto3 is provided by the managed AWS Lambda Python runtime.
    import boto3

    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Subject=subject,
        Message=message,
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del event, context

    now_japan = datetime.now(ZoneInfo("Asia/Tokyo"))
    now_shanghai = now_japan.astimezone(ZoneInfo("Asia/Shanghai"))
    if monitor.monitor_has_ended(now_japan):
        print("Monitoring period has ended; no Toyoko Inn request was sent.")
        return {"status": "ended", "notified": False}

    session = monitor.build_session()
    initial = monitor.fetch_all(session, "all")
    results, smoking_results = monitor.confirm_candidates(
        session,
        initial,
        monitor.DEFAULT_CONFIRM_DELAY_SECONDS,
    )
    monitor.print_results(results, smoking_results)

    available = monitor.notifiable_results_in_priority_order(results)
    over_budget = monitor.over_budget_results_in_priority_order(results)
    unknown = [
        result for result in results.values() if result.status is monitor.Status.UNKNOWN
    ]
    notified = bool(available or unknown)
    if notified:
        if available and unknown:
            subject = "东横INN大阪：发现空房，同时有查询异常"
        elif available:
            preferred_available = any(
                result.code == monitor.PREFERRED_HOTEL_CODE for result in available
            )
            subject = (
                "东横INN大阪：本町2号店有空房"
                if preferred_available
                else f"东横INN大阪：{len(available)}家有空房"
            )
        else:
            subject = "东横INN大阪：查询失败"

        message = monitor.build_alert_message(results, smoking_results, now_shanghai)
        publish_notification(subject, message)
        print(f"SNS notification published: {subject}")
    elif over_budget:
        print(
            "Only over-budget vacancies were found "
            f"(limit: {monitor.format_price(monitor.MAX_TOTAL_PRICE)}); no email was sent."
        )
    else:
        print(
            f"All {len(monitor.all_hotel_codes())} monitored hotels are unavailable; "
            "no email was sent."
        )

    payload = {
        "status": (
            "unknown"
            if unknown
            else ("available" if available else ("over_budget" if over_budget else "full"))
        ),
        "available": len(available),
        "over_budget": len(over_budget),
        "unknown": len(unknown),
        "notified": notified,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return payload
