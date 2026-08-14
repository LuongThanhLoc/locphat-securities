"""Network adapters for free official and aggregator macro sources.

Providers return raw, source-aware records. They never invent missing values.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from calendar import month_name
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .registry import CATEGORY_NAMES, IndicatorSpec, find_indicator


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
US_EASTERN = ZoneInfo("America/New_York")
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_SCHEDULE_URL = "https://www.bea.gov/news/schedule/full"
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FAIR_ECONOMY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/{series_id}"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_SERIES_URL = "https://data.bls.gov/timeseries/{series_id}"
BLS_SERIES_IDS = {
    "cpi": "CUSR0000SA0",
    "core_cpi": "CUSR0000SA0L1E",
    "ppi": "WPUFD4",
    "core_ppi": "WPUFD49116",
    "nonfarm_payrolls": "CES0000000001",
    "unemployment": "LNS14000000",
}
BLS_RELEASE_URLS = {
    "cpi": "https://www.bls.gov/news.release/cpi.htm",
    "ppi": "https://www.bls.gov/news.release/ppi.htm",
    "nonfarm_payrolls": "https://www.bls.gov/news.release/empsit.htm",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "Accept": "application/json,text/calendar,text/html;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    })
    return session


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")[:80]


def _event_id(publisher: str, title: str, reference_period: Optional[str], event_date: str) -> tuple[str, str]:
    key = f"{_slug(publisher)}:{_slug(title)}:{reference_period or event_date}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20], key


def _reference_period(text: str) -> Optional[str]:
    value = " ".join((text or "").split())
    months = "|".join(month_name[1:])
    match = re.search(rf"\b({months})\s+(20\d{{2}})\b", value, re.IGNORECASE)
    if match:
        month = list(month_name).index(match.group(1).title())
        return f"{match.group(2)}-{month:02d}"
    match = re.search(r"\b(?:Q|Quarter\s+)([1-4])\D+(20\d{2})\b", value, re.IGNORECASE)
    if match:
        return f"{match.group(2)}-Q{match.group(1)}"
    match = re.search(r"\b([1-4])(?:st|nd|rd|th)\s+Quarter\D+(20\d{2})\b", value, re.IGNORECASE)
    if match:
        return f"{match.group(2)}-Q{match.group(1)}"
    match = re.search(r"\b(20\d{2})\s*(?:Q|Quarter\s*)([1-4])\b", value, re.IGNORECASE)
    if match:
        return f"{match.group(1)}-Q{match.group(2)}"
    return None


def _classify(title: str) -> tuple[str, str, Optional[IndicatorSpec]]:
    spec = find_indicator(title)
    if spec:
        return spec.category, CATEGORY_NAMES[spec.category], spec
    lowered = title.lower()
    rules = (
        ("interest_rate", r"fomc|fed |interest rate|monetary policy"),
        ("inflation", r"inflation|price index|\bcpi\b|\bppi\b"),
        ("employment", r"employment|unemployment|jobless|payroll|jolts|labor"),
        ("growth", r"\bgdp\b|gross domestic|growth"),
        ("trade_manufacturing", r"retail|trade|manufacturing|industrial|durable|pmi|ism"),
        ("housing", r"housing|home sales|building permit"),
        ("energy", r"oil|petroleum|natural gas|eia"),
        ("bonds", r"treasury|bond|auction|yield"),
    )
    for category, pattern in rules:
        if re.search(pattern, lowered):
            return category, CATEGORY_NAMES[category], None
    return "general", CATEGORY_NAMES["general"], None


def make_event(
    *,
    publisher: str,
    source_url: str,
    title: str,
    scheduled: datetime | date,
    verification: str,
    raw_id: Optional[str] = None,
    reference_period: Optional[str] = None,
    impact: str = "medium",
    forecast: Optional[str] = None,
    previous: Optional[str] = None,
    actual: Optional[str] = None,
    unit: Optional[str] = None,
) -> dict[str, Any]:
    all_day = isinstance(scheduled, date) and not isinstance(scheduled, datetime)
    if all_day:
        event_date = scheduled.isoformat()
        event_time = None
        scheduled_at = None
    else:
        local = scheduled.astimezone(VN_TZ)
        event_date = local.date().isoformat()
        event_time = local.strftime("%H:%M")
        scheduled_at = scheduled.astimezone(timezone.utc).isoformat()
    reference_period = reference_period or _reference_period(title)
    event_id, event_key = _event_id(publisher, title, reference_period, event_date)
    category, category_name, spec = _classify(title)
    observed_at = utc_now_iso()
    impact_stars = 3 if impact.lower() == "high" or spec else 2 if impact.lower() == "medium" else 1
    return {
        "id": event_id,
        "event_key": event_key,
        "event_date": event_date,
        "event_time": event_time,
        "scheduled_at_utc": scheduled_at,
        "all_day": all_day,
        "country": "USD",
        "country_name": "Mỹ",
        "flag": "🇺🇸",
        "title": title,
        "title_vi": spec.title_vi if spec else f"{title} (Mỹ)",
        "indicator_key": spec.key if spec else None,
        "reference_period": reference_period,
        "category": category,
        "category_name_vi": category_name,
        "impact": impact.lower(),
        "impact_stars": impact_stars,
        "actual": actual,
        "forecast": forecast,
        "previous": previous,
        "unit": unit or (spec.unit if spec else None),
        "change_vs_previous": None,
        "source": publisher,
        "source_url": source_url,
        "observed_at": observed_at,
        "published_at": None,
        "as_of": observed_at,
        "verification": verification,
        "stale": False,
        "revision": 0,
        "overview_vi": spec.overview_vi if spec else "Sự kiện kinh tế Mỹ; theo dõi tác động đến thị trường tài chính.",
        "impact_analysis_vi": spec.impact_analysis_vi if spec else "Đánh giá tác động cần dựa trên nội dung công bố và bối cảnh nhiều chỉ báo.",
        "vn_market_impact_vi": spec.vn_market_impact_vi if spec else "Kênh truyền dẫn tới Việt Nam có thể gồm tỷ giá, lợi suất, thương mại và dòng vốn quốc tế.",
        "evidence": [{
            "publisher": publisher,
            "source_tier": "official" if verification == "official" else "aggregator",
            "url": source_url,
            "raw_id": raw_id,
            "observed_at": observed_at,
        }],
    }


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw.rstrip("\r"))
    return lines


def _parse_ics_datetime(value: str, params: str = "") -> datetime | date:
    clean = value.strip()
    if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", clean):
        return datetime.strptime(clean[:8], "%Y%m%d").date()
    if clean.endswith("Z"):
        return datetime.strptime(clean, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    tz_match = re.search(r"TZID=([^;:]+)", params)
    tz_name = tz_match.group(1) if tz_match else "America/New_York"
    aliases = {"US-Eastern": "America/New_York", "Eastern Standard Time": "America/New_York"}
    zone = ZoneInfo(aliases.get(tz_name, tz_name))
    return datetime.strptime(clean[:15], "%Y%m%dT%H%M%S").replace(tzinfo=zone)


def parse_bls_ics(text: str, start: date, end: date) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, tuple[str, str]] = {}
    for line in _unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            title = current.get("SUMMARY", ("", ""))[1].replace("\\,", ",")
            dt_params, dt_value = current.get("DTSTART", ("", ""))
            if title and dt_value:
                scheduled = _parse_ics_datetime(dt_value, dt_params)
                day = scheduled if isinstance(scheduled, date) and not isinstance(scheduled, datetime) else scheduled.astimezone(VN_TZ).date()
                if start <= day <= end:
                    events.append(make_event(
                        publisher="U.S. Bureau of Labor Statistics",
                        source_url=BLS_ICS_URL,
                        title=title,
                        scheduled=scheduled,
                        verification="official",
                        raw_id=current.get("UID", ("", None))[1],
                        reference_period=_reference_period(title),
                        impact="high" if find_indicator(title) else "medium",
                    ))
            current = {}
        elif current is not None and ":" in line:
            left, value = line.split(":", 1)
            key, _, params = left.partition(";")
            if key in {"SUMMARY", "DTSTART", "UID"}:
                current[key] = (params, value)
    return events


def fetch_bls_schedule(session: requests.Session, start: date, end: date) -> list[dict[str, Any]]:
    response = session.get(BLS_ICS_URL, timeout=12)
    response.raise_for_status()
    if "BEGIN:VCALENDAR" not in response.text or "BEGIN:VEVENT" not in response.text:
        raise ProviderError("BLS schedule response is not a valid event calendar")
    return parse_bls_ics(response.text, start, end)


def parse_fomc_html(html: str, start: date, end: date) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict[str, Any]] = []
    for panel in soup.select("div.panel.panel-default"):
        heading = panel.select_one("div.panel-heading")
        match = re.search(r"(20\d{2}) FOMC Meetings", heading.get_text(" ", strip=True) if heading else "")
        if not match:
            continue
        year = int(match.group(1))
        for row in panel.select("div.fomc-meeting"):
            month_el = row.select_one(".fomc-meeting__month")
            date_el = row.select_one(".fomc-meeting__date")
            if not month_el or not date_el:
                continue
            month_label = month_el.get_text(" ", strip=True).title()
            if month_label not in month_name:
                continue
            month = list(month_name).index(month_label)
            days = [int(item) for item in re.findall(r"\d+", date_el.get_text(" ", strip=True))]
            if not days:
                continue
            meeting_day = date(year, month, days[-1])
            if not start <= meeting_day <= end:
                continue
            statement = row.find("a", href=re.compile(r"pressreleases/monetary.*\.htm"))
            source_url = urljoin("https://www.federalreserve.gov", statement.get("href")) if statement else FOMC_CALENDAR_URL
            scheduled = datetime.combine(meeting_day, dt_time(14, 0), tzinfo=US_EASTERN)
            events.append(make_event(
                publisher="Board of Governors of the Federal Reserve System",
                source_url=source_url,
                title="FOMC Statement and Federal Funds Rate Decision",
                scheduled=scheduled,
                verification="official",
                raw_id=f"fomc-{meeting_day.isoformat()}",
                reference_period=meeting_day.strftime("%Y-%m-%d"),
                impact="high",
            ))
    return events


def fetch_fomc_schedule(session: requests.Session, start: date, end: date) -> list[dict[str, Any]]:
    response = session.get(FOMC_CALENDAR_URL, timeout=12)
    response.raise_for_status()
    return parse_fomc_html(response.text, start, end)


def parse_bea_html(html: str, start: date, end: date) -> list[dict[str, Any]]:
    """Parse BEA schedule tables/cards without depending on presentation classes."""
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict[str, Any]] = []
    for container in soup.select("tr.scheduled-releases-type-press, article, .release-row"):
        text = " ".join(container.get_text(" ", strip=True).split())
        if not re.search(r"GDP|Personal Income and Outlays", text, re.IGNORECASE):
            continue
        link = container.find("a", href=True)
        year_match = re.search(r"/news/(20\d{2})/", link.get("href", "") if link else "")
        date_el = container.select_one(".release-date")
        date_match = re.search(rf"\b({'|'.join(month_name[1:])})\s+(\d{{1,2}})\b", date_el.get_text(" ", strip=True) if date_el else text, re.IGNORECASE)
        if not date_match or not year_match:
            continue
        month = list(month_name).index(date_match.group(1).title())
        day, year = int(date_match.group(2)), int(year_match.group(1))
        release_day = date(year, month, day)
        if not start <= release_day <= end:
            continue
        title_el = container.select_one(".release-title")
        title = (title_el.get_text(" ", strip=True) if title_el else text).strip()[:220]
        time_el = container.select_one("small")
        time_match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", time_el.get_text(" ", strip=True) if time_el else "", re.IGNORECASE)
        hour, minute = 8, 30
        if time_match:
            hour, minute = int(time_match.group(1)) % 12, int(time_match.group(2))
            if time_match.group(3).upper() == "PM":
                hour += 12
        scheduled = datetime.combine(release_day, dt_time(hour, minute), tzinfo=US_EASTERN)
        events.append(make_event(
            publisher="U.S. Bureau of Economic Analysis",
            source_url=urljoin("https://www.bea.gov", link["href"]) if link else BEA_SCHEDULE_URL,
            title=title,
            scheduled=scheduled,
            verification="official",
            reference_period=_reference_period(title),
            impact="high",
        ))
    return events


def fetch_bea_schedule(session: requests.Session, start: date, end: date) -> list[dict[str, Any]]:
    response = session.get(BEA_SCHEDULE_URL, timeout=12)
    response.raise_for_status()
    return parse_bea_html(response.text, start, end)


def fetch_aggregator_schedule(session: requests.Session, start: date, end: date) -> list[dict[str, Any]]:
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]
    body: list[dict[str, Any]] = []
    for u in urls:
        try:
            resp = session.get(u, timeout=8)
            if resp.ok and resp.text:
                data = resp.json()
                if isinstance(data, list):
                    body.extend(data)
        except Exception:
            pass

    if not body:
        # If both fail, raise ValueError to let caller know
        raise ValueError("Không thể lấy dữ liệu lịch từ aggregator")

    def _clean_str(val: Any) -> Optional[str]:
        if val is None:
            return None
        s = str(val).strip()
        return s if s and s != "-" and s.lower() != "null" else None

    events: list[dict[str, Any]] = []
    seen_ids = set()
    for item in body:
        if str(item.get("country") or "").upper() != "USD":
            continue
        raw_date = str(item.get("date") or "")
        try:
            scheduled = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not start <= scheduled.astimezone(VN_TZ).date() <= end:
            continue

        raw_id = str(item.get("id") or "") or None
        if raw_id and raw_id in seen_ids:
            continue
        if raw_id:
            seen_ids.add(raw_id)

        fc = _clean_str(item.get("forecast"))
        pr = _clean_str(item.get("previous"))
        act = _clean_str(item.get("actual"))

        events.append(make_event(
            publisher="FairEconomy / ForexFactory",
            source_url=FAIR_ECONOMY_URL,
            title=str(item.get("title") or "").strip(),
            scheduled=scheduled,
            verification="aggregator",
            raw_id=raw_id,
            impact=str(item.get("impact") or "medium"),
            forecast=fc,
            previous=pr,
            actual=act,
        ))
    return [event for event in events if event["title"]]


def fetch_fred_observations(
    session: requests.Session,
    series_id: str,
    *,
    observation_start: Optional[str] = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if api_key:
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": max(limit, 2),
        }
        if observation_start:
            params["observation_start"] = observation_start
        response = session.get(FRED_API_URL, params=params, timeout=12)
        response.raise_for_status()
        raw = response.json().get("observations") or []
        rows = [{"date": item.get("date"), "value": item.get("value"), "realtime_start": item.get("realtime_start")} for item in raw]
    else:
        response = session.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": series_id},
            timeout=12,
        )
        response.raise_for_status()
        rows = [{"date": row.get("observation_date"), "value": row.get(series_id), "realtime_start": None} for row in csv.DictReader(io.StringIO(response.text))]
        if observation_start:
            rows = [row for row in rows if str(row.get("date") or "") >= observation_start]
        rows = list(reversed(rows[-max(limit, 2):]))
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            continue
        cleaned.append({"date": row["date"], "value": value, "realtime_start": row.get("realtime_start")})
    return sorted(cleaned, key=lambda item: item["date"])


def parse_bls_api(body: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    status = str(body.get("status") or "")
    if status != "REQUEST_SUCCEEDED":
        messages = "; ".join(body.get("message") or [])
        raise ValueError(messages or "BLS API request failed")
    parsed: dict[str, list[dict[str, Any]]] = {}
    for series in (body.get("Results") or {}).get("series") or []:
        series_id = str(series.get("seriesID") or "")
        rows: list[dict[str, Any]] = []
        for item in series.get("data") or []:
            period = str(item.get("period") or "")
            if not re.fullmatch(r"M(0[1-9]|1[0-2])", period):
                continue
            try:
                value = float(item.get("value"))
                year = int(item.get("year"))
                month = int(period[1:])
            except (TypeError, ValueError):
                continue
            rows.append({
                "date": f"{year:04d}-{month:02d}-01",
                "value": value,
                "realtime_start": None,
                "provider_series_id": series_id,
            })
        parsed[series_id] = sorted(rows, key=lambda row: row["date"])
    return parsed


def fetch_bls_observations(session: requests.Session, keys: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    selected = {key: BLS_SERIES_IDS[key] for key in keys if key in BLS_SERIES_IDS}
    if not selected:
        return {}
    current_year = datetime.now(timezone.utc).year
    payload: dict[str, Any] = {
        "seriesid": list(selected.values()),
        "startyear": str(current_year - 2),
        "endyear": str(current_year),
    }
    registration_key = os.getenv("BLS_API_KEY", "").strip()
    if registration_key:
        payload["registrationkey"] = registration_key
    response = session.post(BLS_API_URL, json=payload, timeout=15)
    response.raise_for_status()
    by_series = parse_bls_api(response.json())
    return {key: by_series.get(series_id, []) for key, series_id in selected.items()}


def fetch_bls_release_metadata(session: requests.Session) -> dict[str, dict[str, str]]:
    """Read the current official release date/reference period from BLS releases."""
    metadata: dict[str, dict[str, str]] = {}
    months = "|".join(month_name[1:])
    for key, url in BLS_RELEASE_URLS.items():
        response = session.get(url, timeout=15)
        response.raise_for_status()
        text = " ".join(BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True).split())
        release_match = re.search(
            rf"\(?(?:ET|EST|EDT)\)?\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday),\s+({months})\s+(\d{{1,2}}),\s+(20\d{{2}})",
            text,
            re.IGNORECASE,
        )
        if key == "cpi":
            period_match = re.search(rf"CONSUMER PRICE INDEX\s*[-–]\s*({months})\s+(20\d{{2}})", text, re.IGNORECASE)
        else:
            period_match = re.search(rf"THE EMPLOYMENT SITUATION\s*[-–]\s*({months})\s+(20\d{{2}})", text, re.IGNORECASE)
        if not release_match or not period_match:
            continue
        release_month = list(month_name).index(release_match.group(1).title())
        period_month = list(month_name).index(period_match.group(1).title())
        metadata[key] = {
            "released_on": f"{int(release_match.group(3)):04d}-{release_month:02d}-{int(release_match.group(2)):02d}",
            "reference_period": f"{int(period_match.group(2)):04d}-{period_month:02d}",
            "source_url": url,
        }
    return metadata


def transform_observations(spec: IndicatorSpec, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    source = list(sorted(rows, key=lambda item: item["date"]))
    result: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        raw_value = float(item["value"])
        value: Optional[float]
        if spec.transform == "percent_change":
            previous = float(source[index - 1]["value"]) if index else 0.0
            value = ((raw_value / previous) - 1.0) * 100.0 if previous else None
        elif spec.transform == "difference":
            value = raw_value - float(source[index - 1]["value"]) if index else None
        elif spec.transform == "thousands":
            value = raw_value / 1000.0
        else:
            value = raw_value
        if value is None:
            continue
        result.append({
            "period": item["date"],
            "value": round(value, 2),
            "unit": spec.unit,
            "published_at": item.get("realtime_start"),
            "source": spec.source_publisher,
            "source_url": FRED_SERIES_URL.format(series_id=spec.series_id),
            "series_id": spec.series_id,
        })
    return result
