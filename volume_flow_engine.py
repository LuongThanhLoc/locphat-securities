"""Verified Vietcap adapter and cache-aside service for Tổng quan KLGD."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

import requests

from volume_flow_store import (
    PostgresVolumeFlowStore,
    VolumeFlowStoreUnavailable,
    get_volume_flow_store,
)


SOURCE_NAME = "Vietcap public REST"
QUALITY_VERSION = "volume-flow-quality-v2-ytd"
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,5}$")
LIVE_CACHE_SECONDS = 4.0
PRICE_CHART_QUALITY_VERSION = "price-chart-v1-unadjusted"
PRICE_CHART_PAGE_SIZE = 400
PRICE_CHART_MAX_PAGES = 4


class VolumeFlowSourceError(RuntimeError):
    """The upstream source was unavailable or returned invalid financial data."""


class VolumeFlowSymbolNotFound(ValueError):
    """The requested ticker is not an active Vietnamese listed security."""


class VolumeFlowUnavailable(RuntimeError):
    """Neither a fresh source response nor a durable cached response is available."""


@dataclass(frozen=True)
class NormalizedVolumeFlowDataset:
    sessions: list[dict[str, Any]]
    foreign_ytd_start_date: Optional[str]
    foreign_ytd_session_count: int
    foreign_ytd_complete: bool
    foreign_ytd_calculation: str = "source_sessions_sum"


def _date_only(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise VolumeFlowSourceError("Nguồn dữ liệu thiếu ngày giao dịch")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError as exc:
            raise VolumeFlowSourceError(f"Ngày giao dịch không hợp lệ: {raw}") from exc
    return parsed.date().isoformat()


def _required_int(row: dict[str, Any], field: str, *, nonnegative: bool = False) -> int:
    value = row.get(field)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise VolumeFlowSourceError(f"Thiếu hoặc sai trường {field}") from exc
    if not math.isfinite(number) or abs(number - round(number)) > 0.01:
        raise VolumeFlowSourceError(f"Trường {field} không phải số nguyên hợp lệ")
    result = int(round(number))
    if nonnegative and result < 0:
        raise VolumeFlowSourceError(f"Trường {field} không được âm")
    return result


def _required_price(row: dict[str, Any], field: str) -> float:
    try:
        number = float(row.get(field))
    except (TypeError, ValueError) as exc:
        raise VolumeFlowSourceError(f"Thiếu hoặc sai trường giá {field}") from exc
    if not math.isfinite(number) or number <= 0:
        raise VolumeFlowSourceError(f"Giá {field} không hợp lệ")
    return number


def _required_scaled_int(
    row: dict[str, Any], field: str, *, scale: int, nonnegative: bool = False
) -> int:
    try:
        number = Decimal(str(row.get(field))) * scale
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise VolumeFlowSourceError(f"Thiếu hoặc sai trường {field}") from exc
    integral = number.to_integral_value()
    if number != integral:
        raise VolumeFlowSourceError(f"Trường {field} không quy đổi được sang số nguyên")
    result = int(integral)
    if nonnegative and result < 0:
        raise VolumeFlowSourceError(f"Trường {field} không được âm")
    return result


def _market_session_snapshot() -> dict[str, Any]:
    """Reuse the exchange-aware clock maintained by the Heatmap module."""
    from heatmap_engine import get_market_session

    return get_market_session()


def _three_year_start(cutoff: str) -> str:
    value = date.fromisoformat(cutoff)
    try:
        return value.replace(year=value.year - 3).isoformat()
    except ValueError:  # 29/02 -> 28/02
        return value.replace(year=value.year - 3, day=28).isoformat()


def _validate_net(buy: int, sell: int, reported: int, field: str) -> None:
    if (buy - sell) != reported:
        raise VolumeFlowSourceError(f"Dữ liệu {field} không thỏa ròng = mua - bán")


class VietcapVolumeFlowSource:
    """Small isolated adapter for the public Vietcap endpoints used by this page."""

    IQ_BASE = "https://iq.vietcap.com.vn/api/iq-insight-service/v1"
    IQ_SEARCH = "https://iq.vietcap.com.vn/api/iq-insight-service/v2/company/search-bar"
    PRICE_BOARD = "https://trading.vietcap.com.vn/api/price/symbols/getList"

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 10):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
            "User-Agent": "Mozilla/5.0 (compatible; LocPhatSecurities/1.0)",
        })
        self.timeout = timeout
        self._cutoff_cache: tuple[float, Optional[str]] = (0.0, None)
        self._security_cache: tuple[float, dict[str, dict[str, str]]] = (0.0, {})
        self._cache_lock = threading.Lock()

    def _get(self, url: str, *, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise VolumeFlowSourceError(f"Không kết nối được nguồn Vietcap: {exc}") from exc
        if not isinstance(body, dict) or body.get("successful") is False or body.get("status") != 200:
            message = body.get("msg") if isinstance(body, dict) else "Phản hồi không hợp lệ"
            raise VolumeFlowSourceError(f"Vietcap trả lỗi: {message}")
        return body

    @staticmethod
    def _content(body: dict[str, Any]) -> list[dict[str, Any]]:
        data = body.get("data") or {}
        rows = data.get("content") if isinstance(data, dict) else data
        return [row for row in (rows or []) if isinstance(row, dict)]

    def resolve_security(self, symbol: str) -> dict[str, str]:
        now = time.monotonic()
        with self._cache_lock:
            cached_at, securities = self._security_cache
            if now - cached_at < 21_600 and securities:
                result = securities.get(symbol.upper())
                if result:
                    return dict(result)
                raise VolumeFlowSymbolNotFound(f"Không tìm thấy mã {symbol.upper()}")
        body = self._get(self.IQ_SEARCH, params={"language": 1})
        data = body.get("data") or []
        supported_exchanges = {"HOSE", "HNX", "UPCOM"}
        common_equity_types = {"CT", "NH", "CK", "BH"}
        securities = {}
        for item in data if isinstance(data, list) else []:
            exchange = str(item.get("floor") or "").upper()
            security_type = str(item.get("comTypeCode") or "").upper()
            code = str(item.get("code") or "").upper().strip()
            if code and exchange in supported_exchanges and security_type in common_equity_types:
                securities[code] = {
                    "symbol": code,
                    "company_name": str(item.get("name") or code).strip(),
                    "exchange": exchange,
                }
        with self._cache_lock:
            self._security_cache = (now, securities)
        result = securities.get(symbol.upper())
        if not result:
            raise VolumeFlowSymbolNotFound(f"Không tìm thấy mã {symbol.upper()}")
        return dict(result)

    def latest_finalized_session(self) -> str:
        now = time.monotonic()
        with self._cache_lock:
            cached_at, cached_value = self._cutoff_cache
            if cached_value and now - cached_at < 300:
                return cached_value
        body = self._get(
            f"{self.IQ_BASE}/market-indices/proprietary-history",
            params={"index": "VNINDEX", "page": 0, "size": 1},
        )
        rows = self._content(body)
        if not rows:
            raise VolumeFlowSourceError("Chưa xác định được phiên EOD đã hoàn tất")
        cutoff = _date_only(rows[0].get("tradingDate"))
        if cutoff > date.today().isoformat():
            raise VolumeFlowSourceError("Nguồn trả về phiên giao dịch trong tương lai")
        with self._cache_lock:
            self._cutoff_cache = (now, cutoff)
        return cutoff

    def fetch_price_history(self, symbol: str) -> dict[str, Any]:
        body = self._get(
            f"{self.IQ_BASE}/company/{symbol}/price-history",
            params={"page": 0, "size": 400},
        )
        data = body.get("data") or {}
        return {
            "rows": self._content(body),
            "last": bool(data.get("last")) if isinstance(data, dict) else False,
            "number_of_elements": int(data.get("numberOfElements") or 0) if isinstance(data, dict) else 0,
        }

    def build_price_chart_sessions(
        self, symbol: str, cutoff: str
    ) -> tuple[list[dict[str, Any]], str]:
        retention_start = _three_year_start(cutoff)
        source_rows: list[dict[str, Any]] = []
        crossed_start = False
        for page in range(PRICE_CHART_MAX_PAGES):
            body = self._get(
                f"{self.IQ_BASE}/company/{symbol}/price-history",
                params={"page": page, "size": PRICE_CHART_PAGE_SIZE},
            )
            data = body.get("data") or {}
            rows = self._content(body)
            if not rows:
                break
            source_rows.extend(rows)
            dates = [_date_only(row.get("tradingDate")) for row in rows]
            crossed_start = min(dates) <= retention_start
            if crossed_start or (isinstance(data, dict) and data.get("last")):
                break
        if not source_rows:
            raise VolumeFlowSymbolNotFound(f"Không có lịch sử giá cho mã {symbol}")
        if not crossed_start:
            oldest = min(_date_only(row.get("tradingDate")) for row in source_rows)
            # A newly listed symbol may legitimately expose its entire shorter history.
            if len(source_rows) >= PRICE_CHART_PAGE_SIZE * PRICE_CHART_MAX_PAGES:
                raise VolumeFlowSourceError(
                    f"Nguồn chưa phủ đủ ba năm lịch sử giá; phiên cũ nhất {oldest}"
                )

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in source_rows:
            if str(raw.get("ticker") or "").upper() != symbol:
                continue
            day = _date_only(raw.get("tradingDate"))
            if day < retention_start or day > cutoff:
                continue
            if day in seen:
                raise VolumeFlowSourceError(f"Lịch sử giá có phiên trùng ngày {day}")
            seen.add(day)
            open_price = _required_price(raw, "openPrice")
            high_price = _required_price(raw, "highestPrice")
            low_price = _required_price(raw, "lowestPrice")
            close_price = _required_price(raw, "closePrice")
            if high_price < max(open_price, close_price) or low_price > min(open_price, close_price):
                raise VolumeFlowSourceError(f"OHLC lịch sử giá không hợp lệ tại {day}")
            volume = _required_int(raw, "totalVolume", nonnegative=True)
            canonical = {
                "symbol": symbol,
                "trading_date": day,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price,
                "close_price": close_price,
                "volume": volume,
                "price_basis": "unadjusted",
                "source": "Vietcap price-history",
                "source_updated_at": raw.get("endTradingDate") or raw.get("tradingDate"),
            }
            canonical["response_hash"] = hashlib.sha256(
                json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":")).encode()
            ).hexdigest()
            normalized.append(canonical)
        normalized.sort(key=lambda row: row["trading_date"])
        if not normalized:
            raise VolumeFlowSourceError(f"Không có phiên giá hợp lệ trong ba năm cho {symbol}")
        return normalized, retention_start

    def fetch_proprietary_history(self, symbol: str) -> list[dict[str, Any]]:
        body = self._get(
            f"{self.IQ_BASE}/company/{symbol}/proprietary-history",
            params={"page": 0, "size": 60},
        )
        return self._content(body)

    def build_final_sessions(self, symbol: str, cutoff: str) -> NormalizedVolumeFlowDataset:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="volume-flow") as executor:
            price_future = executor.submit(self.fetch_price_history, symbol)
            prop_future = executor.submit(self.fetch_proprietary_history, symbol)
            price_page = price_future.result()
            prop_rows = prop_future.result()
        price_rows = price_page.get("rows") or []
        if not price_rows:
            raise VolumeFlowSymbolNotFound(f"Không có lịch sử giao dịch cho mã {symbol}")

        proprietary_by_date: dict[str, dict[str, Any]] = {}
        for raw in prop_rows:
            if str(raw.get("ticker") or "").upper() != symbol:
                continue
            day = _date_only(raw.get("tradingDate"))
            if day <= cutoff:
                proprietary_by_date[day] = raw

        cutoff_year = cutoff[:4]
        year_start = f"{cutoff_year}-01-01"
        seen_dates: set[str] = set()
        current_year_source_positions: list[int] = []
        prior_year_source_positions: list[int] = []
        normalized: list[dict[str, Any]] = []
        for source_position, price in enumerate(price_rows):
            if str(price.get("ticker") or "").upper() != symbol:
                continue
            day = _date_only(price.get("tradingDate"))
            if day > cutoff:
                continue
            if day in seen_dates:
                raise VolumeFlowSourceError(f"Nguồn có phiên trùng ngày {day}")
            seen_dates.add(day)
            if day < year_start:
                prior_year_source_positions.append(source_position)
                # A prior-year row is only evidence that the descending source
                # page crossed the year boundary. Its OHLC/flow fields are not
                # inputs to this year's YTD calculation and are not persisted.
                continue
            current_year_source_positions.append(source_position)

            open_price = _required_price(price, "openPrice")
            high_price = _required_price(price, "highestPrice")
            low_price = _required_price(price, "lowestPrice")
            close_price = _required_price(price, "closePrice")
            if high_price < max(open_price, close_price) or low_price > min(open_price, close_price):
                raise VolumeFlowSourceError(f"OHLC không hợp lệ tại phiên {day}")

            foreign_buy_volume = _required_int(price, "foreignBuyVolumeTotal", nonnegative=True)
            foreign_sell_volume = _required_int(price, "foreignSellVolumeTotal", nonnegative=True)
            foreign_net_volume = _required_int(price, "foreignNetVolumeTotal")
            foreign_buy_value = _required_int(price, "foreignBuyValueTotal", nonnegative=True)
            foreign_sell_value = _required_int(price, "foreignSellValueTotal", nonnegative=True)
            foreign_net_value = _required_int(price, "foreignNetValueTotal")
            _validate_net(foreign_buy_volume, foreign_sell_volume, foreign_net_volume, "khối ngoại/KL")
            _validate_net(foreign_buy_value, foreign_sell_value, foreign_net_value, "khối ngoại/GT")

            prop = proprietary_by_date.get(day)
            if prop:
                prop_buy_volume = _required_int(prop, "totalBuyTradeVolume", nonnegative=True)
                prop_sell_volume = _required_int(prop, "totalSellTradeVolume", nonnegative=True)
                prop_net_volume = _required_int(prop, "totalTradeNetVolume")
                prop_buy_value = _required_int(prop, "totalBuyTradeValue", nonnegative=True)
                prop_sell_value = _required_int(prop, "totalSellTradeValue", nonnegative=True)
                prop_net_value = _required_int(prop, "totalTradeNetValue")
                _validate_net(prop_buy_volume, prop_sell_volume, prop_net_volume, "tự doanh/KL")
                _validate_net(prop_buy_value, prop_sell_value, prop_net_value, "tự doanh/GT")
            else:
                prop_buy_volume = prop_sell_volume = prop_net_volume = None
                prop_buy_value = prop_sell_value = prop_net_value = None

            # Store derived values only after exact equality with the source's
            # reported NET fields has been proven.
            foreign_net_volume = foreign_buy_volume - foreign_sell_volume
            foreign_net_value = foreign_buy_value - foreign_sell_value
            if prop:
                prop_net_volume = prop_buy_volume - prop_sell_volume
                prop_net_value = prop_buy_value - prop_sell_value

            canonical = {
                "symbol": symbol,
                "trading_date": day,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price,
                "close_price": close_price,
                "market_volume": _required_int(price, "totalVolume", nonnegative=True),
                "market_value": _required_int(price, "totalValue", nonnegative=True),
                "foreign_buy_volume": foreign_buy_volume,
                "foreign_sell_volume": foreign_sell_volume,
                "foreign_net_volume": foreign_net_volume,
                "foreign_buy_value": foreign_buy_value,
                "foreign_sell_value": foreign_sell_value,
                "foreign_net_value": foreign_net_value,
                "foreign_ytd_net_volume": None,
                "foreign_ytd_net_value": None,
                "proprietary_buy_volume": prop_buy_volume,
                "proprietary_sell_volume": prop_sell_volume,
                "proprietary_net_volume": prop_net_volume,
                "proprietary_buy_value": prop_buy_value,
                "proprietary_sell_value": prop_sell_value,
                "proprietary_net_value": prop_net_value,
                "proprietary_source_record": prop is not None,
                "is_final": True,
                "source": SOURCE_NAME,
                "source_updated_at": (prop or price).get("updateDate") or price.get("endTradingDate"),
            }
            normalized.append(canonical)

        normalized.sort(key=lambda row: row["trading_date"])
        if not normalized:
            raise VolumeFlowSourceError(f"Không có phiên đã chốt cho mã {symbol}")

        ytd_rows = list(normalized)
        has_prior_year_boundary = bool(
            current_year_source_positions
            and prior_year_source_positions
            and max(current_year_source_positions) < min(prior_year_source_positions)
        )
        source_history_exhausted = bool(price_page.get("last"))
        ytd_complete = bool(ytd_rows and (has_prior_year_boundary or source_history_exhausted))
        ytd_start_date = ytd_rows[0]["trading_date"] if ytd_complete else None
        if ytd_complete:
            cumulative_volume = 0
            cumulative_value = 0
            for row in ytd_rows:
                cumulative_volume += int(row["foreign_net_volume"])
                cumulative_value += int(row["foreign_net_value"])
                row["foreign_ytd_net_volume"] = cumulative_volume
                row["foreign_ytd_net_value"] = cumulative_value

        # The audit hash covers the final canonical row, including persisted
        # YTD values (or their verified absence).
        for row in normalized:
            row["response_hash"] = hashlib.sha256(
                json.dumps(row, sort_keys=True, default=str, separators=(",", ":")).encode()
            ).hexdigest()

        return NormalizedVolumeFlowDataset(
            sessions=normalized[-20:],
            foreign_ytd_start_date=ytd_start_date,
            foreign_ytd_session_count=len(ytd_rows) if ytd_complete else 0,
            foreign_ytd_complete=ytd_complete,
        )

    def live_session(self, symbol: str, latest_finalized: Optional[str]) -> Optional[dict[str, Any]]:
        try:
            response = self.session.post(
                self.PRICE_BOARD,
                json={"symbols": [symbol]},
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise VolumeFlowSourceError(f"Không lấy được bảng giá realtime: {exc}") from exc
        if not isinstance(body, list) or not body:
            return None
        listing = body[0].get("listingInfo") or {}
        match = body[0].get("matchPrice") or {}
        returned_symbol = str(listing.get("symbol") or match.get("symbol") or "").upper().strip()
        if returned_symbol != symbol:
            raise VolumeFlowSourceError(f"Bảng giá trả sai mã {returned_symbol or '--'} cho {symbol}")
        trading_date = _date_only(listing.get("tradingDate"))
        if trading_date > date.today().isoformat():
            raise VolumeFlowSourceError("Bảng giá realtime trả ngày trong tương lai")
        if latest_finalized and trading_date <= latest_finalized:
            return None
        market_volume = _required_int(match, "accumulatedVolume", nonnegative=True)
        if market_volume == 0:
            return None
        open_price = _required_price(match, "openPrice")
        high_price = _required_price(match, "highest")
        low_price = _required_price(match, "lowest")
        close_price = _required_price(match, "matchPrice")
        if high_price < max(open_price, close_price) or low_price > min(open_price, close_price):
            raise VolumeFlowSourceError(f"OHLC realtime không hợp lệ tại phiên {trading_date}")
        market_value = _required_scaled_int(
            match, "accumulatedValue", scale=1_000_000, nonnegative=True
        )
        buy_volume = _required_int(match, "foreignBuyVolume", nonnegative=True)
        sell_volume = _required_int(match, "foreignSellVolume", nonnegative=True)
        buy_value = _required_int(match, "foreignBuyValue", nonnegative=True)
        sell_value = _required_int(match, "foreignSellValue", nonnegative=True)
        raw_exchange = str(listing.get("board") or "").upper()
        exchange = "HOSE" if raw_exchange == "HSX" else raw_exchange
        return {
            "date": trading_date,
            "observed_at": match.get("receivedTime") or listing.get("receivedTime"),
            "source_session": str(match.get("session") or "UNKNOWN"),
            "exchange": exchange,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "market_volume": market_volume,
            "market_value": market_value,
            "foreign": {
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "net_volume": buy_volume - sell_volume,
                "buy_value": buy_value,
                "sell_value": sell_value,
                "net_value": buy_value - sell_value,
                "ytd_net_volume": None,
                "ytd_net_value": None,
            },
            "proprietary": {
                "buy_volume": None,
                "sell_volume": None,
                "net_volume": None,
                "buy_value": None,
                "sell_value": None,
                "net_value": None,
                "source_record": False,
                "record_status": "not_yet_published",
            },
            "is_provisional": True,
            "source": "Vietcap public price board",
        }

    def live_foreign(self, symbol: str, latest_finalized: Optional[str]) -> Optional[dict[str, Any]]:
        """Backward-compatible projection retained for existing API consumers."""
        live = self.live_session(symbol, latest_finalized)
        if not live:
            return None
        foreign = live["foreign"]
        return {
            "trading_date": live["date"],
            "as_of": live["observed_at"],
            "session": live["source_session"],
            "buy_volume": foreign["buy_volume"],
            "sell_volume": foreign["sell_volume"],
            "net_volume": foreign["net_volume"],
            "buy_value": foreign["buy_value"],
            "sell_value": foreign["sell_value"],
            "net_value": foreign["net_value"],
            "is_provisional": True,
            "source": live["source"],
        }


class VolumeFlowService:
    def __init__(
        self,
        store: PostgresVolumeFlowStore,
        source: Optional[VietcapVolumeFlowSource] = None,
        market_session_provider: Optional[Callable[[], dict[str, Any]]] = None,
    ):
        self.store = store
        self.source = source or VietcapVolumeFlowSource()
        self._symbol_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._market_session_provider = market_session_provider or _market_session_snapshot
        self._live_cache: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}
        self._live_locks: dict[str, threading.Lock] = {}
        self._live_guard = threading.Lock()

    def _local_lock(self, symbol: str) -> threading.Lock:
        with self._locks_guard:
            return self._symbol_locks.setdefault(symbol, threading.Lock())

    def _live_lock(self, symbol: str) -> threading.Lock:
        with self._live_guard:
            return self._live_locks.setdefault(symbol, threading.Lock())

    def _cached_live_session(
        self, symbol: str, latest_finalized: str
    ) -> Optional[dict[str, Any]]:
        cache_key = f"{symbol}:{latest_finalized}"
        now = time.monotonic()
        cached = self._live_cache.get(cache_key)
        if cached and now - cached[0] < LIVE_CACHE_SECONDS:
            return json.loads(json.dumps(cached[1])) if cached[1] is not None else None
        with self._live_lock(symbol):
            now = time.monotonic()
            cached = self._live_cache.get(cache_key)
            if cached and now - cached[0] < LIVE_CACHE_SECONDS:
                return json.loads(json.dumps(cached[1])) if cached[1] is not None else None
            live = self.source.live_session(symbol, latest_finalized)
            self._live_cache[cache_key] = (now, live)
            return json.loads(json.dumps(live)) if live is not None else None

    @staticmethod
    def _exchange_poll_state(
        exchange: str, market_session: dict[str, Any]
    ) -> tuple[bool, Optional[int]]:
        normalized = "HOSE" if exchange.upper() == "HSX" else exchange.upper()
        exchange_state = (market_session.get("exchange_sessions") or {}).get(normalized) or {}
        enabled = bool(exchange_state.get("is_matching"))
        return enabled, 5 if enabled else None

    def _build_live_payload(
        self,
        symbol: str,
        sessions: list[dict[str, Any]],
        state: Optional[dict[str, Any]],
        security: dict[str, str],
    ) -> dict[str, Any]:
        if not sessions:
            raise VolumeFlowUnavailable("Chưa có dữ liệu EOD làm mốc cho realtime")
        try:
            market_session = self._market_session_provider() or {}
        except Exception as exc:
            raise VolumeFlowSourceError(f"Không xác định được trạng thái phiên: {exc}") from exc
        poll_enabled, poll_after_seconds = self._exchange_poll_state(
            security["exchange"], market_session
        )
        live = self._cached_live_session(symbol, sessions[-1]["trading_date"])
        if live:
            if live["exchange"] and live["exchange"] != security["exchange"]:
                raise VolumeFlowSourceError(
                    f"Sàn realtime {live['exchange']} không khớp metadata {security['exchange']}"
                )
            ytd_complete = bool((state or {}).get("foreign_ytd_complete"))
            last_ytd_volume = sessions[-1].get("foreign_ytd_net_volume")
            last_ytd_value = sessions[-1].get("foreign_ytd_net_value")
            if ytd_complete and last_ytd_volume is not None and last_ytd_value is not None:
                live["foreign"]["ytd_net_volume"] = int(last_ytd_volume) + int(
                    live["foreign"]["net_volume"]
                )
                live["foreign"]["ytd_net_value"] = int(last_ytd_value) + int(
                    live["foreign"]["net_value"]
                )
            live["status"] = "live" if poll_enabled else "provisional_after_close"
            live["display_label"] = (
                "Tạm tính trong phiên" if poll_enabled else "Tạm tính sau đóng cửa"
            )
        return {
            "schema_version": 1,
            "symbol": symbol,
            "exchange": security["exchange"],
            "official_eod_date": sessions[-1]["trading_date"],
            "market_session": market_session,
            "poll_enabled": poll_enabled,
            "poll_after_seconds": poll_after_seconds,
            "live_session": live,
            "source": "Vietcap public price board",
        }

    def get_live_overview(self, raw_symbol: str) -> dict[str, Any]:
        symbol = str(raw_symbol or "").upper().strip()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise VolumeFlowSymbolNotFound("Mã cổ phiếu không hợp lệ")
        sessions = self.store.load_sessions(symbol)
        state = self.store.load_state(symbol)
        security = self._security_from_state(symbol, state)
        if security is None:
            security = self.source.resolve_security(symbol)
        return self._build_live_payload(symbol, sessions, state, security)

    def get_price_chart(self, raw_symbol: str) -> dict[str, Any]:
        symbol = str(raw_symbol or "").upper().strip()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise VolumeFlowSymbolNotFound("Mã cổ phiếu không hợp lệ")
        rows = self.store.load_price_chart(symbol)
        chart_state = self.store.load_price_chart_state(symbol)
        security = self._security_from_state(symbol, self.store.load_state(symbol))
        if security is None:
            security = self.source.resolve_security(symbol)

        stale = False
        refreshed = False
        warnings: list[str] = []
        cutoff: Optional[str] = None
        try:
            cutoff = self.source.latest_finalized_session()
        except VolumeFlowSourceError as exc:
            if not rows:
                raise VolumeFlowUnavailable(str(exc)) from exc
            stale = True
            warnings.append("Không xác minh được phiên EOD mới nhất; đang dùng lịch sử giá đã lưu.")

        is_current = bool(
            cutoff and chart_state and rows
            and chart_state.get("final_cutoff_date") == cutoff
            and chart_state.get("last_session") == rows[-1]["trading_date"]
            and int(chart_state.get("session_count") or 0) == len(rows)
            and chart_state.get("quality_version") == PRICE_CHART_QUALITY_VERSION
        )
        if cutoff and not is_current:
            with self._local_lock(f"price-chart:{symbol}"):
                rows = self.store.load_price_chart(symbol)
                chart_state = self.store.load_price_chart_state(symbol)
                is_current = bool(
                    chart_state and rows
                    and chart_state.get("final_cutoff_date") == cutoff
                    and chart_state.get("last_session") == rows[-1]["trading_date"]
                    and int(chart_state.get("session_count") or 0) == len(rows)
                    and chart_state.get("quality_version") == PRICE_CHART_QUALITY_VERSION
                )
                if not is_current:
                    with self.store.sync_lock(f"price-chart:{symbol}") as acquired:
                        if acquired:
                            # Another worker may have completed the same symbol while
                            # this request was waiting for PostgreSQL's advisory lock.
                            rows = self.store.load_price_chart(symbol)
                            chart_state = self.store.load_price_chart_state(symbol)
                            already_refreshed = bool(
                                chart_state and rows
                                and chart_state.get("final_cutoff_date") == cutoff
                                and chart_state.get("last_session") == rows[-1]["trading_date"]
                                and int(chart_state.get("session_count") or 0) == len(rows)
                                and chart_state.get("quality_version") == PRICE_CHART_QUALITY_VERSION
                            )
                            if not already_refreshed:
                                try:
                                    sessions, retention_start = self.source.build_price_chart_sessions(symbol, cutoff)
                                    self.store.upsert_price_chart(
                                        symbol, sessions,
                                        final_cutoff_date=cutoff,
                                        retention_start_date=retention_start,
                                        exchange=security["exchange"],
                                        source="Vietcap price-history",
                                        quality_version=PRICE_CHART_QUALITY_VERSION,
                                    )
                                    refreshed = True
                                except (VolumeFlowSourceError, VolumeFlowSymbolNotFound) as exc:
                                    try:
                                        self.store.record_price_chart_failure(symbol, str(exc))
                                    except VolumeFlowStoreUnavailable:
                                        pass
                                    if not rows:
                                        raise VolumeFlowUnavailable(str(exc)) from exc
                                    stale = True
                                    warnings.append("Nguồn Vietcap tạm thời lỗi; đang dùng lịch sử giá đã lưu.")
                        else:
                            rows = self.store.load_price_chart(symbol)
                            if not rows:
                                raise VolumeFlowUnavailable("Lịch sử giá đang được đồng bộ, vui lòng thử lại sau")
                            stale = True
                            warnings.append("Một tiến trình khác đang cập nhật lịch sử giá; đang dùng dữ liệu đã lưu.")
                    rows = self.store.load_price_chart(symbol)
                    chart_state = self.store.load_price_chart_state(symbol)

        if not rows:
            raise VolumeFlowUnavailable("Chưa có lịch sử giá đã lưu cho mã cổ phiếu này")
        if chart_state and chart_state.get("quality_status") == "stale" and not refreshed:
            stale = True
        return {
            "schema_version": 1,
            "symbol": symbol,
            "exchange": security["exchange"],
            "price_basis": "unadjusted",
            "price_basis_label": "Giá giao dịch thực tế chưa điều chỉnh",
            "first_session": rows[0]["trading_date"],
            "last_session": rows[-1]["trading_date"],
            "coverage_count": len(rows),
            "sessions": [{
                "date": row["trading_date"],
                "open": row["open_price"], "high": row["high_price"],
                "low": row["low_price"], "close": row["close_price"],
                "volume": row["volume"],
            } for row in rows],
            "sync": {
                "served_from": "database", "refreshed": refreshed, "stale": stale,
                "final_cutoff_date": cutoff or (chart_state or {}).get("final_cutoff_date"),
                "last_success_at": (chart_state or {}).get("last_success_at"),
                "warnings": warnings,
            },
        }

    @staticmethod
    def _security_from_state(symbol: str, state: Optional[dict[str, Any]]) -> Optional[dict[str, str]]:
        if state and state.get("company_name") and state.get("exchange"):
            return {
                "symbol": symbol,
                "company_name": str(state["company_name"]),
                "exchange": str(state["exchange"]),
            }
        return None

    @staticmethod
    def _is_current(state: Optional[dict[str, Any]], cutoff: str, sessions: list[dict[str, Any]]) -> bool:
        return bool(
            state and sessions and state.get("final_cutoff_date") == cutoff
            and int(state.get("session_count") or 0) == len(sessions)
            and state.get("quality_version") == QUALITY_VERSION
        )

    @staticmethod
    def _participant_summary(sessions: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
        latest = sessions[-1]
        if prefix == "proprietary":
            reported = [row for row in sessions if row.get("proprietary_source_record")]
        else:
            reported = sessions
        complete = len(reported) == len(sessions)
        buy_volume = sum(int(row[f"{prefix}_buy_volume"]) for row in reported)
        sell_volume = sum(int(row[f"{prefix}_sell_volume"]) for row in reported)
        buy_value = sum(int(row[f"{prefix}_buy_value"]) for row in reported)
        sell_value = sum(int(row[f"{prefix}_sell_value"]) for row in reported)
        net_values = [int(row[f"{prefix}_net_value"]) for row in reported]
        latest_available = prefix != "proprietary" or bool(latest.get("proprietary_source_record"))
        return {
            "latest": {
                "buy_volume": latest[f"{prefix}_buy_volume"] if latest_available else None,
                "sell_volume": latest[f"{prefix}_sell_volume"] if latest_available else None,
                "net_volume": latest[f"{prefix}_net_volume"] if latest_available else None,
                "buy_value": latest[f"{prefix}_buy_value"] if latest_available else None,
                "sell_value": latest[f"{prefix}_sell_value"] if latest_available else None,
                "net_value": latest[f"{prefix}_net_value"] if latest_available else None,
                "source_record": latest_available,
            },
            "period": {
                "buy_volume": buy_volume if complete else None,
                "sell_volume": sell_volume if complete else None,
                "net_volume": buy_volume - sell_volume if complete else None,
                "buy_value": buy_value if complete else None,
                "sell_value": sell_value if complete else None,
                "net_value": buy_value - sell_value if complete else None,
                "buy_sell_volume_ratio": round(buy_volume / sell_volume, 4) if complete and sell_volume else None,
                "buy_sell_value_ratio": round(buy_value / sell_value, 4) if complete and sell_value else None,
                "net_buy_sessions": sum(value > 0 for value in net_values) if complete else None,
                "net_sell_sessions": sum(value < 0 for value in net_values) if complete else None,
                "flat_sessions": sum(value == 0 for value in net_values) if complete else None,
                "coverage_count": len(reported),
                "target_count": len(sessions),
                "complete": complete,
            },
        }

    @staticmethod
    def _public_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        public = []
        for row in sessions:
            public.append({
                "date": row["trading_date"],
                "open": row["open_price"],
                "high": row["high_price"],
                "low": row["low_price"],
                "close": row["close_price"],
                "market_volume": row["market_volume"],
                "market_value": row["market_value"],
                "foreign": {
                    "buy_volume": row["foreign_buy_volume"],
                    "sell_volume": row["foreign_sell_volume"],
                    "net_volume": row["foreign_net_volume"],
                    "buy_value": row["foreign_buy_value"],
                    "sell_value": row["foreign_sell_value"],
                    "net_value": row["foreign_net_value"],
                    "ytd_net_volume": row["foreign_ytd_net_volume"],
                    "ytd_net_value": row["foreign_ytd_net_value"],
                },
                "proprietary": {
                    "buy_volume": row["proprietary_buy_volume"],
                    "sell_volume": row["proprietary_sell_volume"],
                    "net_volume": row["proprietary_net_volume"],
                    "buy_value": row["proprietary_buy_value"],
                    "sell_value": row["proprietary_sell_value"],
                    "net_value": row["proprietary_net_value"],
                    "source_record": bool(row["proprietary_source_record"]),
                    "record_status": "reported" if row["proprietary_source_record"] else "missing_source_record",
                },
            })
        return public

    def get_overview(self, raw_symbol: str) -> dict[str, Any]:
        symbol = str(raw_symbol or "").upper().strip()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise VolumeFlowSymbolNotFound("Mã cổ phiếu không hợp lệ")

        sessions = self.store.load_sessions(symbol)
        state = self.store.load_state(symbol)
        security = self._security_from_state(symbol, state)
        if security is None:
            security = self.source.resolve_security(symbol)

        refreshed = False
        stale = False
        warnings: list[str] = []
        cutoff: Optional[str] = None
        try:
            cutoff = self.source.latest_finalized_session()
        except VolumeFlowSourceError as exc:
            if not sessions:
                raise VolumeFlowUnavailable(str(exc)) from exc
            stale = True
            warnings.append("Không xác minh được phiên EOD mới nhất; đang dùng dữ liệu đã lưu.")

        if cutoff and not self._is_current(state, cutoff, sessions):
            with self._local_lock(symbol):
                sessions = self.store.load_sessions(symbol)
                state = self.store.load_state(symbol)
                if not self._is_current(state, cutoff, sessions):
                    with self.store.sync_lock(symbol) as acquired:
                        if not acquired:
                            stale = True
                            warnings.append("Mã đang được đồng bộ ở một tiến trình khác; tạm dùng bản lưu gần nhất.")
                        else:
                            try:
                                dataset = self.source.build_final_sessions(symbol, cutoff)
                                self.store.upsert_sessions(
                                    symbol,
                                    dataset.sessions,
                                    final_cutoff_date=cutoff,
                                    source=SOURCE_NAME,
                                    company_name=security["company_name"],
                                    exchange=security["exchange"],
                                    quality_version=QUALITY_VERSION,
                                    foreign_ytd_start_date=dataset.foreign_ytd_start_date,
                                    foreign_ytd_session_count=dataset.foreign_ytd_session_count,
                                    foreign_ytd_complete=dataset.foreign_ytd_complete,
                                    foreign_ytd_calculation=dataset.foreign_ytd_calculation,
                                )
                                refreshed = True
                            except (VolumeFlowSourceError, VolumeFlowSymbolNotFound) as exc:
                                try:
                                    self.store.record_failure(symbol, str(exc))
                                except VolumeFlowStoreUnavailable:
                                    pass
                                if not sessions:
                                    raise VolumeFlowUnavailable(str(exc)) from exc
                                stale = True
                                warnings.append("Nguồn Vietcap tạm thời không khả dụng; đang dùng dữ liệu đã lưu.")
                            sessions = self.store.load_sessions(symbol)
                            state = self.store.load_state(symbol)

        if not sessions:
            raise VolumeFlowUnavailable("Chưa có dữ liệu đã lưu cho mã cổ phiếu này")

        if state and state.get("quality_status") == "stale" and not refreshed:
            stale = True
        if not bool((state or {}).get("foreign_ytd_complete")):
            warnings.append(
                "Chưa xác minh đủ lịch sử khối ngoại từ đầu năm; không hiển thị lũy kế YTD."
            )
        live_payload = None
        try:
            live_payload = self._build_live_payload(symbol, sessions, state, security)
        except VolumeFlowSourceError:
            warnings.append("Chưa lấy được bảng giá realtime.")
            # A temporary board failure must not disable later in-session retries.
            # The exchange clock is independent from the price-board snapshot.
            try:
                market_session = self._market_session_provider() or {}
            except Exception:
                market_session = {}
            poll_enabled, poll_after_seconds = self._exchange_poll_state(
                security["exchange"], market_session
            )
            live_payload = {
                "schema_version": 1,
                "symbol": symbol,
                "exchange": security["exchange"],
                "official_eod_date": sessions[-1]["trading_date"],
                "market_session": market_session,
                "poll_enabled": poll_enabled,
                "poll_after_seconds": poll_after_seconds,
                "live_session": None,
                "source": "Vietcap public price board",
            }
        live_session = (live_payload or {}).get("live_session")
        live_foreign = None
        if live_session:
            foreign = live_session["foreign"]
            live_foreign = {
                "trading_date": live_session["date"],
                "as_of": live_session["observed_at"],
                "session": live_session["source_session"],
                "buy_volume": foreign["buy_volume"],
                "sell_volume": foreign["sell_volume"],
                "net_volume": foreign["net_volume"],
                "buy_value": foreign["buy_value"],
                "sell_value": foreign["sell_value"],
                "net_value": foreign["net_value"],
                "is_provisional": True,
                "source": live_session["source"],
            }

        coverage = len(sessions)
        data_status = "stale" if stale else ("partial_history" if coverage < 20 else "fresh")
        return {
            "schema_version": 2,
            "symbol": symbol,
            "company_name": security["company_name"],
            "exchange": security["exchange"],
            "as_of": sessions[-1]["trading_date"],
            "coverage_count": coverage,
            "target_session_count": 20,
            "data_status": data_status,
            "source": SOURCE_NAME,
            "quality_version": QUALITY_VERSION,
            "summary": {
                "foreign": self._participant_summary(sessions, "foreign"),
                "proprietary": self._participant_summary(sessions, "proprietary"),
            },
            "foreign_ytd": {
                "start_date": (state or {}).get("foreign_ytd_start_date"),
                "session_count": int((state or {}).get("foreign_ytd_session_count") or 0),
                "complete": bool((state or {}).get("foreign_ytd_complete")),
                "calculation": (state or {}).get("foreign_ytd_calculation"),
            },
            "sessions": self._public_sessions(sessions),
            "live_foreign": live_foreign,
            "live": live_payload,
            "sync": {
                "served_from": "database",
                "refreshed": refreshed,
                "stale": stale,
                "final_cutoff_date": cutoff or (state or {}).get("final_cutoff_date"),
                "last_success_at": (state or {}).get("last_success_at"),
                "warning": warnings[0] if warnings else None,
                "warnings": warnings,
            },
        }


_SERVICE: Optional[VolumeFlowService] = None
_SERVICE_LOCK = threading.Lock()


def get_volume_flow_service() -> VolumeFlowService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            store = get_volume_flow_store(required=True)
            assert store is not None
            _SERVICE = VolumeFlowService(store)
    return _SERVICE
