import os
# Set timezone before any datetime operations — ensures consistent date boundaries
# regardless of the host system's clock or TZ configuration (critical for Render).
os.environ.setdefault("TZ", "Asia/Ho_Chi_Minh")

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import re
from datetime import date, datetime, timedelta
import uvicorn
from ctck_analyzer import analyze_security_stock, CTCK_STOCKS
from peer_comparison_engine import get_peer_comparison
from typing import Optional, Dict, Any
from dnse_realtime import get_dnse_realtime_snapshot
from market_data_provider import Quote
from quant_engine import build_quant_framework
from premium_analysis import build_premium_analysis
from rsi_backtest_engine import run_backtest

app = FastAPI(
    title="Hệ Thống Phân Tích Cổ Phiếu Chứng Khoán (CTCK)",
    description="Hệ thống phân tích dữ liệu thị trường, BCTC và tin tức có grounding cho chứng khoán Việt Nam",
    version="1.3.1"
)

# Mount static directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

@app.get("/static/heatmap.js")
def get_heatmap_js():
    js_path = os.path.join(static_dir, "heatmap.js")
    if os.path.exists(js_path):
        return FileResponse(js_path, media_type="application/javascript", headers=_cache_busting_headers_for_file(js_path))
    raise HTTPException(status_code=404, detail="File not found")

app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Boot the background intraday snapshot poller so the bottom-of-page
# timeline scrubber has fresh checkpoints while the market is live.
# Idempotent — `start_intraday_poller()` short-circuits if a worker is
# already alive.
try:
    from heatmap_engine import init_db_snapshot, start_intraday_poller
    init_db_snapshot()
    start_intraday_poller()
except Exception as boot_err:
    print(f"[Heatmap] Warning: failed to start intraday poller: {boot_err}")


def _build_quant_decision(symbol: str, stock_data: dict) -> dict:
    """Enrich the deterministic quant model with peer and VN-Index evidence."""
    peer_data = get_peer_comparison(symbol, None)
    benchmark_history = []
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=220)).strftime("%Y-%m-%d")
        benchmark = Quote(symbol="VNINDEX", source="VCI").history(start=start_date, end=end_date)
        if not benchmark.empty:
            benchmark_history = benchmark.to_dict("records")
    except Exception as exc:
        stock_data.setdefault("data_quality", {}).setdefault("warnings", []).append(
            "Khong tai duoc VN-Index cho bo loc suc manh tuong doi."
        )
        print(f"Quant benchmark warning for {symbol}: {exc}")

    stock_data["decision_framework"] = build_quant_framework(
        stock_data,
        peer_comparison=peer_data,
        benchmark_history=benchmark_history,
    )
    return peer_data


