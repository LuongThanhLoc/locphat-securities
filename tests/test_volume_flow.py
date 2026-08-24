import contextlib
import inspect
import threading
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as application
import volume_flow_store
from volume_flow_engine import (
    NormalizedVolumeFlowDataset,
    QUALITY_VERSION,
    VietcapVolumeFlowSource,
    VolumeFlowService,
    VolumeFlowSourceError,
    VolumeFlowSymbolNotFound,
    VolumeFlowUnavailable,
)


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def session_row(day, seed=1, proprietary_record=True):
    foreign_buy_volume = 1_000 * seed
    foreign_sell_volume = 700 * seed
    foreign_buy_value = 20_000_000 * seed
    foreign_sell_value = 14_000_000 * seed
    prop_buy_volume = 500 * seed if proprietary_record else None
    prop_sell_volume = 300 * seed if proprietary_record else None
    prop_buy_value = 10_000_000 * seed if proprietary_record else None
    prop_sell_value = 6_000_000 * seed if proprietary_record else None
    return {
        "symbol": "FPT", "trading_date": day,
        "open_price": 100 + seed, "high_price": 104 + seed,
        "low_price": 99 + seed, "close_price": 103 + seed,
        "market_volume": 100_000 * seed, "market_value": 2_000_000_000 * seed,
        "foreign_buy_volume": foreign_buy_volume,
        "foreign_sell_volume": foreign_sell_volume,
        "foreign_net_volume": foreign_buy_volume - foreign_sell_volume,
        "foreign_buy_value": foreign_buy_value,
        "foreign_sell_value": foreign_sell_value,
        "foreign_net_value": foreign_buy_value - foreign_sell_value,
        "foreign_ytd_net_volume": 30_000 + (foreign_buy_volume - foreign_sell_volume),
        "foreign_ytd_net_value": 600_000_000 + (foreign_buy_value - foreign_sell_value),
        "proprietary_buy_volume": prop_buy_volume,
        "proprietary_sell_volume": prop_sell_volume,
        "proprietary_net_volume": prop_buy_volume - prop_sell_volume if proprietary_record else None,
        "proprietary_buy_value": prop_buy_value,
        "proprietary_sell_value": prop_sell_value,
        "proprietary_net_value": prop_buy_value - prop_sell_value if proprietary_record else None,
        "proprietary_source_record": proprietary_record,
        "is_final": True, "source": "test", "response_hash": f"hash-{day}",
        "source_updated_at": f"{day}T17:00:00", "fetched_at": f"{day}T17:01:00+00:00",
    }


class MemoryStore:
    def __init__(self, rows=None, state=None):
        self.rows = {"FPT": deepcopy(rows or [])}
        self.states = {"FPT": deepcopy(state)} if state else {}
        self.upserts = 0
        self.failures = []
        self.read_after_upsert = False
        self._guard = threading.Lock()

    def load_sessions(self, symbol, limit=20):
        with self._guard:
            if self.upserts:
                self.read_after_upsert = True
            rows = sorted(deepcopy(self.rows.get(symbol, [])), key=lambda row: row["trading_date"])
            return rows[-limit:]

    def load_state(self, symbol):
        with self._guard:
            return deepcopy(self.states.get(symbol))

    @contextlib.contextmanager
    def sync_lock(self, symbol):
        yield True

    def upsert_sessions(self, symbol, sessions, **metadata):
        with self._guard:
            self.upserts += 1
            merged = {row["trading_date"]: deepcopy(row) for row in self.rows.get(symbol, [])}
            merged.update({row["trading_date"]: deepcopy(row) for row in sessions})
            self.rows[symbol] = sorted(merged.values(), key=lambda row: row["trading_date"])[-20:]
            self.states[symbol] = {
                "symbol": symbol,
                "company_name": metadata["company_name"],
                "exchange": metadata["exchange"],
                "final_cutoff_date": metadata["final_cutoff_date"],
                "session_count": len(self.rows[symbol]),
                "last_success_at": "2026-08-24T10:00:00+00:00",
                "quality_status": "valid",
                "quality_version": metadata["quality_version"],
                "foreign_ytd_start_date": metadata["foreign_ytd_start_date"],
                "foreign_ytd_session_count": metadata["foreign_ytd_session_count"],
                "foreign_ytd_complete": metadata["foreign_ytd_complete"],
                "foreign_ytd_calculation": metadata["foreign_ytd_calculation"],
            }

    def record_failure(self, symbol, error):
        self.failures.append((symbol, error))
        if symbol in self.states:
            self.states[symbol]["quality_status"] = "stale"


