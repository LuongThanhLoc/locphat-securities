"""Source-aware Vietnamese corporate calendar.

Calendar v2 is deliberately fail-honest:
- it never invents a date role or time;
- it stores one occurrence per observed milestone;
- it keeps source evidence and measured coverage;
- partial refreshes never replace the last-known-good dataset.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from market_data_provider import Listing, VCI_IQ, _get_json, _unwrap_data


os.environ.setdefault("TZ", "Asia/Ho_Chi_Minh")
VN_TZ = timezone(timedelta(hours=7))
DB_PATH = os.path.join(os.path.dirname(__file__), "corporate_calendar.db")
SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "corporate_calendar_snapshot.json")
SCHEMA_VERSION = 2
MAX_QUERY_DAYS = 366
FETCH_TIMEOUT_SECONDS = 12
REPORT_WORKERS = 3 if os.environ.get("RENDER") == "true" else 8
ACTION_REFRESH_SECONDS = 30 * 60
REPORT_REFRESH_SECONDS = 6 * 60 * 60

_SYNC_LOCK = threading.Lock()
_SYNC_THREAD: Optional[threading.Thread] = None
_WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_STOP = threading.Event()
_REFRESH_STATE: Dict[str, Any] = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "error": None,
}


REPORT_EXCLUSION_PATTERNS = (
    r"(?:ký|kí|gia hạn|thay đổi).*hợp đồng.*kiểm toán",
    r"(?:lựa chọn|chọn|bổ nhiệm|thay đổi).*đơn vị kiểm toán",
    r"báo cáo thường niên",
    r"báo cáo quản trị",
    r"báo cáo phát triển bền vững",
    r"báo cáo kiểm toán nội bộ",
    r"báo cáo tình hình quản trị",
)
FINANCIAL_REPORT_PATTERNS = (
    r"\bbctc\b",
    r"báo cáo tài chính",
    r"giải trình.*(?:lnst|lợi nhuận).*bctc",
)
EARNINGS_RELEASE_PATTERNS = (
    r"\bkqkd\b",
    r"kết quả kinh doanh",
    r"thông cáo.*kinh doanh",
)
STOCK_DIVIDEND_PATTERNS = (
    r"cổ phiếu thưởng",
    r"chia.*cổ phiếu",
    r"cổ tức.*bằng.*(?:cp|cổ phiếu)",
    r"phát hành.*cổ phiếu.*thưởng",
)

EVENT_CODES = "DIV,ISS,AGME,AGMR,EGME,EGMR,AIS,LIST,DELIST,SUSP,HALT"
DATE_ROLE_LABELS = {
    "ex_right": "Ngày GDKHQ",
    "record": "Ngày ĐKCC",
    "payment": "Ngày thanh toán",
    "meeting": "Ngày họp ĐHĐCĐ",
    "issue": "Ngày phát hành",
    "listing": "Ngày niêm yết/GD đầu tiên",
    "delisting": "Ngày hủy niêm yết",
    "suspension_start": "Ngày tạm ngừng",
    "publication": "Ngày công bố",
    "provider_display": "Ngày theo nguồn",
}
ROLE_PRIORITY = {
    "ex_right": 0,
    "record": 1,
    "meeting": 2,
    "listing": 3,
    "delisting": 4,
    "suspension_start": 5,
    "issue": 6,
    "payment": 7,
    "publication": 8,
    "provider_display": 9,
}


def _vietnam_now() -> datetime:
    return datetime.now(VN_TZ)


def _vietnam_today() -> date:
    return _vietnam_now().date()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "null"}:
        return None
    return text


def _iso_date(value: Any) -> Optional[str]:
    text = _clean(value)
    if not text:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def _iso_time(value: Any) -> Optional[str]:
    text = _clean(value)
    if not text:
        return None
    match = re.search(r"T(\d{2}:\d{2})(?::\d{2})?", text)
    return match.group(1) if match else None


def _in_window(value: Optional[str], start: date, end: date) -> bool:
    return bool(value and start.isoformat() <= value <= end.isoformat())


def _status(event_date: str) -> str:
    today = _vietnam_today().isoformat()
    if event_date > today:
        return "upcoming"
    if event_date == today:
        return "today"
    return "occurred"


def _snake_case_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower(): value
        for key, value in row.items()
    }


def _classify(title: str) -> Optional[str]:
    """Classify only explicit financial disclosures.

    Unknown items must stay unknown. They are never coerced to BCTC.
    """
    lowered = re.sub(r"\s+", " ", str(title or "").lower()).strip()
    if any(re.search(pattern, lowered) for pattern in REPORT_EXCLUSION_PATTERNS):
        return None
    if any(re.search(pattern, lowered) for pattern in FINANCIAL_REPORT_PATTERNS):
        return "financial_report"
    if any(re.search(pattern, lowered) for pattern in EARNINGS_RELEASE_PATTERNS):
        return "earnings_release"
    return None


def _event_kind(row: Dict[str, Any]) -> Optional[str]:
    code = str(_clean(row.get("event_code")) or "").upper()
    category = str(_clean(row.get("category")) or "").upper()
    title = str(_clean(row.get("event_title_vi")) or _clean(row.get("event_name_vi")) or "").lower()
    if code == "DIV":
        return "stock_dividend" if any(re.search(p, title) for p in STOCK_DIVIDEND_PATTERNS) else "cash_dividend"
    if code in {"AGME", "AGMR"} or category == "SHAREHOLDER_MEETING":
        return "shareholder_meeting_annual"
    if code in {"EGME", "EGMR"} or re.search(r"bất thường|\begm\b", title):
        return "shareholder_meeting_extraordinary"
    if code in {"ISS", "AIS"}:
        return "capital_action"
    if code in {"LIST", "DELIST"}:
        return "listing_change"
    if code in {"SUSP", "HALT"}:
        return "trading_halt"
    return None


def _event_priority(event_type: str) -> int:
    return {
        "trading_halt": 0,
        "listing_change": 1,
        "cash_dividend": 2,
        "stock_dividend": 3,
        "shareholder_meeting_annual": 4,
        "shareholder_meeting_extraordinary": 5,
        "capital_action": 6,
        "financial_report": 7,
        "earnings_release": 8,
    }.get(event_type, 99)


def _ratio_label(row: Dict[str, Any], kind: str) -> Optional[str]:
    amount = _clean(row.get("value_per_share"))
    if kind == "cash_dividend" and amount:
        try:
            return f"{float(amount):,.0f} VND/cp"
        except (TypeError, ValueError):
            return None
    ratio = _clean(row.get("exercise_ratio"))
    if ratio:
        try:
            value = float(ratio)
            return f"Tỷ lệ {value * 100:.2f}%" if value <= 20 else f"Tỷ lệ {value:.2f}%"
        except (TypeError, ValueError):
            return f"Tỷ lệ {ratio}"
    return None


def _float_or_none(value: Any) -> Optional[float]:
    clean = _clean(value)
    if clean is None:
        return None
    try:
        return float(clean)
    except (TypeError, ValueError):
        return None


def _canonical_hash(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _provider_evidence(raw_id: Any, published_at: Any, source_url: Any = None) -> Dict[str, Any]:
    clean_url = _clean(source_url)
    official_hosts = ("vsd.vn", "hsx.vn", "hnx.vn", "ssc.gov.vn")
    is_official = bool(clean_url and any(host in str(clean_url).lower() for host in official_hosts))
    return {
        "source_name": "Nguồn công bố chính thức" if is_official else "Vietcap public REST",
        "source_tier": "official" if is_official else "aggregator",
        "source_url": clean_url,
        "raw_id": _clean(raw_id),
        "published_at": _clean(published_at),
        "observed_at": _utc_now_iso(),
    }


def _verification(evidence: list[Dict[str, Any]], *, stale: bool = False) -> Dict[str, Any]:
    official = [item for item in evidence if item.get("source_tier") == "official"]
    status = "cross_checked" if official and len(evidence) > 1 else "official" if official else "provider_only"
    return {
        "status": status,
        "sources": evidence,
        "conflict_fields": [],
        "stale": bool(stale),
    }


def _related_dates(row: Dict[str, Any]) -> Dict[str, Optional[str]]:
    return {
        "publication_date": _iso_date(row.get("public_date")),
        "ex_right_date": _iso_date(row.get("exright_date")),
        "record_date": _iso_date(row.get("record_date")),
        "payment_date": _iso_date(row.get("payout_date")),
        "meeting_date": _iso_date(row.get("meeting_date")),
        "issue_date": _iso_date(row.get("issue_date")),
        "listing_date": _iso_date(row.get("listing_date")),
        "delisting_date": _iso_date(row.get("delist_date")),
        "provider_display_date": _iso_date(row.get("display_date1")),
    }


def _structured_milestones(kind: str, dates: Dict[str, Optional[str]]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, Optional[str]]] = []
    if kind in {"cash_dividend", "stock_dividend"}:
        candidates = [
            ("ex_right", dates["ex_right_date"]),
            ("record", dates["record_date"]),
            ("payment", dates["payment_date"]),
        ]
    elif kind.startswith("shareholder_meeting"):
        candidates = [
            ("meeting", dates["meeting_date"]),
            ("ex_right", dates["ex_right_date"]),
            ("record", dates["record_date"]),
        ]
    elif kind == "listing_change":
        candidates = [
            ("listing", dates["listing_date"]),
            ("delisting", dates["delisting_date"]),
        ]
    elif kind == "trading_halt":
        candidates = [("suspension_start", dates["issue_date"])]
    else:
        candidates = [
            ("listing", dates["listing_date"]),
            ("issue", dates["issue_date"]),
            ("ex_right", dates["ex_right_date"]),
            ("record", dates["record_date"]),
        ]

    result = [(role, value) for role, value in candidates if value]
    # displayDate is explicitly generic. Use it only when the source exposes no
    # semantically named milestone; never relabel it as GDKHQ or meeting date.
    if not result and dates.get("provider_display_date"):
        result.append(("provider_display", str(dates["provider_display_date"])))
    return result


def _corporate_action_occurrences(row: Dict[str, Any], start: date, end: date) -> list[Dict[str, Any]]:
    kind = _event_kind(row)
    symbol = str(_clean(row.get("ticker")) or "").upper()
    if not kind or not re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", symbol):
        return []

    raw_id = str(_clean(row.get("id")) or _canonical_hash(symbol, kind, row.get("event_title_vi")))
    title = str(_clean(row.get("event_title_vi")) or _clean(row.get("event_name_vi")) or "Sự kiện doanh nghiệp")
    title = re.sub(rf"^{re.escape(symbol)}\s*[-:]\s*", "", title, flags=re.IGNORECASE).strip()
    dates = _related_dates(row)
    evidence = [_provider_evidence(raw_id, row.get("public_date"))]
    canonical_event_id = f"vci:{raw_id}"
    ratio_label = _ratio_label(row, kind)
    details = {
        "cash_per_share": _float_or_none(row.get("value_per_share")) if kind == "cash_dividend" else None,
        "exercise_ratio": _float_or_none(row.get("exercise_ratio")),
        "issue_price": _float_or_none(row.get("issue_price")),
        "meeting_location": _clean(row.get("meeting_location") or row.get("event_location")),
    }

    occurrences = []
    for role, event_date in _structured_milestones(kind, dates):
        if not _in_window(event_date, start, end):
            continue
        occurrence_id = f"{canonical_event_id}:{role}:{event_date}"
        occurrences.append({
            "id": occurrence_id,
            "canonical_event_id": canonical_event_id,
            "symbol": symbol,
            "exchange": _clean(row.get("exchange")),
            "event_date": event_date,
            "event_time": None,
            "date_role": DATE_ROLE_LABELS[role],
            "date_role_code": role,
            "date_role_label": DATE_ROLE_LABELS[role],
            "type": kind,
            "title": title,
            "status": _status(event_date),
            "priority": _event_priority(kind),
            "related_dates": dates,
            "details": details,
            "verification": _verification(evidence),
            "source": "Vietcap public REST",
            "source_url": None,
            "source_verified": False,
            # Compatibility fields retained for current consumers.
            "published_at": _clean(row.get("public_date")),
            "record_date": dates["record_date"],
            "exright_date": dates["ex_right_date"],
            "payout_date": dates["payment_date"],
            "listing_date": dates["listing_date"],
            "delist_date": dates["delisting_date"],
            "ratio_label": ratio_label,
            "impact": "high" if kind in {"trading_halt", "listing_change", "cash_dividend", "stock_dividend"} else "medium",
        })
    return occurrences


def _corporate_action_event(row: Dict[str, Any], start: date, end: date) -> Optional[Dict[str, Any]]:
    """Compatibility helper returning the highest-priority observed occurrence."""
    occurrences = _corporate_action_occurrences(row, start, end)
    if not occurrences:
        return None
    return sorted(occurrences, key=lambda item: (ROLE_PRIORITY.get(item["date_role_code"], 99), item["event_date"]))[0]


def _report_period(title: str) -> Optional[str]:
    lowered = str(title or "").lower()
    quarter = re.search(r"quý\s*([1-4ivx]+)\s*[/\-]?\s*(20\d{2})", lowered)
    if quarter:
        roman = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
        q = roman.get(quarter.group(1), quarter.group(1))
        return f"{quarter.group(2)}-Q{q}"
    year = re.search(r"(?:năm|cả năm)\s*(20\d{2})", lowered)
    return year.group(1) if year else None


def _disclosure_event(item: Dict[str, Any], symbol_hint: str, start: date, end: date) -> Optional[Dict[str, Any]]:
    raw_title = str(_clean(item.get("newsTitle")) or "")
    kind = _classify(raw_title)
    if kind is None:
        return None

    match = re.match(r"^([A-Z][A-Z0-9]{1,5})\s*[:\-–]\s*(.+)$", raw_title)
    explicit_ticker = str(_clean(item.get("ticker")) or "").upper()
    hint = str(symbol_hint or "").upper()
    # A per-symbol response without a ticker is accepted only when the title
    # explicitly starts with that symbol. This rejects generic media stories.
    if match:
        symbol = match.group(1)
        title = match.group(2).strip()
    elif explicit_ticker and explicit_ticker == hint:
        symbol = explicit_ticker
        title = raw_title
    else:
        return None
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", symbol):
        return None

    published_at = _clean(item.get("publicDate") or item.get("displayDate"))
    event_date = _iso_date(published_at)
    if not _in_window(event_date, start, end):
        return None
    raw_id = str(_clean(item.get("id")) or _canonical_hash(symbol, kind, event_date, title))
    source_url = _clean(item.get("newsSourceLink"))
    evidence = [_provider_evidence(raw_id, published_at, source_url)]
    canonical_event_id = f"disclosure:{raw_id}"
    verification = _verification(evidence)
    period = _report_period(title)
    scope = "parent" if re.search(r"công ty mẹ|riêng", title, re.IGNORECASE) else "consolidated" if re.search(r"hợp nhất", title, re.IGNORECASE) else None
    return {
        "id": f"{canonical_event_id}:publication:{event_date}",
        "canonical_event_id": canonical_event_id,
        "symbol": symbol,
        "exchange": None,
        "event_date": event_date,
        "event_time": _iso_time(published_at),
        "date_role": DATE_ROLE_LABELS["publication"],
        "date_role_code": "publication",
        "date_role_label": DATE_ROLE_LABELS["publication"],
        "type": kind,
        "title": re.sub(r"(?i)\b(\w+(?:\s+\w+)?)\s+\1\b", r"\1", title).strip(),
        "status": "published",
        "priority": _event_priority(kind),
        "related_dates": {
            "publication_date": event_date,
            "ex_right_date": None,
            "record_date": None,
            "payment_date": None,
            "meeting_date": None,
            "issue_date": None,
            "listing_date": None,
            "delisting_date": None,
            "provider_display_date": None,
        },
        "details": {"report_period": period, "report_scope": scope},
        "verification": verification,
        "source": "Vietcap public REST",
        "source_url": source_url,
        "source_verified": verification["status"] in {"official", "cross_checked"},
        "published_at": published_at,
        "record_date": None,
        "exright_date": None,
        "payout_date": None,
        "listing_date": None,
        "delist_date": None,
        "ratio_label": None,
        "impact": "high" if kind == "financial_report" else "medium",
    }


def _normalize_title_key(title: str) -> str:
    return re.sub(r"\W+", "", str(title or "").lower())


def _deduplicate(events: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    result: list[Dict[str, Any]] = []
    seen = set()
    for event in events:
        key = event.get("id") or (
            event.get("symbol"), event.get("type"), event.get("event_date"),
            event.get("date_role_code"), _normalize_title_key(event.get("title", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return sorted(result, key=lambda row: (
        row.get("event_date") or "9999-12-31",
        row.get("priority", 99),
        row.get("symbol") or "",
        row.get("date_role_code") or "",
        row.get("title") or "",
    ))


def _listed_universe() -> tuple[list[str], Dict[str, str]]:
    frame = Listing(source="VCI").symbols_by_industries()
    if frame is None or frame.empty:
        raise RuntimeError("Nguồn danh sách niêm yết trả rỗng")
    symbols: list[str] = []
    exchange_by_symbol: Dict[str, str] = {}
    for _, row in frame.iterrows():
        symbol = str(row.get("symbol") or "").upper().strip()
        security_type = str(row.get("com_type_code") or "").upper().strip()
        exchange = str(row.get("exchange") or "").upper().strip()
        if security_type == "QU" or exchange not in {"HOSE", "HNX", "UPCOM"}:
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", symbol):
            continue
        symbols.append(symbol)
        exchange_by_symbol[symbol] = exchange
    symbols = sorted(set(symbols))
    if len(symbols) < 300:
        raise RuntimeError(f"Universe niêm yết không đầy đủ ({len(symbols)} mã)")
    return symbols, exchange_by_symbol


def _fetch_global_actions(start: date, end: date) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    page = 0
    page_size = 200
    total_pages = 1
    # An old announcement may contain a payment milestone inside the requested
    # window, so the provider query intentionally looks back one year.
    from_date = start - timedelta(days=366)
    while page < total_pages:
        body = _unwrap_data(_get_json(
            f"{VCI_IQ}/v1/events",
            params={
                "fromDate": from_date.strftime("%Y%m%d"),
                "toDate": end.strftime("%Y%m%d"),
                "eventCode": EVENT_CODES,
                "page": page,
                "size": page_size,
            },
            timeout=FETCH_TIMEOUT_SECONDS,
        )) or {}
        rows = body.get("content", []) if isinstance(body, dict) else []
        total_pages = max(int(body.get("totalPages") or 1), 1) if isinstance(body, dict) else 1
        for raw in rows:
            events.extend(_corporate_action_occurrences(_snake_case_row(raw), start, end))
        page += 1
    return _deduplicate(events), {
        "pages_fetched": page,
        "pages_total": total_pages,
        "records_received": int(body.get("totalElements") or len(events)) if isinstance(body, dict) else len(events),
    }


def _fetch_symbol_reports(symbol: str, start: date, end: date) -> tuple[list[Dict[str, Any]], bool, int]:
    body = _unwrap_data(_get_json(
        f"{VCI_IQ}/v1/news",
        params={
            "ticker": symbol,
            "fromDate": start.strftime("%Y%m%d"),
            "toDate": end.strftime("%Y%m%d"),
            "languageId": 1,
            "page": 0,
            "size": 100,
        },
        timeout=FETCH_TIMEOUT_SECONDS,
    )) or {}
    rows = body.get("content", []) if isinstance(body, dict) else []
    accepted = [event for event in (_disclosure_event(item, symbol, start, end) for item in rows) if event]
    return accepted, True, len(rows)


def _fetch(start: date, end: date, *, include_reports: bool = True) -> Dict[str, Any]:
    symbols, exchange_by_symbol = _listed_universe()
    actions, action_meta = _fetch_global_actions(start, end)
    events = list(actions)
    report_symbols_scanned = report_sources_ok = raw_disclosures = 0
    if include_reports:
        with ThreadPoolExecutor(max_workers=REPORT_WORKERS, thread_name_prefix="lp-calendar") as pool:
            futures = {pool.submit(_fetch_symbol_reports, symbol, start, end): symbol for symbol in symbols}
            for future in as_completed(futures):
                report_symbols_scanned += 1
                symbol = futures[future]
                try:
                    rows, ok, raw_count = future.result()
                    report_sources_ok += int(ok)
                    raw_disclosures += raw_count
                    for item in rows:
                        item["exchange"] = exchange_by_symbol.get(symbol)
                    events.extend(rows)
                except Exception:
                    continue
    for item in events:
        item["exchange"] = item.get("exchange") or exchange_by_symbol.get(str(item.get("symbol") or ""))
    events = _deduplicate(events)
    rejected = max(raw_disclosures - sum(1 for event in events if event["type"] in {"financial_report", "earnings_release"}), 0)
    partial = include_reports and report_symbols_scanned < len(symbols)
    fetched_at = _utc_now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "events": events,
        "coverage": {
            "mode": "measured_source_coverage",
            "universe_total": len(symbols),
            "universe_scanned": report_symbols_scanned if include_reports else 0,
            "action_pages_fetched": action_meta["pages_fetched"],
            "action_pages_total": action_meta["pages_total"],
            "action_records_received": action_meta["records_received"],
            "report_sources_ok": report_sources_ok,
            "accepted_events": len(events),
            "rejected_items": rejected,
            "conflicts": 0,
            "partial": partial or not include_reports,
            "coverage_note": "Độ phủ là số nguồn/trang đã quét, không phải cam kết có đủ mọi công bố.",
        },
        "data_quality": {
            "no_synthetic_data": True,
            "as_of": fetched_at,
            "stale": False,
            "partial": partial or not include_reports,
        },
        "source": "Vietcap public REST; đối chiếu nguồn chính thức khi có URL bằng chứng",
        "fetched_at": fetched_at,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS corporate_events_v2 (
        id TEXT PRIMARY KEY,
        canonical_event_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        event_date TEXT NOT NULL,
        event_type TEXT NOT NULL,
        date_role TEXT NOT NULL,
        payload TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calendar_v2_date ON corporate_events_v2(event_date, symbol)")
    conn.execute("""CREATE TABLE IF NOT EXISTS calendar_sync_runs_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        window_start TEXT NOT NULL,
        window_end TEXT NOT NULL,
        status TEXT NOT NULL,
        requested INTEGER NOT NULL DEFAULT 0,
        received INTEGER NOT NULL DEFAULT 0,
        accepted INTEGER NOT NULL DEFAULT 0,
        rejected INTEGER NOT NULL DEFAULT 0,
        conflicts INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        error TEXT,
        meta TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("CREATE TABLE IF NOT EXISTS calendar_meta_v2 (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    _migrate_strict_legacy_reports(conn)
    return conn


def _migrate_strict_legacy_reports(conn: sqlite3.Connection) -> None:
    marker = conn.execute("SELECT value FROM calendar_meta_v2 WHERE key='legacy_migration'").fetchone()
    if marker:
        return
    old_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='corporate_events'").fetchone()
    now = _utc_now_iso()
    accepted = 0
    if old_exists:
        for row in conn.execute("SELECT payload FROM corporate_events"):
            try:
                item = json.loads(row["payload"])
            except Exception:
                continue
            if item.get("type") not in {"financial_report", "earnings_release"}:
                continue
            symbol = str(item.get("symbol") or "").upper()
            kind = _classify(f"{symbol}: {item.get('title') or ''}")
            event_date = _iso_date(item.get("event_date"))
            if kind not in {"financial_report", "earnings_release"} or not event_date:
                continue
            item["type"] = kind
            item["event_time"] = _iso_time(item.get("published_at"))
            item["date_role"] = DATE_ROLE_LABELS["publication"]
            item["date_role_code"] = "publication"
            item["date_role_label"] = DATE_ROLE_LABELS["publication"]
            item["canonical_event_id"] = item.get("canonical_event_id") or f"legacy:{_canonical_hash(symbol, kind, event_date, item.get('title'))}"
            item["id"] = f"{item['canonical_event_id']}:publication:{event_date}"
            item["verification"] = _verification([_provider_evidence(item.get("id"), item.get("published_at"), item.get("source_url"))], stale=True)
            item["source_verified"] = False
            item["related_dates"] = item.get("related_dates") or {"publication_date": event_date}
            item["details"] = item.get("details") or {"report_period": _report_period(item.get("title") or ""), "report_scope": None}
            conn.execute("INSERT OR IGNORE INTO corporate_events_v2 VALUES (?,?,?,?,?,?,?,?,?)", (
                item["id"], item["canonical_event_id"], symbol, event_date, kind, "publication",
                json.dumps(item, ensure_ascii=False), now, now,
            ))
            accepted += 1
    conn.execute("INSERT OR REPLACE INTO calendar_meta_v2 VALUES ('legacy_migration', ?)", (
        json.dumps({"at": now, "accepted_strict_reports": accepted}, ensure_ascii=False),
    ))
    conn.commit()


def _write_snapshot(snapshot: Dict[str, Any]) -> None:
    temp_path = f"{SNAPSHOT_PATH}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, SNAPSHOT_PATH)
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _promote_snapshot(snapshot: Dict[str, Any]) -> None:
    events = snapshot.get("events") or []
    coverage = snapshot.get("coverage") or {}
    if not events or int(coverage.get("action_pages_fetched") or 0) < int(coverage.get("action_pages_total") or 0):
        raise RuntimeError("Nguồn structured events chưa tải đủ trang; giữ nguyên last-known-good")
    conn = _connection()
    now = _utc_now_iso()
    started_at = snapshot.get("fetched_at") or now
    with conn:
        cursor = conn.execute("""INSERT INTO calendar_sync_runs_v2 (
            source,window_start,window_end,status,requested,received,accepted,rejected,conflicts,started_at,finished_at,error,meta
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "calendar_v2", snapshot["window_start"], snapshot["window_end"], "success",
            int(coverage.get("universe_total") or 0),
            int(coverage.get("action_records_received") or 0),
            len(events), int(coverage.get("rejected_items") or 0), int(coverage.get("conflicts") or 0),
            started_at, now, None, json.dumps({key: value for key, value in snapshot.items() if key != "events"}, ensure_ascii=False),
        ))
        sync_id = cursor.lastrowid
        for event in events:
            conn.execute("""INSERT INTO corporate_events_v2 (
                id,canonical_event_id,symbol,event_date,event_type,date_role,payload,first_seen_at,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload,last_seen_at=excluded.last_seen_at,event_type=excluded.event_type,date_role=excluded.date_role
            """, (
                event["id"], event["canonical_event_id"], event["symbol"], event["event_date"],
                event["type"], event.get("date_role_code") or "provider_display", json.dumps(event, ensure_ascii=False), now, now,
            ))
        conn.execute("INSERT OR REPLACE INTO calendar_meta_v2 VALUES ('active_sync_id', ?)", (str(sync_id),))
    conn.close()
    _write_snapshot(snapshot)


def _events_from_db(conn: sqlite3.Connection, start: date, end: date) -> list[Dict[str, Any]]:
    return [json.loads(row["payload"]) for row in conn.execute(
        "SELECT payload FROM corporate_events_v2 WHERE event_date BETWEEN ? AND ? ORDER BY event_date,event_type,symbol",
        (start.isoformat(), end.isoformat()),
    )]


def _nearby_from_db(conn: sqlite3.Connection, today: date) -> list[Dict[str, Any]]:
    return [json.loads(row["payload"]) for row in conn.execute(
        "SELECT payload FROM corporate_events_v2 ORDER BY ABS(julianday(event_date)-julianday(?)), event_date LIMIT 12",
        (today.isoformat(),),
    )]


def _latest_meta(conn: sqlite3.Connection) -> Dict[str, Any]:
    row = conn.execute("SELECT meta,finished_at FROM calendar_sync_runs_v2 WHERE status='success' ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {}
    meta = json.loads(row["meta"] or "{}")
    meta["last_success_at"] = row["finished_at"]
    return meta


def _refresh_snapshot(*, include_reports: bool) -> None:
    global _REFRESH_STATE
    if not _SYNC_LOCK.acquire(blocking=False):
        return
    started = _utc_now_iso()
    _REFRESH_STATE = {"state": "running", "started_at": started, "finished_at": None, "error": None}
    try:
        today = _vietnam_today()
        snapshot = _fetch(today - timedelta(days=62), today + timedelta(days=365), include_reports=include_reports)
        _promote_snapshot(snapshot)
        _REFRESH_STATE = {"state": "complete", "started_at": started, "finished_at": _utc_now_iso(), "error": None}
    except Exception as exc:
        _REFRESH_STATE = {"state": "error", "started_at": started, "finished_at": _utc_now_iso(), "error": str(exc)}
        try:
            conn = _connection()
            with conn:
                conn.execute("""INSERT INTO calendar_sync_runs_v2 (
                    source,window_start,window_end,status,started_at,finished_at,error
                ) VALUES (?,?,?,?,?,?,?)""", (
                    "calendar_v2", _vietnam_today().isoformat(), (_vietnam_today() + timedelta(days=365)).isoformat(),
                    "error", started, _utc_now_iso(), str(exc),
                ))
            conn.close()
        except Exception:
            pass
    finally:
        _SYNC_LOCK.release()


def request_calendar_refresh(*, include_reports: bool = True) -> Dict[str, Any]:
    global _SYNC_THREAD
    if _SYNC_THREAD and _SYNC_THREAD.is_alive():
        return dict(_REFRESH_STATE)
    _SYNC_THREAD = threading.Thread(
        target=_refresh_snapshot,
        kwargs={"include_reports": include_reports},
        daemon=True,
        name="corporate-calendar-refresh",
    )
    _SYNC_THREAD.start()
    return dict(_REFRESH_STATE)


def _worker_loop() -> None:
    last_actions = last_reports = 0.0
    while not _WORKER_STOP.is_set():
        now = time.time()
        include_reports = now - last_reports >= REPORT_REFRESH_SECONDS
        if now - last_actions >= ACTION_REFRESH_SECONDS:
            request_calendar_refresh(include_reports=include_reports)
            last_actions = now
            if include_reports:
                last_reports = now
        _WORKER_STOP.wait(60)


def start_calendar_background_sync() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _WORKER_STOP.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, daemon=True, name="corporate-calendar-scheduler")
    _WORKER_THREAD.start()


def _response(start: date, end: date, events: list[Dict[str, Any]], nearby: list[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    coverage = dict(meta.get("coverage") or {})
    coverage.update({
        "returned_events": len(events),
        "returned_symbols": len({event.get("symbol") for event in events}),
    })
    fetched_at = meta.get("fetched_at") or meta.get("last_success_at")
    stale = True
    if fetched_at:
        try:
            observed = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            stale = datetime.now(timezone.utc) - observed.astimezone(timezone.utc) > timedelta(hours=24)
        except (TypeError, ValueError):
            stale = True
    data_quality = dict(meta.get("data_quality") or {})
    data_quality.update({
        "no_synthetic_data": True,
        "as_of": fetched_at,
        "stale": stale,
        "partial": bool(coverage.get("partial", True)),
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "events": events,
        "nearby_events": nearby,
        "conflicts": [],
        "coverage": coverage,
        "data_quality": data_quality,
        "refresh": dict(_REFRESH_STATE),
        "no_synthetic_data": True,
        "source": meta.get("source") or "Calendar v2 verified cache",
        "fetched_at": fetched_at,
        "cache": "stale" if stale else "hit",
    }


def get_corporate_calendar(start: date, end: date, force_refresh: bool = False) -> Dict[str, Any]:
    if end < start or (end - start).days > MAX_QUERY_DAYS:
        raise ValueError(f"Khoảng lịch phải từ 0 đến {MAX_QUERY_DAYS} ngày.")
    conn = _connection()
    events = _events_from_db(conn, start, end)
    nearby = _nearby_from_db(conn, _vietnam_today())
    meta = _latest_meta(conn)
    total_v2 = conn.execute("SELECT COUNT(*) FROM corporate_events_v2").fetchone()[0]
    conn.close()

    if force_refresh:
        request_calendar_refresh(include_reports=True)
    elif not meta or meta.get("data_quality", {}).get("stale"):
        request_calendar_refresh(include_reports=not bool(total_v2))

    # A fresh installation may have no last-known-good yet. Fetch only the
    # paginated structured feed synchronously; the expensive report scan stays
    # in the background.
    if total_v2 == 0:
        today = _vietnam_today()
        try:
            bootstrap = _fetch(today - timedelta(days=62), today + timedelta(days=365), include_reports=False)
            _promote_snapshot(bootstrap)
            conn = _connection()
            events = _events_from_db(conn, start, end)
            nearby = _nearby_from_db(conn, today)
            meta = _latest_meta(conn)
            conn.close()
        except Exception:
            pass
    return _response(start, end, events, nearby, meta)


def fetch_price_affecting_actions(symbol: str, start: date, end: date) -> list[Dict[str, Any]]:
    """Return observed price-affecting occurrences for Market Bubbles."""
    symbol = str(symbol or "").upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", symbol):
        raise ValueError("Mã chứng khoán không hợp lệ")
    body = _unwrap_data(_get_json(
        f"{VCI_IQ}/v1/events",
        params={
            "ticker": symbol,
            "fromDate": (start - timedelta(days=366)).strftime("%Y%m%d"),
            "toDate": end.strftime("%Y%m%d"),
            "eventCode": "DIV,ISS,AIS,LIST,DELIST,SUSP,HALT",
            "page": 0,
            "size": 200,
        },
        timeout=FETCH_TIMEOUT_SECONDS,
    )) or {}
    rows = body.get("content", []) if isinstance(body, dict) else []
    accepted_types = {"cash_dividend", "stock_dividend", "capital_action", "listing_change", "trading_halt"}
    events: list[Dict[str, Any]] = []
    for raw in rows:
        for event in _corporate_action_occurrences(_snake_case_row(raw), start, end):
            if event.get("type") in accepted_types:
                events.append(event)
    return _deduplicate(events)