def _load_deep_history(symbol: str, stock_data: dict) -> None:
    """Price history is intentionally loaded only for user-triggered deep analysis."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365 * 5 + 30)).strftime("%Y-%m-%d")
    frame = Quote(symbol=symbol, source="VCI").history(start=start_date, end=end_date)
    if not frame.empty:
        stock_data["price_history"] = frame.rename(columns={"time": "date"}).to_dict("records")


def _require_explicit_deepseek_action(user_action: Optional[str]) -> None:
    if user_action != "deepseek":
        raise HTTPException(
            status_code=400,
            detail="DeepSeek chi duoc goi sau thao tac chu dong cua nguoi dung.",
        )

def _cache_busting_headers_for_file(filepath: str) -> dict:
    """Generate ETag + aggressive anti-cache HTTP response headers based on actual file mtime.
    Browsers compare ETag between requests. If file edited on disk, ETag changes,
    causing browsers to DISCARD stale cache instantly."""
    mtime = int(os.path.getmtime(filepath))
    size = os.path.getsize(filepath)
    etag_value = f'W/"lpsec-{mtime}-{size}"'
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "Surrogate-Control": "no-store",
        "ETag": etag_value,
        "Last-Modified": datetime.utcfromtimestamp(mtime).strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "Vary": "*",
    }

@app.get("/", response_class=HTMLResponse)
def read_root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, headers=_cache_busting_headers_for_file(index_path))
    raise HTTPException(status_code=404, detail="Trang chủ chưa sẵn sàng")

@app.get("/heatmap", response_class=HTMLResponse)
def read_heatmap():
    heatmap_path = os.path.join(static_dir, "heatmap.html")
    if os.path.exists(heatmap_path):
        return FileResponse(heatmap_path, headers=_cache_busting_headers_for_file(heatmap_path))
    raise HTTPException(status_code=404, detail="Trang Bản Đồ Nhiệt chưa sẵn sàng")

@app.get("/stock/{symbol}", response_class=HTMLResponse)
def read_stock(symbol: str):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,5}", symbol):
        raise HTTPException(status_code=404, detail="Mã cổ phiếu không hợp lệ")
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, headers=_cache_busting_headers_for_file(index_path))
    raise HTTPException(status_code=404, detail="Trang phân tích chưa sẵn sàng")

@app.get("/calendar", response_class=HTMLResponse)
def read_calendar():
    calendar_path = os.path.join(static_dir, "calendar.html")
    if os.path.exists(calendar_path):
        return FileResponse(calendar_path, headers=_cache_busting_headers_for_file(calendar_path))
    raise HTTPException(status_code=404, detail="Lịch doanh nghiệp chưa sẵn sàng")

@app.get("/watchlist", response_class=HTMLResponse)
def read_watchlist():
    watchlist_path = os.path.join(static_dir, "watchlist.html")
    if os.path.exists(watchlist_path):
        return FileResponse(
            watchlist_path,
            headers=_cache_busting_headers_for_file(watchlist_path),
        )
    raise HTTPException(
        status_code=404,
        detail="Danh mục theo dõi chưa sẵn sàng",
    )

@app.get("/backtest", response_class=HTMLResponse)
def read_backtest():
    backtest_path = os.path.join(static_dir, "backtest.html")
    if os.path.exists(backtest_path):
        return FileResponse(
            backtest_path,
            headers=_cache_busting_headers_for_file(backtest_path),
        )
    raise HTTPException(
        status_code=404,
        detail="Trang Backtest chưa sẵn sàng",
    )

@app.get("/rrg", response_class=HTMLResponse)
def read_rrg():
    rrg_path = os.path.join(static_dir, "rrg.html")
    if os.path.exists(rrg_path):
        return FileResponse(
            rrg_path,
            headers=_cache_busting_headers_for_file(rrg_path),
        )
    raise HTTPException(
        status_code=404,
        detail="Trang Biểu Đồ RRG chưa sẵn sàng",
    )

@app.get("/api/rrg/data")
def get_rrg_data_api(
    response: Response,
    group: str = "SMC_TOP",
    symbols: Optional[str] = None,
    benchmark: str = "VNINDEX",
    tail_length: int = 15,
    period: int = 14,
):
    """Return an LP-RRG dataset for the requested group/benchmark.

    Verified bars come from Vietcap -> KBS and are persisted in PostgreSQL.
    The endpoint fails closed with HTTP 503 instead of returning a partial set.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    from rrg_engine import RrgDataIncomplete, generate_rrg_dataset
    try:
        custom_list = None
        if symbols:
            raw_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
            if len(raw_symbols) > 30:
                raise HTTPException(status_code=422, detail="Danh mục tùy chỉnh tối đa 30 mã")
            invalid = [s for s in raw_symbols if not re.fullmatch(r"[A-Z0-9]{2,10}", s)]
            if invalid:
                raise HTTPException(status_code=422, detail=f"Mã cổ phiếu không hợp lệ: {invalid[0]}")
            custom_list = list(dict.fromkeys(raw_symbols))
        return generate_rrg_dataset(
            group_key=group,
            custom_symbols=custom_list,
            benchmark_symbol=benchmark,
            tail_length=tail_length,
            period=period,
        )
    except RrgDataIncomplete as e:
        raise HTTPException(
            status_code=503,
            detail={
                "code": e.reason,
                "message": "Dữ liệu RRG đang được đồng bộ; hệ thống không trả dataset thiếu.",
                "missing_symbols": e.missing_symbols,
            },
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tính toán biểu đồ RRG: {str(e)}")


@app.get("/api/rrg/health")
def get_rrg_health_api(response: Response):
    """Data-store health and freshness metadata; never exposes credentials."""
    from rrg_data_gateway import rrg_data_health
    payload = rrg_data_health()
    response.headers["Cache-Control"] = "no-store"
    if payload.get("status") == "error":
        response.status_code = 503
    return payload


@app.on_event("startup")
def initialise_rrg_data_store():
    """Create the idempotent RRG schema before the first strict-mode request."""
    try:
        from rrg_data_gateway import init_rrg_store, strict_store_enabled
        if strict_store_enabled():
            init_rrg_store()
            from rrg_sync import start_background_sync
            start_background_sync()
    except Exception as exc:
        # Keep unrelated application pages available.  The RRG API itself is
        # fail-closed and will return 503 until PostgreSQL recovers.
        print(f"[RRG] PostgreSQL initialization failed: {exc}")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    fav_path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(fav_path):
        return FileResponse(fav_path)
    raise HTTPException(status_code=404, detail="Favicon not found")

# Cached Stock List for Search Auto-Complete
ALL_STOCKS_CACHE = []

def remove_accents_str(text: str) -> str:
    text = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', text)
    text = re.sub(r'[ÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴ]', 'A', text)
    text = re.sub(r'[èéẹẻẽêềếệểễ]', 'e', text)
    text = re.sub(r'[ÈÉẸẺẼÊỀẾỆỂỄ]', 'E', text)
    text = re.sub(r'[ìíịỉĩ]', 'i', text)
    text = re.sub(r'[ÌÍỊỈĨ]', 'I', text)
    text = re.sub(r'[òóọỏõôồốộổỗơờớợởỡ]', 'o', text)
    text = re.sub(r'[ÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠ]', 'O', text)
    text = re.sub(r'[ùúụủũưừứựửữ]', 'u', text)
    text = re.sub(r'[ÙÚỤỦŨƯỪỨỰỬỮ]', 'U', text)
    text = re.sub(r'[ỳýỵỷỹ]', 'y', text)
    text = re.sub(r'[ỲÝỴỶỸ]', 'Y', text)
    text = re.sub(r'[đ]', 'd', text)
    text = re.sub(r'[Đ]', 'D', text)
    return text

def load_all_stocks():
    global ALL_STOCKS_CACHE
    if not ALL_STOCKS_CACHE:
        try:
            from market_data_provider import Listing
            df = Listing(source='VCI').all_symbols()
            if not df.empty:
                records = []
                for _, row in df.iterrows():
                    sym = str(row.get('symbol') or '').upper().strip()
                    name = str(row.get('organ_name') or '').strip()
                    if sym and len(sym) <= 6:
                        records.append({
                            "symbol": sym,
                            "name": name,
                            "name_norm": remove_accents_str(name).lower()
                        })
                ALL_STOCKS_CACHE = records
        except Exception as e:
            print(f"Error loading all stocks: {e}")
    return ALL_STOCKS_CACHE

@app.get("/api/all_stocks")
def get_all_stocks():
    return load_all_stocks()

@app.get("/api/search_suggest")
def search_suggest(q: str = ""):
    query_raw = q.upper().strip()
    query_norm = remove_accents_str(q).lower().strip()
    all_stocks = load_all_stocks()

    if not query_raw:
        return {"query": q, "results": []}

    matches = []
    for s in all_stocks:
        if s["symbol"].startswith(query_raw):
            matches.append(s)

    for s in all_stocks:
        if query_raw in s["symbol"] and s not in matches:
            matches.append(s)

    for s in all_stocks:
        if (query_norm in s["name_norm"] or q.lower() in s["name"].lower()) and s not in matches:
            matches.append(s)

    return {"query": q, "results": matches[:12]}

@app.get("/api/stocks")
def get_ctck_list():
    return CTCK_STOCKS

@app.get("/api/watchlist/quotes")
async def get_watchlist_quotes_api(symbols: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not symbols or not symbols.strip():
        raise HTTPException(status_code=400, detail="Danh sách mã cổ phiếu không được rỗng")
    raw_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not raw_list:
        raise HTTPException(status_code=400, detail="Cần cung cấp ít nhất một mã cổ phiếu hợp lệ")
    from watchlist_quote_service import get_watchlist_quotes
    try:
        return await get_watchlist_quotes(raw_list)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi lấy dữ liệu giá danh mục: {exc}")


@app.get("/api/dnse/realtime/{symbol}")
async def get_dnse_realtime(symbol: str, response: Response, timeout: float = 6.0):
    try:
        response.headers["Cache-Control"] = "no-store"
        safe_timeout = max(1.0, min(float(timeout), 12.0))
        return await get_dnse_realtime_snapshot(symbol, timeout_seconds=safe_timeout)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi kết nối DNSE realtime cho {symbol}: {str(e)}")

@app.get("/api/analyze/{symbol}")
def analyze_stock(symbol: str, response: Response):
    try:
        response.headers["Cache-Control"] = "no-store"
        data = analyze_security_stock(symbol)
        return data
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi phân tích mã {symbol}: {str(e)}")

@app.post("/api/ai_analysis/{symbol}")
def get_ai_analysis(symbol: str, x_lp_user_action: Optional[str] = Header(default=None)):
    try:
        _require_explicit_deepseek_action(x_lp_user_action)
        from ai_advisor_engine import generate_ai_advisor_analysis
        data = analyze_security_stock(symbol)
        _load_deep_history(symbol.upper(), data)
        peer_data = _build_quant_decision(symbol.upper(), data)
        data["premium_analysis"] = build_premium_analysis(data, peer_data)
        ai_report = generate_ai_advisor_analysis(symbol, data)
        return {"symbol": symbol.upper(), "ai_advisor": ai_report}
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tạo báo cáo AI cho mã {symbol}: {str(e)}")


@app.get("/api/quant/{symbol}")
def get_quant_analysis(symbol: str):
    try:
        data = analyze_security_stock(symbol)
        peer_data = _build_quant_decision(symbol.upper(), data)
        return {
            "symbol": symbol.upper(),
            "decision_framework": data["decision_framework"],
            "peer_symbols": peer_data.get("peer_symbols", []),
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tính Quant cho mã {symbol}: {str(e)}")

@app.get("/api/ai_news/{symbol}")
def get_ai_news(symbol: str, response: Response):
    try:
        response.headers["Cache-Control"] = "no-store"
        from ai_advisor_engine import generate_news_feed
        news_data = generate_news_feed(symbol)
        return {"symbol": symbol.upper(), "widget_hot_news": news_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lấy tin tức có grounding cho {symbol}: {str(e)}")


@app.get("/api/news-image/{token}")
def get_news_image(token: str):
    if not re.fullmatch(r"[a-f0-9]{24}", token):
        raise HTTPException(status_code=400, detail="Mã ảnh không hợp lệ.")
    try:
        from ai_advisor_engine import fetch_registered_news_image
        content, media_type = fetch_registered_news_image(token)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Không tải được ảnh bài báo: {exc}")


@app.get("/api/peers/{symbol}")
def get_peers(symbol: str, response: Response, peers: Optional[str] = None, refresh: bool = False):
    try:
        response.headers["Cache-Control"] = "no-store"
        if peers is not None:
            peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]
        else:
            peer_list = None
        try:
            return get_peer_comparison(symbol, peer_list, force_refresh=refresh)
        except TypeError:
            # Older signature without force_refresh kwarg (defensive)
            return get_peer_comparison(symbol, peer_list)
    except Exception as e:
        print(f"Warning in get_peers route for {symbol}: {e}")
        try:
            return get_peer_comparison(symbol, None)
        except Exception:
            raise HTTPException(status_code=500, detail=f"Lỗi so sánh cùng ngành mã {symbol}: {str(e)}")


@app.get("/api/peers/{symbol}/snapshot/{snapshot_id}")
def get_peer_snapshot(symbol: str, snapshot_id: int):
    """Return the raw financial statement payload used to compute the matrix.

    This is the audit endpoint for Section 5 — every cell in the peer
    table carries a `snapshot_id` and the UI can deep-link here to show
    the user which BCTC row each number came from.
    """
    from peer_accuracy_store import get_financial_snapshot_by_id

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,5}", symbol):
        raise HTTPException(status_code=400, detail="Mã cổ phiếu không hợp lệ")
    if snapshot_id <= 0:
        raise HTTPException(status_code=400, detail="snapshot_id không hợp lệ")
    snap = get_financial_snapshot_by_id(snapshot_id)
    if not snap or snap.get("symbol") != symbol.upper():
        raise HTTPException(status_code=404, detail="Không tìm thấy snapshot")
    return snap


@app.get("/api/peers/{symbol}/metric/{metric_snapshot_id}")
def get_peer_metric_snapshot(symbol: str, metric_snapshot_id: int):
    """Return the cached 15-metric pack + provenance manifest for audit."""
    from peer_accuracy_store import get_metric_snapshot

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,5}", symbol):
        raise HTTPException(status_code=400, detail="Mã cổ phiếu không hợp lệ")
    if metric_snapshot_id <= 0:
        raise HTTPException(status_code=400, detail="metric_snapshot_id không hợp lệ")
    snap = get_metric_snapshot(metric_snapshot_id)
    if not snap or snap.get("symbol") != symbol.upper():
        raise HTTPException(status_code=404, detail="Không tìm thấy metric snapshot")
    return snap


@app.get("/api/peers/store/summary")
def get_peer_store_summary():
    """Health check for the persistent BCTC store backing the matrix."""
    from peer_accuracy_store import store_summary
    return store_summary()


@app.get("/api/corporate-calendar")
def get_corporate_calendar_api(
    response: Response,
    start: Optional[date] = None,
    end: Optional[date] = None,
    refresh: bool = False,
):
    try:
        from corporate_calendar_engine import get_corporate_calendar
        today = datetime.now().date()
        start_date = start or (today - timedelta(days=7))
        end_date = end or (today + timedelta(days=7))
        response.headers["Cache-Control"] = "no-store"
        return get_corporate_calendar(start_date, end_date, force_refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tải lịch doanh nghiệp: {str(e)}")

@app.get("/api/heatmap/data")
def get_heatmap_data(response: Response, refresh: bool = False):
    try:
        from heatmap_engine import fetch_market_heatmap_data
        response.headers["Cache-Control"] = "no-store"
        return fetch_market_heatmap_data(force_refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lấy dữ liệu bản đồ nhiệt: {str(e)}")


def _summarize_intraday_for_timeline(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Strip per-stock rows from a full heatmap payload so timeline responses
    stay small. Keeps only the top-level summary, market_session, quant
    snapshot, and per-sector summary fields. The UI only needs aggregate
    metrics to drive the slider and tooltip; drilling into a stock still
    uses the latest live snapshot from /api/heatmap/data."""
    summary_sectors = []
    for sec in payload.get("sectors", []) or []:
        if not isinstance(sec, dict):
            continue
        summary_sectors.append({
            "name": sec.get("name"),
            "code": sec.get("code"),
            "avg_change_pct": sec.get("avg_change_pct"),
            "flow_score": sec.get("flow_score"),
            "breadth_pct": sec.get("breadth_pct"),
            "net_breadth_pct": sec.get("net_breadth_pct"),
            "directional_participation_pct": sec.get("directional_participation_pct"),
            "liquidity_share_pct": sec.get("liquidity_share_pct"),
            "total_market_cap": sec.get("total_market_cap"),
            "total_trading_value": sec.get("total_trading_value"),
            "stock_count": len(sec.get("stocks", []) or []),
        })
    quant = payload.get("quant_snapshot") or {}
    return {
        "schema_version": payload.get("schema_version"),
        "timestamp": payload.get("timestamp"),
        "is_market_open": payload.get("is_market_open"),
        "market_closed": payload.get("market_closed"),
        "market_session": payload.get("market_session"),
        "snapshot_frozen": payload.get("snapshot_frozen", False),
        "summary": payload.get("summary"),
        "quant_snapshot": {
            "market_temperature": quant.get("market_temperature"),
            "market_regime": quant.get("market_regime"),
            "model_version": quant.get("model_version"),
            "heat_confidence": quant.get("heat_confidence"),
            "breadth_pct": quant.get("breadth_pct"),
            "breadth_available": quant.get("breadth_available"),
            "breadth_sample_size": quant.get("breadth_sample_size"),
            "advance_share_active_pct": quant.get("advance_share_active_pct"),
            "directional_participation_pct": quant.get("directional_participation_pct"),
            "net_breadth_pct": quant.get("net_breadth_pct"),
            "advance_decline_ratio": quant.get("advance_decline_ratio"),
            "advance_decline_state": quant.get("advance_decline_state"),
            "active_ratio_pct": quant.get("active_ratio_pct"),
            "inactive_count": quant.get("inactive_count"),
            "top5_liquidity_share_pct": quant.get("top5_liquidity_share_pct"),
            "top10_liquidity_share_pct": quant.get("top10_liquidity_share_pct"),
            "top20_liquidity_share_pct": quant.get("top20_liquidity_share_pct"),
            "liquidity_hhi": quant.get("liquidity_hhi"),
            "effective_stock_count": quant.get("effective_stock_count"),
            "concentration_state": quant.get("concentration_state"),
            "concentration_baseline": quant.get("concentration_baseline"),
            "breadth_stability_pct": quant.get("breadth_stability_pct"),
            "snapshot_id": quant.get("snapshot_id"),
        },
        "data_lineage": payload.get("data_lineage"),
        "sectors": summary_sectors,
    }


@app.get("/api/heatmap/timeline")
def get_heatmap_timeline(response: Response, date: Optional[str] = None):
    """Return all intraday timeline checkpoints for `date` (default today UTC+7).

    Response is bounded to INTRADAY_MAX_PER_DAY items (~80). Each entry is
    `summarized` — no per-stock arrays — so the scrubber can replay the day
    without shipping the full heatmap payload across the wire for every tick.
    Cache 5s so repeated scrub events don't hammer the server.
    """
    try:
        from heatmap_engine import (
            get_intraday_snapshots,
            get_vn_now,
            init_db_snapshot,
        )
        init_db_snapshot()
        target_date = date or get_vn_now().strftime("%Y-%m-%d")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
            raise HTTPException(status_code=400, detail="date không hợp lệ (YYYY-MM-DD)")
        items = get_intraday_snapshots(target_date)
        items = [
            {
                "snapshot_time": entry["snapshot_time"],
                "session_phase": entry["session_phase"],
                "payload": _summarize_intraday_for_timeline(entry["payload"]),
            }
            for entry in items
        ]
        response.headers["Cache-Control"] = "public, max-age=5"
        return {
            "date": target_date,
            "count": len(items),
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tải timeline: {str(e)}")


@app.get("/api/heatmap/timeline/latest")
def get_heatmap_timeline_latest(response: Response):
    """Return the freshest intraday checkpoint (any trade date)."""
    try:
        from heatmap_engine import get_latest_intraday_snapshot
        entry = get_latest_intraday_snapshot()
        if not entry:
            return {"snapshot_time": None, "session_phase": None, "payload": None}
        return {
            "snapshot_time": entry["snapshot_time"],
            "session_phase": entry["session_phase"],
            "payload": _summarize_intraday_for_timeline(entry["payload"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tải snapshot timeline: {str(e)}")

@app.get("/api/heatmap/ai_insight")
@app.post("/api/heatmap/ai_insight")
def get_heatmap_ai_insight(x_lp_user_action: Optional[str] = Header(default=None)):
    try:
        _require_explicit_deepseek_action(x_lp_user_action)
        from heatmap_engine import fetch_market_heatmap_data, generate_deepseek_heatmap_insight
        heatmap_data = fetch_market_heatmap_data()
        insight = generate_deepseek_heatmap_insight(heatmap_data)
        return insight
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tạo phân tích Lộc Phát AI cho Bản đồ nhiệt: {str(e)}")

@app.post("/api/heatmap/weekly_analysis")
def get_weekly_analysis(x_lp_user_action: Optional[str] = Header(default=None)):
    """
    Weekly trading analysis - only available after 15:00 on Fridays.
    Analyzes the last 5 trading days (stored in heatmap_snapshots).
    """
    try:
        _require_explicit_deepseek_action(x_lp_user_action)

        from datetime import datetime
        import os

        # Check if it's Friday after 15:00 or weekend (Vietnam time)
        now = datetime.now()
        # Simple time check - allow if Friday >= 15:00 or Saturday or Sunday
        if now.weekday() == 4 and now.hour < 15:
            raise HTTPException(
                status_code=403,
                detail="Phân tích tuần chỉ mở sau 15:00 thứ 6. Vui lòng quay lại sau."
            )

        from heatmap_engine import generate_weekly_analysis
        report = generate_weekly_analysis()
        return report
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi phân tích tuần: {str(e)}")


@app.get("/api/backtest/rsi/{symbol}")
def rsi_backtest(
    symbol: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    rsi_period: int = 14,
    lookback: int = 20,
    exit_strategy: str = "time",
    holding_days: int = 20,
    rsi_entry_min: float = 40.0,
    rsi_entry_max: float = 60.0,
    # v2 parameters
    include_short: bool = False,
    max_concurrent_trades: int = 1,
    commission_pct: float = 0.0,
    slippage_pct: float = 0.0,
    position_mode: str = "full",
    position_size_pct: float = 100.0,
    confirm_timeframe: str = "",
    confirm_rsi_min: float = 50.0,
    confirm_rsi_max: float = 50.0,
    trend_filter: str = "none",
    market_index: str = "^VNINDEX",
    initial_capital: float = 100_000_000.0,
):
    """RSI Divergence Backtest API for Lộc Phát Securities (v2)."""
    try:
        return run_backtest(
            symbol=symbol.upper(),
            start=start,
            end=end,
            rsi_period=rsi_period,
            lookback=lookback,
            exit_strategy=exit_strategy,
            holding_days=holding_days,
            rsi_entry_min=rsi_entry_min,
            rsi_entry_max=rsi_entry_max,
            include_short=include_short,
            max_concurrent_trades=max_concurrent_trades,
            commission_pct=commission_pct,
            slippage_pct=slippage_pct,
            position_mode=position_mode,
            position_size_pct=position_size_pct,
            confirm_timeframe=confirm_timeframe,
            confirm_rsi_min=confirm_rsi_min,
            confirm_rsi_max=confirm_rsi_max,
            trend_filter=trend_filter,
            market_index=market_index,
            initial_capital=initial_capital,
        )
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi backtest RSI cho {symbol}: {str(e)}")


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
