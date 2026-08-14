from __future__ import annotations

from market_ribbon_service import CLOSED_REFRESH_SECONDS, LIVE_REFRESH_SECONDS, MarketRibbonService


def session(live=True):
    return {
        "phase": "CONTINUOUS" if live else "CLOSED",
        "is_live_matching": live,
        "calendar_date": "2026-08-14",
    }


def index_item(symbol):
    return {
        "symbol": symbol, "type": "index", "last_price": 1800.0,
        "reference_price": 1790.0, "value": 1800.0, "value_display": "1,800.00",
        "change": 10.0, "change_percent": 10 / 1790 * 100, "status": "GAIN",
        "trend": "up", "source": "Vietcap", "observed_at": "2026-08-14T02:00:00+00:00",
        "as_of": "2026-08-14", "stale": False,
    }


def build_snapshot(symbols):
    stocks = []
    for position, symbol in enumerate(symbols):
        reference = 10_000 + position * 100
        stocks.append({
            "symbol": symbol, "name": symbol, "exchange": "HOSE", "instrument_type": "STOCK",
            "match_price": reference + 100, "price_vnd": reference + 100, "ref_price": reference,
            "status": "GAIN", "volume": 1, "trading_value": 1_000_000,
            "trading_date": "2026-08-14", "sector": "Kiểm thử",
        })
    return {
        "sectors": [{"name": "Kiểm thử", "stocks": stocks}],
        "data_lineage": {"price_source": "Vietcap public price board", "fetched_at": "2026-08-14T02:00:00+00:00"},
        "snapshot_frozen": False,
    }


def configured_service(monkeypatch, *, live=True):
    service = MarketRibbonService()
    symbols = [f"A{position:02d}" for position in range(30)]
    monkeypatch.setattr(service, "_market_session", lambda: session(live))
    monkeypatch.setattr(service, "_membership", lambda: (symbols, {
        "source": "vnstock/KBS+VCI", "source_agreement": True, "stale": False,
    }))
    monkeypatch.setattr(service, "_snapshot", lambda: build_snapshot(symbols))
    monkeypatch.setattr(service, "_index_item", lambda symbol, market_session: index_item(symbol))
    return service, symbols


def test_market_ribbon_has_exact_verified_universe_and_formula(monkeypatch):
    service, symbols = configured_service(monkeypatch)
    payload = service.get()
    assert payload["schema_version"] == 1
    assert payload["refresh_after_seconds"] == LIVE_REFRESH_SECONDS
    assert payload["membership"]["count"] == 30
    assert [item["symbol"] for item in payload["items"]] == ["VNINDEX", "VN30", *symbols]
    assert len(payload["items"]) == len({item["symbol"] for item in payload["items"]}) == 32
    stock = payload["items"][2]
    assert stock["last_price"] > 0 and stock["reference_price"] > 0
    assert stock["change_percent"] == round((stock["last_price"] / stock["reference_price"] - 1) * 100, 4)
    assert payload["data_quality"]["no_synthetic_data"] is True


def test_market_ribbon_closed_session_uses_longer_refresh_and_cache(monkeypatch):
    service, _ = configured_service(monkeypatch, live=False)
    calls = 0
    original = service._build

    def counted(market_session):
        nonlocal calls
        calls += 1
        return original(market_session)

    monkeypatch.setattr(service, "_build", counted)
    first = service.get()
    second = service.get()
    assert first["refresh_after_seconds"] == CLOSED_REFRESH_SECONDS
    assert second["items"] == first["items"]
    assert calls == 1


def test_market_ribbon_refreshes_once_when_session_closes(monkeypatch):
    service, _ = configured_service(monkeypatch)
    live_state = {"value": True}
    monkeypatch.setattr(service, "_market_session", lambda: session(live_state["value"]))
    calls = 0
    original = service._build

    def counted(market_session):
        nonlocal calls
        calls += 1
        return original(market_session)

    monkeypatch.setattr(service, "_build", counted)
    assert service.get()["market_session"]["is_live_matching"] is True
    live_state["value"] = False
    assert service.get()["market_session"]["is_live_matching"] is False
    assert service.get()["market_session"]["is_live_matching"] is False
    assert calls == 2


def test_market_ribbon_preserves_last_known_good_on_refresh_error(monkeypatch):
    service, _ = configured_service(monkeypatch)
    good = service.get()
    monkeypatch.setattr(service, "_build", lambda market_session: (_ for _ in ()).throw(RuntimeError("upstream down")))
    stale = service.get(force_refresh=True)
    assert stale["last_known_good"] is True and stale["stale"] is True
    assert stale["items"][0]["last_price"] == good["items"][0]["last_price"]
    assert all(item["stale"] for item in stale["items"])


def test_missing_price_is_never_exposed_as_zero(monkeypatch):
    service, symbols = configured_service(monkeypatch)
    snapshot = build_snapshot(symbols)
    snapshot["sectors"][0]["stocks"] = snapshot["sectors"][0]["stocks"][1:]
    monkeypatch.setattr(service, "_snapshot", lambda: snapshot)
    payload = service.get()
    missing = next(item for item in payload["items"] if item["symbol"] == symbols[0])
    assert missing["last_price"] is None
    assert missing["value_display"] is None
    assert missing["status"] == "UNAVAILABLE"