class ChartMemoryStore(MemoryStore):
    def __init__(self, rows=None, state=None, chart_rows=None, chart_state=None):
        super().__init__(rows, state)
        self.chart_rows = {"FPT": deepcopy(chart_rows or [])}
        self.chart_states = {"FPT": deepcopy(chart_state)} if chart_state else {}
        self.chart_upserts = 0

    def load_price_chart(self, symbol, limit=900):
        return deepcopy(self.chart_rows.get(symbol, []))[-limit:]

    def load_price_chart_state(self, symbol):
        return deepcopy(self.chart_states.get(symbol))

    def upsert_price_chart(self, symbol, sessions, **metadata):
        self.chart_upserts += 1
        merged = {row["trading_date"]: deepcopy(row) for row in self.chart_rows.get(symbol, [])}
        merged.update({row["trading_date"]: deepcopy(row) for row in sessions})
        self.chart_rows[symbol] = [
            row for day, row in sorted(merged.items())
            if day >= metadata["retention_start_date"]
        ]
        self.chart_states[symbol] = {
            "symbol": symbol, "exchange": metadata["exchange"],
            "final_cutoff_date": metadata["final_cutoff_date"],
            "retention_start_date": metadata["retention_start_date"],
            "first_session": self.chart_rows[symbol][0]["trading_date"],
            "last_session": self.chart_rows[symbol][-1]["trading_date"],
            "session_count": len(self.chart_rows[symbol]),
            "last_source": metadata["source"], "last_success_at": "2026-08-24T10:00:00Z",
            "quality_status": "valid", "quality_version": metadata["quality_version"],
        }

    def record_price_chart_failure(self, symbol, error):
        if symbol in self.chart_states:
            self.chart_states[symbol]["quality_status"] = "stale"


class FakeSource:
    def __init__(self, rows, *, cutoff="2026-08-21", error=None, delay=0):
        self.rows = deepcopy(rows)
        self.cutoff = cutoff
        self.error = error
        self.delay = delay
        self.build_calls = 0
        self.live_calls = 0
        self.price_chart_calls = 0

    def resolve_security(self, symbol):
        if symbol == "ZZZZ":
            raise VolumeFlowSymbolNotFound("Không tìm thấy mã ZZZZ")
        return {"symbol": symbol, "company_name": "Công ty Cổ phần FPT", "exchange": "HOSE"}

    def latest_finalized_session(self):
        if self.error == "cutoff":
            raise VolumeFlowSourceError("cutoff unavailable")
        return self.cutoff

    def build_final_sessions(self, symbol, cutoff):
        self.build_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error == "history":
            raise VolumeFlowSourceError("history unavailable")
        return NormalizedVolumeFlowDataset(
            sessions=deepcopy(self.rows),
            foreign_ytd_start_date="2026-01-05",
            foreign_ytd_session_count=157,
            foreign_ytd_complete=True,
        )

    def live_session(self, symbol, latest_finalized):
        self.live_calls += 1
        if self.error == "live":
            raise VolumeFlowSourceError("price board unavailable")
        return {
            "date": "2026-08-24", "observed_at": "2026-08-24T10:00:00Z",
            "source_session": "CONTINUOUS", "exchange": "HOSE",
            "open": 104, "high": 109, "low": 102, "close": 108,
            "market_volume": 900_000, "market_value": 97_000_000_000,
            "foreign": {
                "buy_volume": 10, "sell_volume": 4, "net_volume": 6,
                "buy_value": 1_000, "sell_value": 400, "net_value": 600,
                "ytd_net_volume": None, "ytd_net_value": None,
            },
            "proprietary": {
                "buy_volume": None, "sell_volume": None, "net_volume": None,
                "buy_value": None, "sell_value": None, "net_value": None,
                "source_record": False, "record_status": "not_yet_published",
            },
            "is_provisional": True, "source": "test-board",
        }

    def build_price_chart_sessions(self, symbol, cutoff):
        self.price_chart_calls += 1
        if self.error == "price_chart":
            raise VolumeFlowSourceError("chart history unavailable")
        result = []
        for row in self.rows:
            result.append({
                "symbol": symbol, "trading_date": row["trading_date"],
                "open_price": row["open_price"], "high_price": row["high_price"],
                "low_price": row["low_price"], "close_price": row["close_price"],
                "volume": row["market_volume"], "price_basis": "unadjusted",
                "source": "test", "response_hash": row["response_hash"],
                "source_updated_at": row["source_updated_at"],
            })
        return result, "2023-08-21"


