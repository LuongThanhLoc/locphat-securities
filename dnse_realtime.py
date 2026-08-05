import asyncio
import base64
import hashlib
import hmac
import json
import os
import ssl
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib import parse

import requests
import websockets
import certifi


DNSE_WS_URL = "wss://ws-openapi.dnse.com.vn"
DNSE_REST_URL = "https://openapi.dnse.com.vn"
DNSE_API_VERSION = "2026-05-07"
DNSE_DEFAULT_BOARD_ID = "G1"
_ENV_LOADED = False
_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}


class DNSEConfigError(RuntimeError):
    pass


def _load_env_file(path: str = ".env") -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    candidates = [path, "/etc/secrets/.env"]
    for env_path in candidates:
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    os.environ.setdefault(key, value)
        except Exception:
            pass



def _settings() -> Dict[str, str]:
    _load_env_file()
    api_key = os.getenv("DNSE_API_KEY", "").strip()
    api_secret = os.getenv("DNSE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise DNSEConfigError("DNSE_API_KEY/DNSE_API_SECRET chưa được cấu hình trong .env")
    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "ws_url": os.getenv("DNSE_WS_URL", DNSE_WS_URL).rstrip("/"),
        "rest_url": os.getenv("DNSE_REST_URL", DNSE_REST_URL).rstrip("/"),
        "api_version": os.getenv("DNSE_API_VERSION", DNSE_API_VERSION),
        "board_id": os.getenv("DNSE_BOARD_ID", DNSE_DEFAULT_BOARD_ID),
        "ws_ssl_verify": os.getenv("DNSE_WS_SSL_VERIFY", "true").strip().lower(),
    }


def _decode_ws_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, bytes):
        message = message.decode("utf-8")
    return json.loads(message)


def _ws_auth_message(api_key: str, api_secret: str) -> Dict[str, Any]:
    timestamp = int(time.time())
    nonce = str(int(time.time() * 1_000_000))
    message = f"{api_key}:{timestamp}:{nonce}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "action": "auth",
        "api_key": api_key,
        "signature": signature,
        "timestamp": timestamp,
        "nonce": nonce,
    }


def _normalize_vnd_price(price: Optional[float]) -> Optional[float]:
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    # DNSE market-data stock prices are commonly expressed in thousands of VND.
    if 0 < value < 1000:
        return value * 1000
    return value


def _ws_ssl_context(verify_mode: str) -> Optional[ssl.SSLContext]:
    if verify_mode in {"0", "false", "no", "off"}:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return ssl.create_default_context(cafile=certifi.where())


def _parse_exchange_time(value: Any) -> Optional[str]:
    try:
        if isinstance(value, dict):
            seconds = value.get("Seconds") or value.get("seconds")
            nanos = value.get("Nanos") or value.get("nanos") or 0
            if seconds is None:
                return None
            dt = datetime.fromtimestamp(float(seconds) + float(nanos) / 1e9, tz=timezone.utc)
            return dt.isoformat()
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
            return dt.isoformat()
        if isinstance(value, str):
            return value
    except Exception:
        return None
    return None


def _classify_payload(payload: Dict[str, Any]) -> Optional[str]:
    if "matchPrice" in payload:
        return "trade"
    if "bid" in payload or "offer" in payload:
        return "quote"
    if "basicPrice" in payload or "ceilingPrice" in payload or "floorPrice" in payload:
        return "security_definition"
    return None


def _clean_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(payload)
    if "time" in cleaned:
        cleaned["exchange_time"] = _parse_exchange_time(cleaned.get("time"))
    if "transactTime" in cleaned:
        cleaned["exchange_time"] = _parse_exchange_time(cleaned.get("transactTime"))
    return cleaned


