#!/usr/bin/env python3
"""Read-only Toyoko Inn Osaka availability monitor for GitHub Actions."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_URL = "https://www.toyoko-inn.com/api/trpc/hotels.availabilities.prices"
BOOKING_URL = (
    "https://www.toyoko-inn.com/search/result/?prefecture=27-all&people=1&room=1"
    "&smoking=all&start=2026-10-30&end=2026-11-01&r_avail_only=true"
)
CHECKIN_DATE = "2026-10-30"
CHECKOUT_DATE = "2026-11-01"
CHECKIN_UTC = "2026-10-29T16:00:00.000Z"
CHECKOUT_UTC = "2026-10-31T16:00:00.000Z"
PEOPLE = 1
ROOMS = 1
REQUEST_TIMEOUT = (8, 25)
DEFAULT_CONFIRM_DELAY_SECONDS = 8.0
ISSUE_TITLE = "东横 INN 大阪空房监控通知（请订阅）"
PREFERRED_HOTEL_CODE = "00266"
PREFERRED_HOTEL_LABEL = "🌟 朋友同店·最高优先"

# Order and 10 + 10 + 10 + 3 batching are taken from the supplied Safari HAR.
HOTEL_BATCHES = [
    ["00366", "00071", "00363", "00237", "00091", "00016", "00200", "00240", "00266", "00033"],
    ["00129", "00287", "00298", "00309", "00320", "00356", "00150", "00223", "00276", "00247"],
    ["00254", "00131", "00154", "00252", "00054", "00300", "00047", "00039", "00023", "00034"],
    ["00349", "00351", "00241"],
]

# Names are extracted from the public search-page response in the supplied HAR.
HOTEL_NAMES = {
    "00016": "东横INN 新大阪中央口本馆",
    "00023": "东横INN 大阪心斋桥西",
    "00033": "东横INN 大阪天满桥大手前",
    "00034": "东横INN 大阪梅田中津1号店",
    "00039": "东横INN 大阪船场东",
    "00047": "东横INN 大阪本町1号店",
    "00054": "东横INN 大阪难波西",
    "00071": "东横INN 大阪堺东站",
    "00091": "东横INN 新大阪中央口新馆",
    "00129": "东横INN 大阪梅田东",
    "00131": "东横INN 大阪淀屋桥站南",
    "00150": "东横INN 大阪谷四交差点",
    "00154": "东横INN 大阪阪急十三站西口1号店",
    "00200": "东横INN 大阪JR野田站前",
    "00223": "东横INN 大阪梅田中津2号店",
    "00237": "东横INN 大阪鹤桥站前",
    "00240": "东横INN 新大阪站东口",
    "00241": "东横INN 大阪伊丹机场",
    "00247": "东横INN 大阪阿倍野天王寺",
    "00252": "东横INN 大阪通天阁前",
    "00254": "东横INN 大阪难波日本桥",
    "00266": "东横INN 大阪本町2号店",
    "00276": "东横INN 新大阪东三国站前",
    "00287": "东横INN 大阪日本桥文乐剧场前",
    "00298": "东横INN 大阪京桥樱之宫",
    "00300": "东横INN 大阪阪急十三站西口2号店",
    "00309": "东横INN 大阪天神桥筋六丁目",
    "00320": "东横INN 大阪门真市站前",
    "00349": "东横INN 大阪弁天町",
    "00351": "东横INN 大阪难波",
    "00356": "东横INN 大阪巨蛋前",
    "00363": "东横INN 东大阪",
    "00366": "东横INN 大阪堺站前",
}


class Status(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HotelResult:
    code: str
    status: Status
    lowest_price: int | float | None = None
    is_under_maintenance: bool | None = None
    reason: str | None = None


def all_hotel_codes() -> list[str]:
    return [code for batch in HOTEL_BATCHES for code in batch]


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "toyoko-osaka-monitor/1.0 (+GitHub Actions; read-only)",
        }
    )
    return session


def build_input(hotel_codes: list[str], smoking_type: str) -> dict[str, Any]:
    if smoking_type not in {"all", "smoking", "noSmoking"}:
        raise ValueError(f"Unsupported smoking type: {smoking_type}")
    return {
        "0": {
            "json": {
                "hotelCodes": hotel_codes,
                "checkinDate": CHECKIN_UTC,
                "checkoutDate": CHECKOUT_UTC,
                "numberOfPeople": PEOPLE,
                "numberOfRoom": ROOMS,
                "smokingType": smoking_type,
            },
            "meta": {
                "values": {
                    "checkinDate": ["Date"],
                    "checkoutDate": ["Date"],
                }
            },
        }
    }


def unknown_batch(hotel_codes: list[str], reason: str) -> dict[str, HotelResult]:
    return {
        code: HotelResult(code=code, status=Status.UNKNOWN, reason=reason)
        for code in hotel_codes
    }


def parse_hotel_result(code: str, raw: Any) -> HotelResult:
    if not isinstance(raw, dict):
        return HotelResult(code, Status.UNKNOWN, reason="酒店结果不是对象")

    available = raw.get("existEnoughVacantRooms")
    maintenance = raw.get("isUnderMaintenance")
    lowest_price = raw.get("lowestPrice")

    if not isinstance(available, bool):
        return HotelResult(code, Status.UNKNOWN, reason="缺少或无法识别空房字段")
    if not isinstance(maintenance, bool):
        return HotelResult(code, Status.UNKNOWN, reason="缺少或无法识别维护字段")
    if not isinstance(lowest_price, (int, float)) or isinstance(lowest_price, bool):
        return HotelResult(code, Status.UNKNOWN, reason="缺少或无法识别最低价格")
    if maintenance:
        return HotelResult(
            code,
            Status.UNKNOWN,
            lowest_price=lowest_price,
            is_under_maintenance=True,
            reason="酒店库存处于维护状态",
        )

    return HotelResult(
        code,
        Status.AVAILABLE if available else Status.UNAVAILABLE,
        lowest_price=lowest_price,
        is_under_maintenance=False,
    )


def fetch_batch(
    session: requests.Session,
    hotel_codes: list[str],
    smoking_type: str,
) -> dict[str, HotelResult]:
    try:
        response = session.get(
            API_URL,
            params={
                "batch": "1",
                "input": json.dumps(
                    build_input(hotel_codes, smoking_type),
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.Timeout:
        return unknown_batch(hotel_codes, "API 请求超时")
    except requests.HTTPError as error:
        status_code = error.response.status_code if error.response is not None else "unknown"
        return unknown_batch(hotel_codes, f"API request failed: HTTP {status_code}")
    except requests.RequestException as error:
        return unknown_batch(hotel_codes, f"API 请求失败：{type(error).__name__}")

    try:
        payload = response.json()
    except (requests.JSONDecodeError, ValueError):
        return unknown_batch(hotel_codes, "API 返回内容不是有效 JSON")

    try:
        prices = payload[0]["result"]["data"]["json"]["prices"]
    except (IndexError, KeyError, TypeError):
        return unknown_batch(hotel_codes, "API JSON 结构缺失 prices 字段")
    if not isinstance(prices, dict):
        return unknown_batch(hotel_codes, "API prices 字段不是对象")

    return {
        code: parse_hotel_result(code, prices.get(code))
        for code in hotel_codes
    }


def fetch_all(session: requests.Session, smoking_type: str) -> dict[str, HotelResult]:
    results: dict[str, HotelResult] = {}
    for batch in HOTEL_BATCHES:
        results.update(fetch_batch(session, batch, smoking_type))
    return results


def confirm_candidates(
    session: requests.Session,
    initial: dict[str, HotelResult],
    delay_seconds: float,
) -> tuple[dict[str, HotelResult], dict[str, HotelResult]]:
    """Confirm candidate vacancies and separately classify smoking availability."""
    final = dict(initial)
    smoking_results: dict[str, HotelResult] = {}
    candidate_codes = {
        code for code, result in initial.items() if result.status is Status.AVAILABLE
    }
    affected_batches = [batch for batch in HOTEL_BATCHES if candidate_codes.intersection(batch)]
    if not affected_batches:
        return final, smoking_results

    first_smoking: dict[str, HotelResult] = {}
    for batch in affected_batches:
        first_smoking.update(fetch_batch(session, batch, "smoking"))

    if delay_seconds > 0:
        print(f"Candidate vacancy detected; waiting {delay_seconds:g}s before confirmation.")
        time.sleep(delay_seconds)

    second_all: dict[str, HotelResult] = {}
    second_smoking: dict[str, HotelResult] = {}
    for batch in affected_batches:
        second_all.update(fetch_batch(session, batch, "all"))
        second_smoking.update(fetch_batch(session, batch, "smoking"))

    for code in candidate_codes:
        confirmation = second_all[code]
        if confirmation.status is Status.AVAILABLE:
            final[code] = confirmation
        elif confirmation.status is Status.UNAVAILABLE:
            final[code] = confirmation
        else:
            final[code] = HotelResult(
                code,
                Status.UNKNOWN,
                reason=f"候选空房二次确认失败：{confirmation.reason or '未知原因'}",
            )

        first = first_smoking.get(code)
        second = second_smoking.get(code)
        if first is None or second is None:
            smoking_results[code] = HotelResult(
                code, Status.UNKNOWN, reason="吸烟房确认结果缺失"
            )
        elif first.status is Status.AVAILABLE and second.status is Status.AVAILABLE:
            smoking_results[code] = second
        elif first.status is Status.UNKNOWN or second.status is Status.UNKNOWN:
            smoking_results[code] = HotelResult(
                code, Status.UNKNOWN, reason="吸烟房查询未能完成两次确认"
            )
        else:
            smoking_results[code] = HotelResult(code, Status.UNAVAILABLE)

    return final, smoking_results


def format_price(price: int | float | None) -> str:
    if price is None or price == 0:
        return "官网未提供"
    return f"¥{price:,.0f}"


def smoking_description(code: str, smoking_results: dict[str, HotelResult]) -> str:
    result = smoking_results.get(code)
    if result is None or result.status is Status.UNKNOWN:
        return "有房，但吸烟属性未能确认"
    if result.status is Status.AVAILABLE:
        return "吸烟房可订（已二次确认）"
    return "当前空房中未确认到吸烟房"


def available_results_in_priority_order(
    results: dict[str, HotelResult],
) -> list[HotelResult]:
    """Put the friend's hotel first while preserving the HAR order for all others."""
    available_by_code = {
        code: result
        for code, result in results.items()
        if result.status is Status.AVAILABLE
    }
    ordered_codes = [PREFERRED_HOTEL_CODE, *all_hotel_codes()]
    return [
        available_by_code.pop(code)
        for code in ordered_codes
        if code in available_by_code
    ]