def raw_price(day, seed=1):
    buy_volume, sell_volume = 1_000 * seed, 600 * seed
    buy_value, sell_value = 20_000_000 * seed, 12_000_000 * seed
    return {
        "ticker": "FPT", "tradingDate": f"{day}T00:00:00", "endTradingDate": f"{day}T00:00:00",
        "openPrice": 100, "highestPrice": 105, "lowestPrice": 99, "closePrice": 104,
        "totalVolume": 100_000, "totalValue": 2_000_000_000,
        "foreignBuyVolumeTotal": buy_volume, "foreignSellVolumeTotal": sell_volume,
        "foreignNetVolumeTotal": buy_volume - sell_volume,
        "foreignBuyValueTotal": buy_value, "foreignSellValueTotal": sell_value,
        "foreignNetValueTotal": buy_value - sell_value,
    }


def raw_proprietary(day):
    return {
        "ticker": "FPT", "tradingDate": f"{day}T00:00:00", "updateDate": f"{day}T17:00:00",
        "totalBuyTradeVolume": 500, "totalSellTradeVolume": 300, "totalTradeNetVolume": 200,
        "totalBuyTradeValue": 10_000_000, "totalSellTradeValue": 6_000_000,
        "totalTradeNetValue": 4_000_000,
    }


def raw_board(day="2026-08-24"):
    return [{
        "listingInfo": {"symbol": "FPT", "board": "HSX", "tradingDate": day},
        "matchPrice": {
            "matchPrice": 71_400, "openPrice": 72_500, "highest": 72_700,
            "lowest": 71_400, "accumulatedVolume": 4_611_900,
            "accumulatedValue": 331_439.68,
            "foreignBuyVolume": 448_347, "foreignSellVolume": 946_000,
            "foreignBuyValue": 32_234_708_000, "foreignSellValue": 68_088_510_000,
            "session": "ENDED", "receivedTime": "2026-08-24T08:33:08.841Z",
        },
    }]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self.payload)


def test_adapter_aligns_to_price_calendar_and_marks_sparse_proprietary_rows():
    source = VietcapVolumeFlowSource()
    prices = [raw_price("2026-08-19", 1), raw_price("2026-08-20", 2), raw_price("2026-08-21", 3)]
    with patch.object(source, "fetch_price_history", return_value={"rows": prices, "last": True}), \
         patch.object(source, "fetch_proprietary_history", return_value=[raw_proprietary("2026-08-20")]):
        dataset = source.build_final_sessions("FPT", "2026-08-21")
        rows = dataset.sessions

    assert [row["trading_date"] for row in rows] == ["2026-08-19", "2026-08-20", "2026-08-21"]
    assert rows[0]["proprietary_source_record"] is False
    assert rows[0]["proprietary_net_value"] is None
    assert rows[1]["proprietary_source_record"] is True
    assert rows[1]["proprietary_net_value"] == 4_000_000
    assert dataset.foreign_ytd_complete is True
    assert rows[-1]["foreign_ytd_net_value"] == 48_000_000


def test_adapter_rejects_inconsistent_net_instead_of_inventing_a_value():
    source = VietcapVolumeFlowSource()
    bad = raw_price("2026-08-21")
    bad["foreignNetValueTotal"] = 123
    with patch.object(source, "fetch_price_history", return_value={"rows": [bad], "last": True}), \
         patch.object(source, "fetch_proprietary_history", return_value=[]), \
         pytest.raises(VolumeFlowSourceError, match="ròng = mua - bán"):
        source.build_final_sessions("FPT", "2026-08-21")


