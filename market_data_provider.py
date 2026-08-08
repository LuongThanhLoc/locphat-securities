"""Direct market/fundamental data adapters used by the application.

This module intentionally exposes the small interface the existing engines
need, while keeping the HTTP details in one place. DNSE is the source of
realtime snapshots; Vietcap's public
REST endpoints provide reported financial statements and company metadata.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
import requests
import time


VCI_IQ = "https://iq.vietcap.com.vn/api/iq-insight-service"
VCI_TRADING = "https://trading.vietcap.com.vn/api"
_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
})
_CACHE: Dict[str, Any] = {}
_CACHE_TIME: Dict[str, float] = {}


def _cache_get(key: str, ttl_seconds: int) -> Any:
    cached_at = _CACHE_TIME.get(key, 0)
    if key in _CACHE and time.time() - cached_at < ttl_seconds:
        return _CACHE[key]
    _CACHE.pop(key, None)
    _CACHE_TIME.pop(key, None)
    return None


def _cache_set(key: str, value: Any) -> Any:
    _CACHE[key] = value
    _CACHE_TIME[key] = time.time()
    return value


def _get_json(url: str, *, params: Optional[Dict[str, Any]] = None,
              method: str = "GET", payload: Any = None, timeout: int = 10) -> Any:
    response = _SESSION.request(method, url, params=params, json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and body.get("successful") is False:
        raise RuntimeError(body.get("msg") or "Nguồn dữ liệu trả về lỗi")
    return body


def _unwrap_data(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _period_label(row: Dict[str, Any], annual: bool = False) -> str:
    year = int(row.get("yearReport") or row.get("year") or 0)
    if annual:
        return str(year)
    quarter = row.get("quarter")
    if quarter is None:
        length = int(row.get("lengthReport") or 0)
        quarter = ((length - 1) % 4) + 1 if length else 4
    return f"{year}-Q{int(quarter)}"


def _metadata(symbol: str) -> Dict[str, Dict[str, str]]:
    key = f"metadata:{symbol.upper()}"
    cached = _cache_get(key, 86400)
    if cached is not None:
        return cached
    data = _unwrap_data(_get_json(f"{VCI_IQ}/v1/company/{symbol.upper()}/financial-statement/metrics")) or {}
    mapping: Dict[str, Dict[str, str]] = {}
    for section in data.values():
        for item in section or []:
            field = str(item.get("field") or "").strip()
            if field:
                mapping[field] = {
                    "vi": str(item.get("fullTitleVi") or item.get("titleVi") or field),
                    "en": str(item.get("fullTitleEn") or item.get("titleEn") or field),
                    "id": field,
                }
    return _cache_set(key, mapping)


def _report_frame(symbol: str, section: str, period: str, lang: str) -> pd.DataFrame:
    cache_key = f"financial-statement:{symbol.upper()}:{section}"
    body = _cache_get(cache_key, 300)
    if body is None:
        body = _unwrap_data(_get_json(
            f"{VCI_IQ}/v1/company/{symbol.upper()}/financial-statement",
            params={"section": section},
        )) or {}
        _cache_set(cache_key, body)
    rows = body.get("quarters" if period == "quarter" else "years", [])
    if not rows:
        return pd.DataFrame()
    labels = _metadata(symbol)
    frame_rows: Dict[str, Dict[str, Any]] = {}
    # Vietcap returns the oldest quarter first; the existing engines expect
    # newest-to-oldest columns, matching the former adapter contract.
    selected_rows = list(reversed(rows[-8:]))
    for raw in selected_rows:
        label = _period_label(raw, annual=period == "year")
        for field, value in raw.items():
            if field in {"organCode", "ticker", "createDate", "updateDate", "yearReport", "quarter", "lengthReport", "publicDate"}:
                continue
            meta = labels.get(field, {"vi": field, "en": field, "id": field})
            item_name = meta.get("vi" if lang == "vi" else "en", field)
            frame_rows.setdefault(field, {"item": item_name, "item_en": meta.get("en", field), "item_id": field})[label] = value
    frame = pd.DataFrame(frame_rows.values())
    public_dates = [str(row.get("publicDate")) for row in selected_rows if row.get("publicDate")]
    frame.attrs.update({
        "source": "Vietcap public REST",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "latest_public_date": public_dates[0] if public_dates else None,
        "period_type": period,
    })
    return frame


class Finance:
    def __init__(self, symbol: str, source: str = "VCI", period: str = "quarter", **_: Any):
        self.symbol = symbol.upper().strip()
        self.period = period

    def balance_sheet(self, period: Optional[str] = None, lang: str = "vi", **_: Any) -> pd.DataFrame:
        return _report_frame(self.symbol, "BALANCE_SHEET", period or self.period, lang)

    def income_statement(self, period: Optional[str] = None, lang: str = "vi", **_: Any) -> pd.DataFrame:
        return _report_frame(self.symbol, "INCOME_STATEMENT", period or self.period, lang)

    def cash_flow(self, period: Optional[str] = None, lang: str = "vi", **_: Any) -> pd.DataFrame:
        return _report_frame(self.symbol, "CASH_FLOW", period or self.period, lang)

    def ratio(self, period: Optional[str] = None, lang: str = "vi", **_: Any) -> pd.DataFrame:
        data = _unwrap_data(_get_json(f"{VCI_IQ}/v1/company/{self.symbol}/statistics-financial")) or []
        if isinstance(data, dict):
            data = data.get("quarters") or data.get("years") or []
        if not data:
            return pd.DataFrame()
        latest = data[-1] if isinstance(data, list) else data
        labels = {
            "pe": "P/E", "pb": "P/B", "ps": "P/S", "roe": "ROE (%)", "roa": "ROA (%)",
            "grossMargin": "Biên LN gộp (%)", "afterTaxProfitMargin": "Biên LN sau thuế (%)",
            "currentRatio": "Hệ số thanh toán hiện hành", "quickRatio": "Hệ số thanh toán nhanh",
            "debtToEquity": "Nợ trên vốn chủ", "daySaleOutstanding": "Số ngày phải thu",
            "daysInventoryOutstanding": "Số ngày tồn kho", "cashCycle": "Chu kỳ tiền",
            "assetTurnover": "Vòng quay tài sản", "evToEbitda": "EV/EBITDA", "car": "CAR (%)",
            "marketCap": "Vốn hóa", "numberOfSharesMktCap": "Số CP lưu hành (triệu)",
        }
        rows = []
        for field, value in latest.items():
            if field in {"year", "quarter", "yearReport", "organCode", "ratioType"}:
                continue
            rows.append({"item": labels.get(field, field), "item_en": field, "item_id": field, "latest": value})
        return pd.DataFrame(rows)


class Company:
    def __init__(self, symbol: str, source: str = "VCI", **_: Any):
        self.symbol = symbol.upper().strip()

    def overview(self) -> pd.DataFrame:
        data = _unwrap_data(_get_json(f"{VCI_IQ}/v1/company/details", params={"ticker": self.symbol})) or {}
        return pd.DataFrame([{
            "symbol": data.get("ticker", self.symbol),
            "organ_name": data.get("viOrganName") or data.get("enOrganName") or self.symbol,
            "current_price": data.get("currentPrice"),
            "market_cap": data.get("marketCap"),
            "issue_share": data.get("numberOfSharesMktCap"),
            "rating": data.get("rating"),
            "target_price": data.get("targetPrice"),
            "company_profile": data.get("viProfile") or data.get("enProfile") or "",
            "com_group_code": data.get("comGroupCode"),
            "sector": data.get("sectorVn") or data.get("sector"),
            "listing_date": data.get("listingDate"),
        }])

    def news(self, **kwargs: Any) -> pd.DataFrame:
        end = datetime.now(timezone.utc).strftime("%Y%m%d")
        start = (datetime.now(timezone.utc) - timedelta(days=365 * 2)).strftime("%Y%m%d")
        data = _unwrap_data(_get_json(
            f"{VCI_IQ}/v1/news", params={"ticker": self.symbol, "fromDate": start, "toDate": end, "languageId": 1, "page": 0, "size": 20}
        )) or {}
        return pd.DataFrame(data.get("content", []) if isinstance(data, dict) else data)

    def events(self, **kwargs: Any) -> pd.DataFrame:
        end = str(kwargs.get("end") or datetime.now(timezone.utc).strftime("%Y-%m-%d")).replace("-", "")[:8]
        start = str(kwargs.get("start") or (datetime.now(timezone.utc) - timedelta(days=365 * 6)).strftime("%Y-%m-%d")).replace("-", "")[:8]
        try:
            data = _unwrap_data(_get_json(
                f"{VCI_IQ}/v1/events",
                params={"ticker": self.symbol, "fromDate": start, "toDate": end, "eventCode": "DIV,ISS,AGME,AGMR,EGME,AIS", "page": 0, "size": 100}
            )) or {}
            rows = (data.get("content", []) if isinstance(data, dict) else data) or []
            if rows:
                return pd.DataFrame(rows)
        except Exception:
            pass

        try:
            data = _unwrap_data(_get_json(
                f"{VCI_IQ}/v1/news-events-for-chart", params={"ticker": self.symbol, "fromDate": start, "toDate": end, "languageId": 1, "eventCode": "DIV,ISS"}
            )) or {}
            return pd.DataFrame(data.get("content", []) if isinstance(data, dict) else data)
        except Exception:
            return pd.DataFrame()


def fetch_vci_history(symbol: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
    """Fetch Vietcap OHLC without an implicit cross-market fallback.

    Callers such as LP-RRG need to know which provider produced a series so it
    can be validated and persisted.  In particular, an unqualified MSN lookup
    for ``SSI`` resolves to SSI Group (Philippines) before SSI Securities.
    """
    clean_symbol = symbol.upper().strip()
    start_dt = datetime.strptime(start[:10], "%Y-%m-%d")
    end_dt = datetime.strptime((end or datetime.now().strftime("%Y-%m-%d"))[:10], "%Y-%m-%d") + timedelta(days=1)
    payload = {
        "timeFrame": "ONE_DAY",
        "symbols": [clean_symbol],
        "to": int(end_dt.replace(tzinfo=timezone.utc).timestamp()),
        "countBack": max((end_dt - start_dt).days + 5, 20),
    }
    data = _get_json(
        f"{VCI_TRADING}/chart/OHLCChart/gap-chart",
        method="POST",
        payload=payload,
        timeout=8,
    )
    if isinstance(data, dict):
        data = data.get("data", data)
    if isinstance(data, list) and data and isinstance(data[0], dict) and isinstance(data[0].get("o"), list):
        data = [{k: data[0].get(k, [])[i] for k in ("t", "o", "h", "l", "c", "v")} for i in range(len(data[0].get("t", [])))]
    rows = []
    for row in data or []:
        timestamp = row.get("t")
        try:
            timestamp = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
        rows.append({
            "time": str(timestamp), "open": _as_float(row.get("o")),
            "high": _as_float(row.get("h")), "low": _as_float(row.get("l")),
            "close": _as_float(row.get("c")), "volume": int(_as_float(row.get("v"))),
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"Vietcap trả rỗng cho {clean_symbol}")
    frame.attrs["source"] = "Vietcap"
    return frame


def fetch_kbs_history(symbol: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
    """Vietnam-market-only historical fallback; never performs MSN search."""
    clean_symbol = "UPCOMINDEX" if symbol.upper().strip() == "UPCOM" else symbol.upper().strip()
    indices = {"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOMINDEX"}
    kind = "index" if clean_symbol in indices else "stocks"
    endpoint = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/{kind}/{clean_symbol}/data_day"
    end_value = (end or datetime.now().strftime("%Y-%m-%d"))[:10]
    response = requests.get(
        endpoint,
        params={
            "sdate": datetime.strptime(start[:10], "%Y-%m-%d").strftime("%d-%m-%Y"),
            "edate": datetime.strptime(end_value, "%Y-%m-%d").strftime("%d-%m-%Y"),
        },
        headers={"Accept": "application/json", "User-Agent": _SESSION.headers.get("User-Agent")},
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json().get("data_day", [])
    frame = pd.DataFrame(rows).rename(
        columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    if frame.empty:
        raise RuntimeError(f"KBS trả rỗng cho {clean_symbol}")
    frame.attrs["source"] = "KBS"
    return frame


class Quote:
    def __init__(self, symbol: str, source: str = "VCI", **_: Any):
        self.symbol = symbol.upper().strip()

    def history(self, start: str, end: Optional[str] = None, interval: str = "1D", **_: Any) -> pd.DataFrame:
        try:
            return fetch_vci_history(self.symbol, start, end)
        except Exception as primary_error:
            try:
                return fetch_kbs_history(self.symbol, start, end)
            except Exception as fallback_error:
                print(f"[Quote] history failed for {self.symbol}: Vietcap={primary_error}; KBS={fallback_error}")
                return pd.DataFrame()


class Listing:
    def __init__(self, source: str = "VCI", **_: Any):
        pass

    def all_symbols(self, **_: Any) -> pd.DataFrame:
        data = _unwrap_data(_get_json(f"{VCI_IQ}/v2/company/search-bar", params={"language": 1})) or []
        supported_exchanges = {"HOSE", "HNX", "UPCOM"}
        # QU (listed ETF/unit trust) is included because market breadth boards
        # count these instruments in the five-state market tally.
        common_equity_types = {"CT", "NH", "CK", "BH", "QU"}
        rows = []
        for company in data:
            exchange = str(company.get("floor") or "").upper()
            security_type = str(company.get("comTypeCode") or "").upper()
            if exchange not in supported_exchanges or security_type not in common_equity_types:
                continue
            rows.append({
                "symbol": company.get("code"),
                "organ_name": company.get("name"),
                "exchange": exchange,
                "com_type_code": security_type,
            })
        return pd.DataFrame(rows).drop_duplicates(subset=["symbol"], keep="first")

    def symbols_by_industries(self, **_: Any) -> pd.DataFrame:
        data = _unwrap_data(_get_json(f"{VCI_IQ}/v2/company/search-bar", params={"language": 1})) or []
        supported_exchanges = {"HOSE", "HNX", "UPCOM"}
        common_equity_types = {"CT", "NH", "CK", "BH", "QU"}
        rows = []
        for company in data:
            exchange = str(company.get("floor") or "").upper()
            security_type = str(company.get("comTypeCode") or "").upper()
            if exchange not in supported_exchanges or security_type not in common_equity_types:
                continue

            row = {
                "symbol": company.get("code"),
                "organ_name": company.get("name"),
                "exchange": exchange,
                "com_type_code": security_type,
            }
            for level in range(1, 5):
                item = company.get(f"icbLv{level}") or {}
                row[f"icb_code{level}"] = item.get("code")
                row[f"icb_name{level}"] = item.get("name")
            rows.append(row)
        return pd.DataFrame(rows).drop_duplicates(subset=["symbol"], keep="first")


class Trading:
    def __init__(self, source: str = "VCI", **_: Any):
        pass

    def price_board(self, symbols_list: Iterable[str], **_: Any) -> pd.DataFrame:
        data = _get_json(f"{VCI_TRADING}/price/symbols/getList", method="POST", payload={"symbols": list(symbols_list)}) or []
        rows = []
        for item in data:
            listing = item.get("listingInfo") or {}
            match = item.get("matchPrice") or {}
            bid_ask = item.get("bidAsk") or {}
            rows.append({
                ("listing", "symbol"): listing.get("symbol"),
                ("listing", "organ_name"): listing.get("organName"),
                ("listing", "exchange"): listing.get("board"),
                ("listing", "stock_type"): listing.get("stockType") or listing.get("type"),
                ("listing", "is_delisted"): listing.get("isDelisted"),
                ("listing", "trading_date"): listing.get("tradingDate"),
                ("listing", "received_time"): listing.get("receivedTime"),
                ("listing", "listed_share"): listing.get("listedShare"),
                ("listing", "ref_price"): listing.get("refPrice"),
                ("listing", "ceiling"): listing.get("ceiling"),
                ("listing", "floor"): listing.get("floor"),
                ("match", "match_price"): match.get("matchPrice"),
                ("match", "accumulated_value"): match.get("accumulatedValue"),
                ("match", "accumulated_volume"): match.get("accumulatedVolume"),
                ("bidAsk", "bid_prices"): bid_ask.get("bidPrices"),
                ("bidAsk", "ask_prices"): bid_ask.get("askPrices"),
            })
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame.columns = pd.MultiIndex.from_tuples(frame.columns)
            frame = frame.drop_duplicates(subset=[("listing", "symbol")], keep="first")
            frame.attrs.update({
                "source": "Vietcap public price board",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        return frame


class VnstockFinance:
    """Optional fallback adapter; imported only when the primary REST source fails."""

    def __init__(self, symbol: str, source: str = "VCI", period: str = "quarter", **_: Any):
        from vnstock import Finance as _Finance
        self.client = _Finance(source=source, symbol=symbol.upper().strip(), period=period, get_all=True, show_log=False)

    def balance_sheet(self, period: Optional[str] = None, lang: str = "vi", **kwargs: Any) -> pd.DataFrame:
        return self.client.balance_sheet(period=period, lang=lang, **kwargs)

    def income_statement(self, period: Optional[str] = None, lang: str = "vi", **kwargs: Any) -> pd.DataFrame:
        return self.client.income_statement(period=period, lang=lang, **kwargs)

    def cash_flow(self, period: Optional[str] = None, lang: str = "vi", **kwargs: Any) -> pd.DataFrame:
        return self.client.cash_flow(period=period, lang=lang, **kwargs)

    def ratio(self, period: Optional[str] = None, lang: str = "vi", **kwargs: Any) -> pd.DataFrame:
        return self.client.ratio(period=period, lang=lang, **kwargs)


class VnstockCompany:
    """Optional vnstock company metadata fallback."""

    def __init__(self, symbol: str, source: str = "VCI", **_: Any):
        from vnstock import Company as _Company
        self.client = _Company(source=source, symbol=symbol.upper().strip(), show_log=False)

    def overview(self) -> pd.DataFrame:
        return self.client.overview()

    def events(self, **kwargs: Any) -> pd.DataFrame:
        return self.client.events(**kwargs)


class VnstockQuote:
    """Optional vnstock historical quote fallback."""

    def __init__(self, symbol: str, source: str = "VCI", **_: Any):
        from vnstock import Quote as _Quote
        self.client = _Quote(source=source, symbol=symbol.upper().strip(), show_log=False)

    def history(self, start: str, end: Optional[str] = None, interval: str = "1D", **kwargs: Any) -> pd.DataFrame:
        return self.client.history(start=start, end=end, interval=interval, **kwargs)