def format_available_line(
    result: HotelResult,
    smoking_results: dict[str, HotelResult],
) -> str:
    return (
        f"- **{HOTEL_NAMES[result.code]}**（`{result.code}`）— "
        f"{smoking_description(result.code, smoking_results)}；"
        f"最低价 {format_price(result.lowest_price)}"
    )


def print_results(
    results: dict[str, HotelResult], smoking_results: dict[str, HotelResult]
) -> None:
    print("Toyoko Inn availability check")
    print(f"Check-in: {CHECKIN_DATE}")
    print(f"Check-out: {CHECKOUT_DATE}")
    print(f"Hotels: {len(all_hotel_codes())}")
    print()
    for code in all_hotel_codes():
        result = results[code]
        suffix = ""
        if result.status is Status.AVAILABLE:
            suffix = f" | {smoking_description(code, smoking_results)} | {format_price(result.lowest_price)}"
        elif result.status is Status.UNKNOWN:
            suffix = f" | {result.reason or '未知错误'}"
        print(f"{code} {HOTEL_NAMES[code]:<30} {result.status.value}{suffix}")


def build_summary(
    results: dict[str, HotelResult],
    smoking_results: dict[str, HotelResult],
    now_shanghai: datetime,
) -> str:
    available = available_results_in_priority_order(results)
    unavailable = [result for result in results.values() if result.status is Status.UNAVAILABLE]
    unknown = [result for result in results.values() if result.status is Status.UNKNOWN]

    lines = [
        "# 东横 INN 大阪空房监控",
        "",
        f"- 查询时间：{now_shanghai:%Y-%m-%d %H:%M:%S}（中国时间）",
        f"- 入住：{CHECKIN_DATE}",
        f"- 退房：{CHECKOUT_DATE}",
        f"- 酒店：{len(results)} 家",
        f"- 有房：{len(available)}",
        f"- 满房：{len(unavailable)}",
        f"- 未知：{len(unknown)}",
        "",
    ]
    if available:
        preferred = next(
            (result for result in available if result.code == PREFERRED_HOTEL_CODE),
            None,
        )
        if preferred is not None:
            lines.extend(
                [
                    f"## {PREFERRED_HOTEL_LABEL}",
                    "",
                    format_available_line(preferred, smoking_results),
                    "",
                ]
            )
        other_available = [
            result for result in available if result.code != PREFERRED_HOTEL_CODE
        ]
        if other_available:
            lines.extend(["## 其他当前有空房", ""])
            for result in other_available:
                lines.append(format_available_line(result, smoking_results))
        lines.extend(["", f"[打开东横 INN 预订页面]({BOOKING_URL})", ""])
    elif not unknown:
        lines.extend(["## 结果：目前大阪府 33 家全部满房", ""])

    if unknown:
        lines.extend(
            [
                "## 查询失败或状态未知",
                "",
                "> UNKNOWN 不代表满房。本次无法确认的酒店如下。",
                "",
            ]
        )
        for result in unknown:
            lines.append(
                f"- {HOTEL_NAMES[result.code]}（`{result.code}`）：{result.reason or '未知错误'}"
            )
        lines.append("")

    lines.extend(
        [
            "## 33 家明细",
            "",
            "| 酒店代码 | 酒店 | 状态 | 最低价 | 说明 |",
            "|---|---|---|---:|---|",
        ]
    )
    for code in all_hotel_codes():
        result = results[code]
        note = (
            smoking_description(code, smoking_results)
            if result.status is Status.AVAILABLE
            else (result.reason or "")
        )
        lines.append(
            f"| `{code}` | {HOTEL_NAMES[code]} | {result.status.value} | "
            f"{format_price(result.lowest_price)} | {note} |"
        )
    return "\n".join(lines) + "\n"


