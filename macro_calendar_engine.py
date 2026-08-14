"""Compatibility facade for the verified Macro v2 subsystem.

No function in this module synthesizes an economic value. Missing observations
remain ``None`` all the way to the API and user interface.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from macro import get_service


SCHEMA_VERSION = 2


def init_macro_db() -> None:
    get_service().repository.init_schema()


def get_macro_calendar(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    country: Optional[str] = None,
    importance: Optional[int] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    service = get_service()
    if force_refresh:
        service.request_refresh()
    return service.get_calendar(
        start_date, end_date, country=country, importance=importance,
        category=category, search=search,
    )


def get_macro_tickers() -> dict[str, Any]:
    """Compatibility facade for the VN30 market ribbon."""
    from market_ribbon_service import get_market_ribbon

    return get_market_ribbon()


def get_macro_event_detail(event_id: str) -> Optional[dict[str, Any]]:
    return get_service().get_event(event_id)


def request_macro_refresh() -> dict[str, Any]:
    return get_service().request_refresh()


def start_macro_background_sync() -> None:
    get_service().start_scheduler()


def audit_macro_data() -> dict[str, Any]:
    return get_service().repository.audit()


def _ics_escape(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _fold_ics(line: str, limit: int = 75) -> list[str]:
    """Fold content lines by UTF-8 octets without splitting a codepoint."""
    if len(line.encode("utf-8")) <= limit:
        return [line]
    folded: list[str] = []
    current = ""
    for character in line:
        candidate = current + character
        if current and len(candidate.encode("utf-8")) > limit:
            folded.append(current)
            current = " " + character
        else:
            current = candidate
    if current:
        folded.append(current)
    return folded


def export_macro_ics(start_date: str, end_date: str) -> str:
    data = get_macro_calendar(start_date or None, end_date or None, country="USD")
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Loc Phat Securities//Verified US Macro Calendar v2//VI",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Lộc Phát - Lịch Kinh tế Mỹ",
    ]
    for event in data.get("events", []):
        evidence = event.get("evidence") or []
        if not evidence or event.get("verification") not in {"official", "aggregator"}:
            continue
        lines.extend(["BEGIN:VEVENT", f"UID:macro-{event['id']}@locphatsecurities.vn", f"DTSTAMP:{now_stamp}"])
        if event.get("scheduled_at_utc"):
            scheduled = datetime.fromisoformat(str(event["scheduled_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc)
            lines.append(f"DTSTART:{scheduled.strftime('%Y%m%dT%H%M%SZ')}")
            lines.append(f"DTEND:{(scheduled + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}")
        else:
            start_day = date.fromisoformat(event["event_date"])
            lines.append(f"DTSTART;VALUE=DATE:{start_day.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{(start_day + timedelta(days=1)).strftime('%Y%m%d')}")
        summary = f"[{event.get('country', 'USD')}] {event.get('title_vi') or event.get('title')}"
        description_parts = [f"Nguồn: {event.get('source')}", f"Xác minh: {event.get('verification')}"]
        if event.get("actual") is not None:
            description_parts.append(f"Thực tế: {event['actual']}")
        if event.get("previous") is not None:
            description_parts.append(f"Kỳ trước: {event['previous']}")
        lines.append(f"SUMMARY:{_ics_escape(summary)}")
        lines.append(f"DESCRIPTION:{_ics_escape(chr(10).join(description_parts))}")
        if event.get("source_url"):
            lines.append(f"URL:{_ics_escape(event['source_url'])}")
        lines.extend(["STATUS:CONFIRMED", "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in _fold_ics(line)) + "\r\n"