def test_live_adapter_preserves_exact_board_values_and_never_estimates_net():
    source = VietcapVolumeFlowSource()
    with patch.object(source.session, "post", return_value=FakeResponse(raw_board())):
        row = source.live_session("FPT", "2026-08-21")

    assert row["date"] == "2026-08-24"
    assert row["exchange"] == "HOSE"
    assert row["market_value"] == 331_439_680_000
    assert row["foreign"]["net_volume"] == 448_347 - 946_000
    assert row["foreign"]["net_value"] == 32_234_708_000 - 68_088_510_000
    assert row["proprietary"]["net_value"] is None
    assert row["is_provisional"] is True


def test_live_adapter_rejects_old_future_negative_and_invalid_ohlc_snapshots():
    source = VietcapVolumeFlowSource()
    with patch.object(source.session, "post", return_value=FakeResponse(raw_board())):
        assert source.live_session("FPT", "2026-08-24") is None

    invalid = raw_board()
    invalid[0]["matchPrice"]["foreignSellVolume"] = -1
    with patch.object(source.session, "post", return_value=FakeResponse(invalid)), \
         pytest.raises(VolumeFlowSourceError, match="không được âm"):
        source.live_session("FPT", "2026-08-21")


def test_price_chart_adapter_pages_and_uses_only_unadjusted_prices():
    source = VietcapVolumeFlowSource()
    recent = raw_price("2026-08-21")
    recent.update({"openPriceAdjusted": 1, "highestPriceAdjusted": 2,
                   "lowestPriceAdjusted": 1, "closePriceAdjusted": 2})
    boundary = raw_price("2023-08-21")
    older = raw_price("2023-08-18")
    body = {"data": {"content": [recent, boundary, older], "last": True}}
    with patch.object(source, "_get", return_value=body):
        rows, retention_start = source.build_price_chart_sessions("FPT", "2026-08-21")

    assert retention_start == "2023-08-21"
    assert [row["trading_date"] for row in rows] == ["2023-08-21", "2026-08-21"]
    assert rows[-1]["open_price"] == recent["openPrice"]
    assert rows[-1]["close_price"] == recent["closePrice"]
    assert rows[-1]["price_basis"] == "unadjusted"


def test_price_chart_adapter_rejects_duplicate_and_invalid_ohlc():
    source = VietcapVolumeFlowSource()
    duplicate = raw_price("2026-08-21")
    body = {"data": {"content": [duplicate, deepcopy(duplicate)], "last": True}}
    with patch.object(source, "_get", return_value=body), \
         pytest.raises(VolumeFlowSourceError, match="trùng ngày"):
        source.build_price_chart_sessions("FPT", "2026-08-21")

    invalid = raw_price("2026-08-21")
    invalid["highestPrice"] = 50
    body = {"data": {"content": [invalid], "last": True}}
    with patch.object(source, "_get", return_value=body), \
         pytest.raises(VolumeFlowSourceError, match="OHLC lịch sử giá"):
        source.build_price_chart_sessions("FPT", "2026-08-21")

    invalid = raw_board()
    invalid[0]["matchPrice"]["highest"] = 70_000
    with patch.object(source.session, "post", return_value=FakeResponse(invalid)), \
         pytest.raises(VolumeFlowSourceError, match="OHLC realtime"):
        source.live_session("FPT", "2026-08-21")


def test_adapter_rejects_duplicate_source_sessions():
    source = VietcapVolumeFlowSource()
    duplicated = [raw_price("2026-08-21"), raw_price("2026-08-21")]
    with patch.object(source, "fetch_price_history", return_value={"rows": duplicated, "last": True}), \
         patch.object(source, "fetch_proprietary_history", return_value=[]), \
         pytest.raises(VolumeFlowSourceError, match="phiên trùng ngày"):
        source.build_final_sessions("FPT", "2026-08-21")


def test_adapter_requires_a_year_boundary_before_exposing_ytd():
    source = VietcapVolumeFlowSource()
    prices = [raw_price("2026-01-05", 1), raw_price("2026-01-06", 2)]
    with patch.object(source, "fetch_price_history", return_value={"rows": prices, "last": False}), \
         patch.object(source, "fetch_proprietary_history", return_value=[]):
        dataset = source.build_final_sessions("FPT", "2026-01-06")

    assert dataset.foreign_ytd_complete is False
    assert dataset.foreign_ytd_start_date is None
    assert dataset.foreign_ytd_session_count == 0
    assert all(row["foreign_ytd_net_value"] is None for row in dataset.sessions)