def _extract_rest_trade(body: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(body.get("trades"), list) and body["trades"]:
        trade = _clean_payload(dict(body["trades"][0]))
        trade["price_vnd"] = _normalize_vnd_price(trade.get("matchPrice"))
        return trade
    cleaned = _clean_payload(body)
    cleaned["price_vnd"] = _normalize_vnd_price(cleaned.get("matchPrice"))
    return cleaned


def _rest_signature_headers(
    api_key: str,
    api_secret: str,
    method: str,
    path: str,
    api_version: str,
) -> Dict[str, str]:
    date_value = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    nonce = uuid.uuid4().hex
    signing_string = f"(request-target): {method.lower()} {path}\ndate: {date_value}\nnonce: {nonce}"
    digest = hmac.new(api_secret.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).digest()
    signature = parse.quote(base64.b64encode(digest).decode("utf-8"), safe="")
    signature_header = (
        f'Signature keyId="{api_key}",algorithm="hmac-sha256",'
        f'headers="(request-target) date",signature="{signature}",nonce="{nonce}"'
    )
    return {
        "Date": date_value,
        "X-Signature": signature_header,
        "x-api-key": api_key,
        "version": api_version,
    }


def _rest_security_definition(settings: Dict[str, str], symbol: str) -> Optional[Dict[str, Any]]:
    path = f"/price/{symbol}/secdef"
    url = f"{settings['rest_url']}{path}?boardId={parse.quote(settings['board_id'])}"
    try:
        headers = _rest_signature_headers(
            settings["api_key"],
            settings["api_secret"],
            "GET",
            path,
            settings["api_version"],
        )
        response = requests.get(url, headers=headers, timeout=8)
        if response.ok:
            body = response.json()
            if isinstance(body, list) and body:
                body = body[0]
            if isinstance(body, dict):
                return _clean_payload(body)
    except Exception:
        pass
    return None


def _rest_close_price(settings: Dict[str, str], symbol: str) -> Optional[Dict[str, Any]]:
    path = f"/price/{symbol}/close"
    url = f"{settings['rest_url']}{path}?boardId={parse.quote(settings['board_id'])}"
    try:
        headers = _rest_signature_headers(
            settings["api_key"],
            settings["api_secret"],
            "GET",
            path,
            settings["api_version"],
        )
        response = requests.get(url, headers=headers, timeout=8)
        if response.ok:
            body = response.json()
            if isinstance(body, list) and body:
                body = body[0]
            if isinstance(body, dict):
                return _clean_payload(body)
    except Exception:
        pass
    return None


def _rest_latest_trade(settings: Dict[str, str], symbol: str) -> Optional[Dict[str, Any]]:
    path = f"/price/{symbol}/trades/latest"
    url = f"{settings['rest_url']}{path}?boardId={parse.quote(settings['board_id'])}"
    headers = _rest_signature_headers(
        settings["api_key"],
        settings["api_secret"],
        "GET",
        path,
        settings["api_version"],
    )
    response = requests.get(url, headers=headers, timeout=10)
    if not response.ok:
        return {"status_code": response.status_code, "error": response.text[:500]}
    try:
        body = response.json()
    except ValueError:
        return {"status_code": response.status_code, "raw": response.text[:500]}
    if isinstance(body, dict):
        return _extract_rest_trade(body)
    return {"status_code": response.status_code, "raw": body}


async def _fetch_ws_snapshot(symbol: str, timeout_seconds: float) -> Dict[str, Any]:
    settings = _settings()
    symbol = symbol.upper().strip()
    board_id = settings["board_id"]
    result: Dict[str, Any] = {
        "symbol": symbol,
        "source": "DNSE OpenAPI WebSocket",
        "status": "connecting",
        "connected": False,
        "authenticated": False,
        "subscribed": False,
        "board_id": board_id,
        "channels": [
            f"tick.{board_id}.json",
            f"top_price.{board_id}.json",
            f"security_definition.{board_id}.json",
        ],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "trade": None,
        "quote": None,
        "security_definition": None,
        "price_vnd": None,
        "raw_price": None,
        "messages_seen": 0,
    }

    url = f"{settings['ws_url']}/v1/stream?encoding=json"
    async with websockets.connect(
        url,
        ping_interval=30,
        ping_timeout=30,
        close_timeout=10,
        max_queue=128,
        open_timeout=10,
        ssl=_ws_ssl_context(settings["ws_ssl_verify"]),
    ) as ws:
        result["connected"] = True
        result["status"] = "connected"

        welcome = _decode_ws_message(await asyncio.wait_for(ws.recv(), timeout=10))
        result["session_id"] = welcome.get("session_id") or welcome.get("sid")

        await ws.send(json.dumps(_ws_auth_message(settings["api_key"], settings["api_secret"])))
        auth_response = _decode_ws_message(await asyncio.wait_for(ws.recv(), timeout=10))
        action = auth_response.get("action") or auth_response.get("a")
        if action != "auth_success":
            result["status"] = "auth_error"
            result["auth_response"] = {
                k: v for k, v in auth_response.items() if k not in {"api_key", "signature"}
            }
            return result

        result["authenticated"] = True
        for channel in result["channels"]:
            await ws.send(json.dumps({
                "action": "subscribe",
                "channels": [{"name": channel, "symbols": [symbol]}],
            }))
        result["subscribed"] = True
        result["status"] = "subscribed_waiting_for_tick"

        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 1)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                payload = _decode_ws_message(await asyncio.wait_for(ws.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                break
            result["messages_seen"] += 1
            if str(payload.get("symbol", "")).upper() != symbol:
                continue
            payload_type = _classify_payload(payload)
            if not payload_type:
                continue
            cleaned = _clean_payload(payload)
            result[payload_type] = cleaned
            if payload_type == "trade":
                result["raw_price"] = cleaned.get("matchPrice")
                result["price_vnd"] = _normalize_vnd_price(cleaned.get("matchPrice"))
                result["exchange_time"] = cleaned.get("exchange_time")
                result["status"] = "live"
            elif payload_type == "security_definition":
                result["reference_price_vnd"] = _normalize_vnd_price(cleaned.get("basicPrice") or cleaned.get("referencePrice"))
                result["ceiling_price_vnd"] = _normalize_vnd_price(cleaned.get("ceilingPrice"))
                result["floor_price_vnd"] = _normalize_vnd_price(cleaned.get("floorPrice"))
            if result.get("trade") and result.get("quote"):
                break

    return result


async def get_dnse_realtime_snapshot(symbol: str, timeout_seconds: float = 6.0) -> Dict[str, Any]:
    symbol = symbol.upper().strip()
    cached = _SNAPSHOT_CACHE.get(symbol)
    now = time.time()
    if cached and now - cached["cached_at"] < 5:
        payload = dict(cached["payload"])
        payload["cache"] = {"hit": True, "age_seconds": round(now - cached["cached_at"], 2)}
        return payload

    try:
        payload = await _fetch_ws_snapshot(symbol, timeout_seconds)
    except DNSEConfigError as exc:
        payload = {
            "symbol": symbol,
            "source": "DNSE OpenAPI WebSocket",
            "status": "config_error",
            "error": str(exc),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        payload = {
            "symbol": symbol,
            "source": "DNSE OpenAPI WebSocket",
            "status": "ws_error",
            "error": str(exc),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    if payload.get("price_vnd") is None and payload.get("status") not in {"config_error", "auth_error"}:
        try:
            settings = _settings()
            rest_trade = _rest_latest_trade(settings, symbol)
            payload["rest_fallback"] = {"source": "DNSE REST latest trade", "trade": rest_trade}
            if isinstance(rest_trade, dict) and rest_trade.get("price_vnd") is not None:
                payload["price_vnd"] = rest_trade["price_vnd"]
                payload["raw_price"] = rest_trade.get("matchPrice")
                payload["exchange_time"] = rest_trade.get("exchange_time")
                payload["source"] = "DNSE REST latest trade"
                payload["status"] = "rest_fallback"
        except Exception as exc:
            payload["rest_fallback"] = {"source": "DNSE REST latest trade", "error": str(exc)}

    payload["cache"] = {"hit": False, "ttl_seconds": 5}
    _SNAPSHOT_CACHE[symbol] = {"cached_at": time.time(), "payload": payload}
    return payload


def get_dnse_latest_price(symbol: str) -> Optional[float]:
    """Synchronous latest-trade helper for the existing analysis pipeline."""
    settings = _settings()
    trade = _rest_latest_trade(settings, symbol.upper().strip())
    if isinstance(trade, dict):
        return trade.get("price_vnd")
    return None


def get_dnse_latest_price_snapshot(symbol: str) -> Dict[str, Any]:
    """Latest REST trade plus exchange/fetch timestamps for valuation provenance."""
    settings = _settings()
    trade = _rest_latest_trade(settings, symbol.upper().strip()) or {}
    return {
        "price_vnd": trade.get("price_vnd") if isinstance(trade, dict) else None,
        "exchange_time": trade.get("exchange_time") if isinstance(trade, dict) else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "DNSE REST latest trade",
        "trade": trade if isinstance(trade, dict) else None,
    }
