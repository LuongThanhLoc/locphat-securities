from datetime import date, datetime, timezone

import pytest

import macro_calendar_engine as facade
from macro.providers import make_event, parse_bls_ics, parse_fomc_html, transform_observations
from macro.registry import INDICATORS
from macro.repository import MacroRepository
from macro.service import MacroService


@pytest.fixture()
def repository(tmp_path):
    return MacroRepository(database_url="", sqlite_path=str(tmp_path / "macro-v2.db"))


def promote(repository, events, *, fetched_at="2026-08-14T04:00:00+00:00", observations=None, tickers=None):
    repository.promote(
        events=events, observations=observations or {}, tickers=tickers or [],
        meta={
            "schema_version": 2, "started_at": fetched_at, "fetched_at": fetched_at,
            "official": sum(item.get("verification") == "official" for item in events),
            "aggregator": sum(item.get("verification") == "aggregator" for item in events),
            "rejected": 0, "coverage": {"accepted_events": len(events), "partial": False},
        },
    )


def official_event(day="2026-08-12", title="Consumer Price Index for July 2026"):
    return make_event(
        publisher="U.S. Bureau of Labor Statistics", source_url="https://www.bls.gov/cpi/",
        title=title, scheduled=datetime.fromisoformat(f"{day}T12:30:00+00:00"),
        verification="official", raw_id=f"bls-{day}", impact="high",
    )


def test_transformations_never_fill_missing_values():
    cpi = transform_observations(INDICATORS["cpi"], [
        {"date": "2026-06-01", "value": 100.0, "realtime_start": "2026-07-01"},
        {"date": "2026-07-01", "value": 99.5, "realtime_start": "2026-08-01"},
    ])
    payroll = transform_observations(INDICATORS["nonfarm_payrolls"], [
        {"date": "2026-06-01", "value": 160000.0}, {"date": "2026-07-01", "value": 160114.0},
    ])
    assert cpi[-1]["value"] == -0.5
    assert payroll[-1]["value"] == 114.0
    assert transform_observations(INDICATORS["cpi"], []) == []


def test_aggregator_event_discards_actual_and_forecast():
    event = make_event(
        publisher="FairEconomy / ForexFactory", source_url="https://example.test/feed", title="CPI m/m",
        scheduled=datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc), verification="aggregator",
    )
    assert event["actual"] is None and event["forecast"] is None
    assert event["verification"] == "aggregator"


def test_bls_ics_timezone_alias_and_reference_period():
    payload = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:cpi-2026\r
DTSTART;TZID=US-Eastern:20260812T083000\r
SUMMARY:Consumer Price Index for July 2026\r
END:VEVENT\r
END:VCALENDAR\r
"""
    events = parse_bls_ics(payload, date(2026, 8, 1), date(2026, 8, 31))
    assert len(events) == 1
    assert events[0]["event_time"] == "19:30"
    assert events[0]["reference_period"] == "2026-07"


def test_fomc_parser_uses_meeting_end_and_stable_reference():
    html = """<div class="panel panel-default"><div class="panel-heading"><h4>2026 FOMC Meetings</h4></div>
    <div class="row fomc-meeting"><div class="fomc-meeting__month">September</div>
    <div class="fomc-meeting__date">15-16*</div><a href="/newsevents/pressreleases/monetary20260916a.htm">HTML</a></div></div>"""
    events = parse_fomc_html(html, date(2026, 9, 1), date(2026, 9, 30))
    assert len(events) == 1
    assert events[0]["reference_period"] == "2026-09-16"


def test_repository_revision_and_active_dataset(repository):
    first = official_event()
    promote(repository, [first])
    changed = dict(first)
    changed["event_time"] = "20:30"
    changed["scheduled_at_utc"] = "2026-08-12T13:30:00+00:00"
    promote(repository, [changed], fetched_at="2026-08-14T05:00:00+00:00")
    active = repository.list_events("2026-08-01", "2026-08-31")
    assert len(active) == 1 and active[0]["revision"] == 1


def test_calendar_contract_filters_and_no_synthetic_data(repository):
    promote(repository, [official_event()])
    service = MacroService(repository)
    result = service.get_calendar("2026-08-01", "2026-08-31", country="USD", category="inflation")
    assert result["schema_version"] == 2 and result["no_synthetic_data"] is True
    assert result["data_quality"]["forecast_available"] is False
    assert result["events"][0]["actual"] is None
    with pytest.raises(ValueError): service.get_calendar("2026-01-01", "2026-12-31")
    with pytest.raises(ValueError): service.get_calendar("2026-08-01", "2026-08-31", country="EUR")
    with pytest.raises(ValueError): service.get_calendar("2026-08-01", "2026-08-31", search="x" * 101)


def test_event_detail_contains_history_and_evidence(repository):
    event = official_event(); event["actual"] = "0.2%"; event["actual_value"] = 0.2
    rows = [
        {"period": "2026-06-01", "value": 0.1, "unit": "% m/m", "series_id": "CPIAUCSL"},
        {"period": "2026-07-01", "value": 0.2, "unit": "% m/m", "series_id": "CPIAUCSL"},
    ]
    promote(repository, [event], observations={"cpi": rows})
    detail = MacroService(repository).get_event(event["id"])
    assert detail["history"] == rows
    assert detail["data_quality"]["has_official_actual"] is True


def test_ticker_response_is_wrapped_and_source_aware(repository):
    ticker = {
        "symbol": "US10Y", "value": 4.1, "value_display": "4.10", "source": "FRED",
        "source_url": "https://fred.stlouisfed.org/series/DGS10", "as_of": "2026-08-13",
        "stale": False, "verification": "official_aggregator",
    }
    promote(repository, [official_event()], tickers=[ticker])
    result = MacroService(repository).get_tickers()
    assert result["schema_version"] == 2 and result["items"][0]["source"] == "FRED"


def test_ics_uses_utc_and_all_day_semantics(repository, monkeypatch):
    timed = official_event()
    all_day = make_event(
        publisher="U.S. Bureau of Economic Analysis", source_url="https://bea.gov/",
        title="GDP, 2nd Quarter 2026", scheduled=date(2026, 8, 26), verification="official",
    )
    promote(repository, [timed, all_day])
    monkeypatch.setattr(facade, "get_service", lambda: MacroService(repository))
    content = facade.export_macro_ics("2026-08-01", "2026-08-31")
    assert "DTSTART:20260812T123000Z" in content
    assert "DTSTART;VALUE=DATE:20260826" in content
    assert "DTEND;VALUE=DATE:20260827" in content
    assert "DTSTAMP:" in content and "Dự báo" not in content


def test_audit_detects_actual_without_official_verification(repository):
    event = make_event(
        publisher="FairEconomy / ForexFactory", source_url="https://example.test/feed", title="CPI m/m",
        scheduled=datetime(2026, 8, 12, tzinfo=timezone.utc), verification="aggregator",
    )
    event["actual"] = "0.2%"
    promote(repository, [event])
    assert repository.audit()["events_with_actual_without_official_evidence"] == 1