def test_adapter_sums_ytd_only_from_current_year_source_sessions():
    source = VietcapVolumeFlowSource()
    prices = [
        raw_price("2026-01-06", 2),
        raw_price("2026-01-05", 1),
        raw_price("2025-12-31", 9),
    ]
    with patch.object(source, "fetch_price_history", return_value={"rows": prices, "last": False}), \
         patch.object(source, "fetch_proprietary_history", return_value=[]):
        dataset = source.build_final_sessions("FPT", "2026-01-06")

    assert dataset.foreign_ytd_complete is True
    assert dataset.foreign_ytd_start_date == "2026-01-05"
    assert dataset.foreign_ytd_session_count == 2
    current_year = [row for row in dataset.sessions if row["trading_date"].startswith("2026-")]
    assert [row["foreign_ytd_net_value"] for row in current_year] == [8_000_000, 24_000_000]
    assert current_year[-1]["foreign_ytd_net_volume"] == 1_200


def test_explicit_zero_proprietary_record_is_not_converted_to_missing():
    source = VietcapVolumeFlowSource()
    prop = raw_proprietary("2026-08-21")
    for field in (
        "totalBuyTradeVolume", "totalSellTradeVolume", "totalTradeNetVolume",
        "totalBuyTradeValue", "totalSellTradeValue", "totalTradeNetValue",
    ):
        prop[field] = 0
    with patch.object(source, "fetch_price_history", return_value={"rows": [raw_price("2026-08-21")], "last": True}), \
         patch.object(source, "fetch_proprietary_history", return_value=[prop]):
        row = source.build_final_sessions("FPT", "2026-08-21").sessions[0]

    assert row["proprietary_source_record"] is True
    assert row["proprietary_net_value"] == 0


def test_service_refreshes_then_reads_back_from_database_and_returns_contract():
    rows = [session_row(f"2026-08-{day:02d}", day) for day in range(1, 22)]
    store = MemoryStore()
    source = FakeSource(rows)
    payload = VolumeFlowService(store, source).get_overview("fpt")

    assert store.upserts == 1
    assert store.read_after_upsert is True
    assert payload["sync"]["served_from"] == "database"
    assert payload["sync"]["refreshed"] is True
    assert payload["coverage_count"] == 20
    assert len(store.rows["FPT"]) == 20
    assert payload["sessions"][0]["date"] == "2026-08-02"
    assert payload["summary"]["foreign"]["period"]["net_value"] > 0
    assert payload["live_foreign"]["is_provisional"] is True


def test_service_does_not_aggregate_or_zero_fill_incomplete_proprietary_data():
    rows = [
        session_row("2026-08-20", 1),
        session_row("2026-08-21", 2, proprietary_record=False),
    ]
    payload = VolumeFlowService(MemoryStore(), FakeSource(rows)).get_overview("FPT")

    proprietary = payload["summary"]["proprietary"]
    assert proprietary["latest"]["net_value"] is None
    assert proprietary["period"]["net_value"] is None
    assert proprietary["period"]["coverage_count"] == 1
    assert proprietary["period"]["complete"] is False
    assert payload["sessions"][-1]["proprietary"]["record_status"] == "missing_source_record"