def append_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(markdown)
    except OSError as error:
        print(f"Unable to write GitHub Job Summary: {error}", file=sys.stderr)


def github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "toyoko-osaka-monitor",
    }


def find_or_create_notification_issue(
    session: requests.Session,
    repository: str,
    token: str,
    owner: str,
    first_message: str,
) -> tuple[int, bool]:
    api = f"https://api.github.com/repos/{repository}/issues"
    response = session.get(
        api,
        headers=github_headers(token),
        params={"state": "all", "per_page": 100},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    for issue in response.json():
        if "pull_request" not in issue and issue.get("title") == ISSUE_TITLE:
            number = int(issue["number"])
            if issue.get("state") != "open":
                reopen = session.patch(
                    f"{api}/{number}",
                    headers=github_headers(token),
                    json={"state": "open"},
                    timeout=REQUEST_TIMEOUT,
                )
                reopen.raise_for_status()
            return number, False

    body = (
        f"@{owner}\n\n"
        "这是 GitHub Actions 自动维护的监控通知中心。请点击右侧 **Subscribe**，"
        "并在 GitHub 通知设置中启用 Email。满房时不会评论；发现空房或查询失败时才会评论。\n\n"
        "---\n\n"
        f"{first_message}"
    )
    created = session.post(
        api,
        headers=github_headers(token),
        json={"title": ISSUE_TITLE, "body": body},
        timeout=REQUEST_TIMEOUT,
    )
    created.raise_for_status()
    return int(created.json()["number"]), True


def send_github_notification(markdown: str) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repository:
        print("GitHub notification skipped: GITHUB_TOKEN or GITHUB_REPOSITORY is not set.")
        return

    owner = repository.split("/", 1)[0]
    session = requests.Session()
    issue_number, created = find_or_create_notification_issue(
        session, repository, token, owner, markdown
    )
    if created:
        print(f"GitHub notification issue created: #{issue_number}")
        return

    response = session.post(
        f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments",
        headers=github_headers(token),
        json={"body": f"@{owner}\n\n{markdown}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    print(f"GitHub notification comment added to issue #{issue_number}.")


def build_alert_message(
    results: dict[str, HotelResult],
    smoking_results: dict[str, HotelResult],
    now_shanghai: datetime,
) -> str:
    available = available_results_in_priority_order(results)
    unknown = [result for result in results.values() if result.status is Status.UNKNOWN]
    lines = [
        f"查询时间：{now_shanghai:%Y-%m-%d %H:%M:%S}（中国时间）  ",
        f"入住：{CHECKIN_DATE}  ",
        f"退房：{CHECKOUT_DATE}",
        "",
    ]
    if available:
        preferred = next(
            (result for result in available if result.code == PREFERRED_HOTEL_CODE),
            None,
        )
        if preferred is not None:
            lines.extend(
                [
                    f"## {PREFERRED_HOTEL_LABEL}",
                    "",
                    "**大阪本町2号店有房，和朋友住同一家最方便。**",
                    "",
                    format_available_line(preferred, smoking_results),
                    "",
                ]
            )
        other_available = [
            result for result in available if result.code != PREFERRED_HOTEL_CODE
        ]
        if other_available:
            heading = "## 🚨 其他空房" if preferred is not None else "## 🚨 发现空房"
            lines.extend([heading, ""])
            for result in other_available:
                lines.append(format_available_line(result, smoking_results))
        lines.extend(["", f"[立即打开东横 INN 预订页面]({BOOKING_URL})", ""])
    if unknown:
        lines.extend(
            [
                "## ⚠️ 查询失败或状态未知",
                "",
                "**UNKNOWN 不代表满房。** 本次无法确认：",
                "",
            ]
        )
        for result in unknown:
            lines.append(
                f"- {HOTEL_NAMES[result.code]}（`{result.code}`）：{result.reason or '未知错误'}"
            )
    return "\n".join(lines)


def monitor_has_ended(now_japan: datetime) -> bool:
    stop_at = datetime(2026, 10, 30, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    return now_japan >= stop_at


def is_quiet_hours(now_shanghai: datetime) -> bool:
    """Block delayed scheduled runs from querying during China quiet hours."""
    return 0 <= now_shanghai.hour < 6


def main() -> int:
    now_japan = datetime.now(ZoneInfo("Asia/Tokyo"))
    now_shanghai = now_japan.astimezone(ZoneInfo("Asia/Shanghai"))
    if monitor_has_ended(now_japan):
        message = (
            "Monitoring period has ended.\n"
            "No API request was sent.\n"
            "请在 GitHub Actions 页面手动 Disable workflow，以停止后续空运行。"
        )
        print(message)
        append_step_summary(f"# 东横 INN 大阪空房监控\n\n{message}\n")
        return 0

    if is_quiet_hours(now_shanghai):
        message = (
            f"Quiet hours: {now_shanghai:%Y-%m-%d %H:%M:%S} China time.\n"
            "No Toyoko Inn API request was sent and no notification was created."
        )
        print(message)
        append_step_summary(f"# 东横 INN 大阪空房监控\n\n{message}\n")
        return 0

    session = build_session()
    initial = fetch_all(session, "all")
    delay = float(os.environ.get("TOYOKO_CONFIRM_DELAY_SECONDS", DEFAULT_CONFIRM_DELAY_SECONDS))
    results, smoking_results = confirm_candidates(session, initial, max(0.0, delay))
    print_results(results, smoking_results)

    summary = build_summary(results, smoking_results, now_shanghai)
    append_step_summary(summary)

    available = any(result.status is Status.AVAILABLE for result in results.values())
    unknown = any(result.status is Status.UNKNOWN for result in results.values())
    if available or unknown:
        alert = build_alert_message(results, smoking_results, now_shanghai)
        try:
            send_github_notification(alert)
        except requests.RequestException as error:
            print(
                f"GitHub notification failed: {type(error).__name__}",
                file=sys.stderr,
            )
            return 2
    else:
        print("All 33 hotels are currently unavailable. No notification sent.")

    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