def test_live_service_polls_only_matching_exchange_and_calculates_provisional_ytd():
    rows = [session_row("2026-08-21", 2)]
    state = {
        "company_name": "Công ty Cổ phần FPT", "exchange": "HOSE",
        "final_cutoff_date": "2026-08-21", "session_count": 1,
        "quality_version": QUALITY_VERSION, "quality_status": "valid",
        "foreign_ytd_complete": True, "foreign_ytd_start_date": "2026-01-05",
        "foreign_ytd_session_count": 157, "foreign_ytd_calculation": "source_sessions_sum",
    }
    source = FakeSource(rows)
    open_market = lambda: {"exchange_sessions": {
        "HOSE": {"is_matching": True}, "HNX": {"is_matching": False},
    }}
    store = MemoryStore(rows, state)
    service = VolumeFlowService(store, source, market_session_provider=open_market)
    payload = service.get_live_overview("FPT")

    assert payload["poll_enabled"] is True
    assert payload["poll_after_seconds"] == 5
    assert payload["live_session"]["foreign"]["ytd_net_value"] == \
        rows[-1]["foreign_ytd_net_value"] + 600
    assert payload["live_session"]["proprietary"]["net_value"] is None
    assert store.upserts == 0

    # Four-second RAM cache prevents duplicate upstream calls for the same ticker/EOD.
    service.get_live_overview("FPT")
    assert source.live_calls == 1

    closed = VolumeFlowService(
        MemoryStore(rows, state), FakeSource(rows),
        market_session_provider=lambda: {"exchange_sessions": {"HOSE": {"is_matching": False}}},
    ).get_live_overview("FPT")
    assert closed["poll_enabled"] is False
    assert closed["poll_after_seconds"] is None
    assert closed["live_session"]["status"] == "provisional_after_close"


def test_initial_live_failure_keeps_eod_and_in_session_retry_contract():
    rows = [session_row("2026-08-21", 2)]
    state = {
        "company_name": "Công ty Cổ phần FPT", "exchange": "HOSE",
        "final_cutoff_date": "2026-08-21", "session_count": 1,
        "quality_version": QUALITY_VERSION, "quality_status": "valid",
        "foreign_ytd_complete": True,
    }
    payload = VolumeFlowService(
        MemoryStore(rows, state), FakeSource(rows, error="live"),
        market_session_provider=lambda: {"exchange_sessions": {"HOSE": {"is_matching": True}}},
    ).get_overview("FPT")

    assert payload["sessions"][-1]["date"] == "2026-08-21"
    assert payload["live"]["live_session"] is None
    assert payload["live"]["poll_enabled"] is True
    assert payload["live"]["poll_after_seconds"] == 5
    assert "realtime" in payload["sync"]["warnings"][-1]


def test_price_chart_service_refreshes_reads_database_and_reuses_fresh_cache():
    flow_rows = [session_row("2026-08-20", 1), session_row("2026-08-21", 2)]
    flow_state = {
        "company_name": "Công ty Cổ phần FPT", "exchange": "HOSE",
        "final_cutoff_date": "2026-08-21", "session_count": 2,
        "quality_version": QUALITY_VERSION, "quality_status": "valid",
    }
    store = ChartMemoryStore(flow_rows, flow_state)
    source = FakeSource(flow_rows)
    service = VolumeFlowService(store, source)

    first = service.get_price_chart("FPT")
    second = service.get_price_chart("FPT")

    assert store.chart_upserts == 1
    assert source.price_chart_calls == 1
    assert first["sync"]["served_from"] == "database"
    assert first["price_basis"] == "unadjusted"
    assert first["coverage_count"] == 2
    assert second["sync"]["refreshed"] is False


def test_complete_or_short_but_checked_database_cache_does_not_refetch():
    rows = [session_row(f"2026-08-{day:02d}", day) for day in range(18, 22)]
    state = {
        "company_name": "Công ty Cổ phần FPT", "exchange": "HOSE",
        "final_cutoff_date": "2026-08-21", "session_count": 4,
        "last_success_at": "2026-08-21T17:00:00+00:00", "quality_status": "valid",
        "quality_version": QUALITY_VERSION, "foreign_ytd_complete": True,
        "foreign_ytd_start_date": "2026-01-05", "foreign_ytd_session_count": 157,
        "foreign_ytd_calculation": "source_sessions_sum",
    }
    store = MemoryStore(rows, state)
    source = FakeSource(rows)
    payload = VolumeFlowService(store, source).get_overview("FPT")

    assert source.build_calls == 0
    assert payload["data_status"] == "partial_history"
    assert payload["coverage_count"] == 4


def test_source_failure_serves_cached_rows_as_stale_and_empty_cache_fails_closed():
    rows = [session_row("2026-08-21")]
    state = {
        "company_name": "Công ty Cổ phần FPT", "exchange": "HOSE",
        "final_cutoff_date": "2026-08-20", "session_count": 1,
        "last_success_at": "2026-08-21T17:00:00+00:00", "quality_status": "valid",
        "quality_version": QUALITY_VERSION,
    }
    cached = VolumeFlowService(MemoryStore(rows, state), FakeSource(rows, error="history")).get_overview("FPT")
    assert cached["sync"]["stale"] is True
    assert cached["sessions"][0]["date"] == "2026-08-21"

    with pytest.raises(VolumeFlowUnavailable):
        VolumeFlowService(MemoryStore(), FakeSource(rows, error="history")).get_overview("FPT")


def test_local_symbol_lock_prevents_duplicate_concurrent_refreshes():
    rows = [session_row("2026-08-21")]
    store = MemoryStore()
    source = FakeSource(rows, delay=.08)
    service = VolumeFlowService(store, source)
    results = []

    threads = [threading.Thread(target=lambda: results.append(service.get_overview("FPT"))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert source.build_calls == 1
    assert store.upserts == 1


def test_postgres_contract_uses_exact_types_primary_key_and_per_symbol_retention():
    schema = volume_flow_store.SCHEMA_SQL
    upsert_source = inspect.getsource(volume_flow_store.PostgresVolumeFlowStore.upsert_sessions)
    assert "PRIMARY KEY (symbol, trading_date)" in schema
    assert "foreign_buy_volume BIGINT" in schema
    assert "foreign_buy_value NUMERIC(24, 0)" in schema
    assert "foreign_ytd_net_value NUMERIC(24, 0)" in schema
    assert "CREATE TABLE IF NOT EXISTS price_chart_daily" in schema
    assert "CHECK (price_basis = 'unadjusted')" in schema
    assert "CREATE TABLE IF NOT EXISTS price_chart_sync_state" in schema
    assert "proprietary_source_record BOOLEAN" in schema
    assert "WHERE proprietary_source_record = FALSE" in schema
    assert "WHERE symbol = %s AND trading_date NOT IN" in upsert_source
    assert "ORDER BY trading_date DESC LIMIT 20" in upsert_source


def test_volume_flow_database_url_precedence(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared/database")
    monkeypatch.setenv("VOLUME_FLOW_DATABASE_URL", "postgresql://module/database")

    assert volume_flow_store.PostgresVolumeFlowStore().database_url == \
        "postgresql://module/database"
    assert volume_flow_store.PostgresVolumeFlowStore(
        "postgresql://explicit/database"
    ).database_url == "postgresql://explicit/database"


def test_volume_flow_database_url_falls_back_to_shared_database(monkeypatch):
    monkeypatch.delenv("VOLUME_FLOW_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared/database")

    assert volume_flow_store.PostgresVolumeFlowStore().database_url == \
        "postgresql://shared/database"


def test_volume_flow_database_url_missing_has_clear_error(monkeypatch):
    monkeypatch.delenv("VOLUME_FLOW_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        volume_flow_store.VolumeFlowStoreUnavailable,
        match="VOLUME_FLOW_DATABASE_URL hoặc DATABASE_URL",
    ):
        volume_flow_store.PostgresVolumeFlowStore()


def test_postgres_sql_failure_is_normalized_to_store_unavailable():
    class BrokenConnection:
        def cursor(self):
            raise RuntimeError("database restarted")

    class BrokenStore(volume_flow_store.PostgresVolumeFlowStore):
        @contextlib.contextmanager
        def _connect(self):
            yield BrokenConnection()

    store = BrokenStore("postgresql://local/test")
    with pytest.raises(volume_flow_store.VolumeFlowStoreUnavailable, match="đọc lịch sử"):
        store.load_sessions("FPT")


def test_api_maps_symbol_and_storage_failures_without_authentication():
    client = TestClient(application.app)

    class MissingService:
        def get_overview(self, symbol):
            raise VolumeFlowSymbolNotFound("Không tìm thấy mã")

    class UnavailableService:
        def get_overview(self, symbol):
            raise VolumeFlowUnavailable("Nguồn và database chưa sẵn sàng")

    with patch.object(application, "get_volume_flow_service", return_value=MissingService()):
        assert client.get("/api/volume-overview/ZZZZ").status_code == 404
    with patch.object(application, "get_volume_flow_service", return_value=UnavailableService()):
        assert client.get("/api/volume-overview/FPT").status_code == 503

    class LiveService:
        def get_live_overview(self, symbol):
            return {"symbol": symbol, "poll_enabled": False, "live_session": None}

    with patch.object(application, "get_volume_flow_service", return_value=LiveService()):
        response = client.get("/api/volume-overview/FPT/live")
        assert response.status_code == 200
        assert response.json()["poll_enabled"] is False
        assert "no-store" in response.headers["cache-control"]

    class PriceChartService:
        def get_price_chart(self, symbol):
            return {"symbol": symbol, "price_basis": "unadjusted", "sessions": []}

    with patch.object(application, "get_volume_flow_service", return_value=PriceChartService()):
        response = client.get("/api/price-chart/FPT")
        assert response.status_code == 200
        assert response.json()["price_basis"] == "unadjusted"


def test_frontend_route_navigation_charts_and_responsive_contract():
    html = (STATIC / "volume-overview.html").read_text(encoding="utf-8")
    css = (STATIC / "volume-overview.css").read_text(encoding="utf-8")
    script = (STATIC / "volume-overview.js").read_text(encoding="utf-8")
    nav = (STATIC / "site-nav.js").read_text(encoding="utf-8")
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "Tổng quan KLGD" in nav
    assert "href: '/tong-quan-klgd'" in nav
    assert "path.startsWith('/tong-quan-klgd')" in nav
    assert '@app.get("/tong-quan-klgd"' in app_source
    assert '@app.get("/api/volume-overview/{symbol}"' in app_source
    assert '@app.get("/api/volume-overview/{symbol}/live"' in app_source
    assert 'id="volumeSymbolInput"' in html
    assert 'id="participantTabs"' in html and 'id="metricTabs"' in html
    assert "lightweight-charts.standalone.production.js" in html
    assert "apexcharts.min.js" in html
    assert "shared-price-chart.js" in html
    assert 'id="volumeSharedPriceChart"' in html
    shared_chart = (STATIC / "shared-price-chart.js").read_text(encoding="utf-8")
    assert "LightweightCharts.createChart" in shared_chart
    assert "new ApexCharts" in script
    assert "proprietary.source_record" in script
    assert "ytd_net_${suffix}" in script
    assert "Lũy kế YTD" in html
    assert "NET trong ngày" in html
    assert "Mua, bán và ròng lũy kế" not in html
    assert "served_from" in script
    assert "viewport-fit=cover" in html
    assert "env(safe-area-inset-bottom)" in css
    assert "clamp(" in css and "dvh" in css
    assert "@media (max-width: 1180px)" in css
    assert "@media (max-width: 860px)" in css
    assert "@media (max-width: 640px)" in css
    assert "@media (max-width: 380px)" in css
    assert "prefers-reduced-motion" in css
    assert "theme: { mode: 'light' }" in script
    assert 'id="volumeManualRefresh"' in html
    assert "poll_after_seconds" in script
    assert "document.hidden" in script and "visibilitychange" in script
    assert "/live`" in script
    assert "displaySessions" in script
    assert "window.setTimeout" in script
    assert "/api/price-chart/" in script
    assert "window.LPPriceChart.create" in script


def test_site_nav_synchronization_and_cache_busting_headers():
    from fastapi.testclient import TestClient
    import app as application

    client = TestClient(application.app)

    # Test cache-busting headers on static nav assets
    for asset_path in ("/static/site-nav.js", "/static/site-nav.css", "/static/site-nav-search.css", "/static/site-nav-ai.css"):
        res = client.get(asset_path)
        assert res.status_code == 200
        assert "no-store" in res.headers.get("cache-control", "")
        assert "etag" in res.headers

    # Verify all HTML entrypoints reference site-nav.js
    html_files = [
        "index.html",
        "heatmap.html",
        "bubbles.html",
        "volume-overview.html",
        "macro.html",
        "calendar.html",
        "bottom-indicator.html",
        "backtest.html",
        "rrg.html",
        "watchlist.html",
    ]
    for filename in html_files:
        content = (STATIC / filename).read_text(encoding="utf-8")
        assert "/static/site-nav.js?v=20260824_volume_v2" in content, f"Missing fresh site-nav.js in {filename}"
        assert "/static/site-nav.css?v=20260824_volume_v2" in content, f"Missing fresh site-nav.css in {filename}"
