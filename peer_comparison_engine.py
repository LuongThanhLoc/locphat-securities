import pandas as pd
import numpy as np
import concurrent.futures
import time
from typing import Dict, Any, List, Optional
from ctck_analyzer import analyze_security_stock
from sector_mapping import get_sector_info
from data_freshness import now_vn_iso
from dnse_realtime import get_dnse_latest_price_snapshot
from peer_accuracy_store import save_financial_snapshot, save_metric_snapshot, store_summary

# Default Peer Mapping for Top Sectors
DEFAULT_SECTOR_PEERS = {
    # Chứng khoán
    "SSI": ["VND", "VCI", "HCM", "MBS"],
    "VND": ["SSI", "VCI", "HCM", "SHS"],
    "VCI": ["SSI", "VND", "HCM", "FTS"],
    "HCM": ["SSI", "VND", "VCI", "MBS"],
    "SHS": ["SSI", "VND", "MBS", "VIX"],
    "MBS": ["SSI", "VND", "VCI", "SHS"],
    "FTS": ["VCI", "BSI", "SSI", "CTS"],
    "BSI": ["FTS", "CTS", "AGR", "SSI"],
    "VIX": ["SHS", "VND", "ORS", "SSI"],

    # Ngân hàng
    "VCB": ["BID", "CTG", "TCB", "MBB"],
    "TCB": ["MBB", "VPB", "ACB", "VCB"],
    "MBB": ["TCB", "ACB", "VPB", "CTG"],
    "BID": ["VCB", "CTG", "TCB", "MBB"],
    "CTG": ["BID", "VCB", "TCB", "MBB"],
    "ACB": ["TCB", "MBB", "VPB", "STB"],
    "VPB": ["TCB", "MBB", "ACB", "HDB"],
    "STB": ["ACB", "EIB", "MSB", "SHB"],
    "HDB": ["VPB", "VIB", "TPB", "MSB"],
    "LPB": ["TPB", "MSB", "SSB", "SHB"],
    "TPB": ["HDB", "VIB", "LPB", "MSB"],
    "MSB": ["SSB", "LPB", "SHB", "EIB"],

    # Bất động sản
    "VHM": ["NVL", "PDR", "DIG", "DXG"],
    "NVL": ["VHM", "PDR", "DXG", "KDH"],
    "PDR": ["NVL", "DIG", "DXG", "KDH"],
    "DIG": ["PDR", "NVL", "DXG", "KDH"],
    "DXG": ["PDR", "DIG", "KDH", "NLG"],
    "KDH": ["DXG", "NLG", "DIG", "PDR"],
    "NLG": ["KDH", "DXG", "BCM", "IJC"],
    "VRE": ["DXG", "NVL", "KBC", "IDC"],
    "BCM": ["NLG", "KBC", "IDC", "VHM"],
    "KBC": ["BCM", "IDC", "SZC", "VRE"],

    # Thép & Vật liệu
    "HPG": ["HSG", "NKG", "VGS", "SMC"],
    "HSG": ["HPG", "NKG", "SMC", "VGS"],
    "NKG": ["HPG", "HSG", "SMC", "VGS"],

    # Xây dựng
    "CTD": ["HBC", "HHV", "VCG", "KSB"],
    "HBC": ["CTD", "HHV", "VCG", "LCG"],
    "HHV": ["CTD", "VCG", "DPG", "CII"],
    "VCG": ["CTD", "HBC", "HHV", "LCG"],

    # Bán lẻ & Tiêu dùng
    "MWG": ["FRT", "PNJ", "MCH", "DGW"],
    "FRT": ["MWG", "DGW", "PNJ", "MCH"],
    "PNJ": ["MWG", "FRT", "MCH", "DGW"],
    "DGW": ["MWG", "FRT", "MCH", "PET"],
    "PET": ["MWG", "FRT", "DGW", "MCH"],
    "MCH": ["MWG", "PNJ", "DGW", "PET"],
    "VNM": ["MSN", "SAB", "MCM", "KDC"],
    "MSN": ["VNM", "SAB", "MWG", "MCM"],
    "SAB": ["VNM", "MSN", "MCM", "KDC"],

    # Công nghệ & Viễn thông
    "FPT": ["CMG", "FOX", "ELC", "CTR"],
    "CMG": ["FPT", "FOX", "ELC", "ITD"],
    "ELC": ["FPT", "CMG", "FOX", "ITD"],

    # Năng lượng & Tiện ích
    "POW": ["GAS", "NT2", "QTP", "REE"],
    "GAS": ["POW", "PLX", "PVD", "BSR"],
    "REE": ["POW", "GEG", "PC1", "BWE"],
    "PVD": ["PVS", "PVC", "GAS", "PVT"],
    "PVS": ["PVD", "PVC", "PVT", "GAS"],
    "PLX": ["GAS", "BSR", "OIL", "PVT"],

    # Dịch vụ tài chính & Cho vay tiêu dùng
    "F88": ["EVF", "IPA", "FIT", "TVC"],
    "EVF": ["F88", "IPA", "FIT", "TVC"],
    "IPA": ["EVF", "FIT", "TVC", "F88"],
    "FIT": ["EVF", "IPA", "TVC", "F88"],
    "TVC": ["EVF", "IPA", "FIT", "F88"],

    # Sản xuất / Hóa chất / Thủy sản
    "DGC": ["DCM", "DPM", "CSV", "BFC"],
    "DCM": ["DGC", "DPM", "CSV", "BFC"],
    "DPM": ["DGC", "DCM", "CSV", "BFC"],
    "VHC": ["ANV", "GMD", "HAH", "VJC"],
    "ANV": ["VHC", "GMD", "HAH", "VGR"],
    "GMD": ["HAH", "VHC", "ANV", "VJC"],
    "HAH": ["GMD", "VHC", "ANV", "VJC"],
    "VJC": ["HVN", "ACV", "GMD", "HAH"],
    "HVN": ["VJC", "ACV", "GMD", "HAH"],
    "GVR": ["DPR", "VLB", "PHR", "MSR"],
    "DHG": ["IMP", "DBD", "VHC", "DCM"],
}

ARCHETYPE_FALLBACK_PEERS = {
    "SECURITIES": ["SSI", "VND", "VCI", "MBS"],
    "FINANCIAL_SERVICES": ["EVF", "IPA", "FIT", "TVC"],
    "BANKING": ["VCB", "CTG", "TCB", "MBB"],
    "REAL_ESTATE": ["VHM", "NVL", "PDR", "DIG"],
    "RETAIL": ["MWG", "FRT", "PNJ", "DGW"],
    "RETAIL_CONSUMER": ["MWG", "FRT", "PNJ", "MSN"],
    "TECH_TELECOM": ["FPT", "CMG", "ELC", "FOX"],
    "CONSTRUCTION": ["CTD", "HHV", "VCG", "HBC"],
    "UTILITIES_ENERGY": ["POW", "GAS", "REE", "PVD"],
    "MANUFACTURING_GENERAL": ["HPG", "HSG", "DGC", "VHC"]
}

PEER_METRICS_CACHE = {}
PEER_CACHE_TTL_SECONDS = 60

def get_fallback_company_metrics(symbol: str) -> Dict[str, Any]:
    """
    Returns null metrics if crawling fails so missing data cannot rank as a real zero.
    """
    symbol_upper = symbol.upper()
    missing_metrics = {
        "market_cap": None, "pe": None, "pb": None, "peg": None, "ev_ebitda": None,
        "roe": None, "roa": None, "gross_margin": None, "net_margin": None,
        "dsi": None, "ccc": None, "asset_turnover": None, "revenue_yoy": None,
        "npat_yoy": None, "debt_to_assets": None
    }
    return {
        "symbol": symbol_upper,
        "organ_name": f"Công ty {symbol_upper}",
        "price": 0.0,
        "metrics": missing_metrics,
        "has_data": False,
        "snapshot_id": None
    }

def get_single_company_metrics(symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Extracts standard 15 key metrics for a given company symbol with real-time price synchronization,
    cryptographic SHA-256 snapshot persistence, and fallback protection.
    """
    symbol_upper = symbol.upper().strip()
    if not force_refresh:
        cached = PEER_METRICS_CACHE.get(symbol_upper)
        if cached and time.time() - cached[0] < PEER_CACHE_TTL_SECONDS:
            return cached[1]

    try:
        stock_data = analyze_security_stock(symbol_upper)
    except (Exception, SystemExit, BaseException) as e:
        print(f"Warning: analyze_security_stock failed for peer {symbol_upper}: {e}")
        stock_data = {}

    if not stock_data or not isinstance(stock_data, dict) or not stock_data.get('valuation'):
        fallback_res = get_fallback_company_metrics(symbol_upper)
        PEER_METRICS_CACHE[symbol_upper] = (time.time(), fallback_res)
        return fallback_res

    def optional_number(value: Any) -> Optional[float]:
        try:
            if value is None or pd.isna(value):
                return None
            parsed = float(value)
            return parsed if np.isfinite(parsed) else None
        except (TypeError, ValueError):
            return None

    # Sync Real-time Market Price from DNSE
    current_price = optional_number(stock_data.get('current_price')) or 0.0
    price_source = stock_data.get("data_quality", {}).get("price_source", "Vietcap snapshot")
    price_as_of = stock_data.get("data_quality", {}).get("price_as_of")

    try:
        dnse_snap = get_dnse_latest_price_snapshot(symbol_upper)
        dnse_price = optional_number(dnse_snap.get("price_vnd"))
        if dnse_price and dnse_price > 0:
            current_price = dnse_price
            price_source = dnse_snap.get("source", "DNSE REST live trade")
            price_as_of = dnse_snap.get("exchange_time") or dnse_snap.get("fetched_at")
    except Exception as exc:
        print(f"Notice: Live price sync for {symbol_upper} fallback to overview price: {exc}")

    val = stock_data.get('valuation', {})
    pm = stock_data.get('peer_metrics', {})
    issue_share_m = optional_number(stock_data.get('issue_share_million'))
    archetype = stock_data.get('archetype', 'MANUFACTURING_GENERAL')

    # Real-time recalculated Valuation Metrics
    if issue_share_m and issue_share_m > 0 and current_price > 0:
        market_cap = (current_price * issue_share_m * 1e6) / 1e9
    else:
        market_cap = optional_number(pm.get('market_cap', stock_data.get('market_cap_billion')))

    eps = optional_number(val.get('eps'))
    bvps = optional_number(val.get('bvps'))

    pe = (current_price / eps) if (current_price > 0 and eps and eps > 0) else optional_number(pm.get('pe', val.get('pe_ratio')))
    pb = (current_price / bvps) if (current_price > 0 and bvps and bvps > 0) else optional_number(pm.get('pb', val.get('pb_ratio')))

    npat_yoy = optional_number(pm.get('npat_yoy'))
    peg = round(pe / npat_yoy, 2) if (pe and pe > 0 and npat_yoy and 5 <= npat_yoy <= 100) else optional_number(pm.get('peg'))
    ev_ebitda = optional_number(pm.get('ev_ebitda'))

    # Profitability Metrics
    roe = optional_number(pm.get('roe', val.get('roe_ratio')))
    roa = optional_number(pm.get('roa'))
    gross_margin = optional_number(pm.get('gross_margin'))
    net_margin = optional_number(pm.get('net_margin'))

    # Operational Efficiency (N/A for Financial/Banking/Real Estate)
    if archetype in {"BANKING", "BANK", "SECURITIES", "FINANCIAL_SERVICES", "REAL_ESTATE"}:
        dsi = None
        ccc = None
    else:
        dsi = optional_number(pm.get('dsi'))
        ccc = optional_number(pm.get('ccc'))

    asset_turnover = optional_number(pm.get('asset_turnover'))

    # Growth & Risk Metrics
    revenue_yoy = optional_number(pm.get('revenue_yoy'))
    debt_to_assets = optional_number(pm.get('debt_to_assets'))

    def rounded(value: Optional[float], digits: int = 2) -> Optional[float]:
        return round(value, digits) if value is not None else None

    metrics_pack = {
        # 1. QUY MÔ & ĐỊNH GIÁ
        "market_cap": rounded(market_cap, 1),
        "pe": rounded(pe),
        "pb": rounded(pb),
        "peg": rounded(peg),
        "ev_ebitda": rounded(ev_ebitda),

        # 2. SỨC MẠNH SINH LỜI
        "roe": rounded(roe),
        "roa": rounded(roa),
        "gross_margin": rounded(gross_margin),
        "net_margin": rounded(net_margin),

        # 3. HIỆU QUẢ VẬN HÀNH
        "dsi": rounded(dsi, 1),
        "ccc": rounded(ccc, 1),
        "asset_turnover": rounded(asset_turnover),

        # 4. TĂNG TRƯỞNG & RỦI RO
        "revenue_yoy": rounded(revenue_yoy),
        "npat_yoy": rounded(npat_yoy),
        "debt_to_assets": rounded(debt_to_assets)
    }

    # Store Audit Provenance Snapshot
    reported_period = stock_data.get("latest_quarter", "N/A")
    fin_source = stock_data.get("data_quality", {}).get("financial_statement_source", "Vietcap public REST")
    snapshot_payload = {
        "symbol": symbol_upper,
        "organ_name": stock_data.get('organ_name', f"Công ty {symbol_upper}"),
        "current_price": current_price,
        "price_source": price_source,
        "price_as_of": price_as_of,
        "reported_period": reported_period,
        "archetype": archetype,
        "metrics": metrics_pack,
        "valuation_raw": val,
        "data_quality": stock_data.get("data_quality", {})
    }

    snapshot_id = save_financial_snapshot(
        symbol=symbol_upper,
        period=reported_period,
        source=f"{price_source} / {fin_source}",
        source_url=f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{symbol_upper}/financial-statement",
        payload=snapshot_payload,
        reported_at=stock_data.get("data_quality", {}).get("statement_reported_at")
    )

    # Save detailed metric provenance traces
    for m_key, m_val in metrics_pack.items():
        save_metric_snapshot(
            symbol=symbol_upper,
            metric_key=m_key,
            raw_inputs={"current_price": current_price, "eps": eps, "bvps": bvps, "issue_share_m": issue_share_m},
            calculated_value=m_val,
            formula_name=f"Formula_{m_key}"
        )

    result = {
        "symbol": symbol_upper,
        "organ_name": stock_data.get('organ_name', f"Công ty {symbol_upper}"),
        "price": current_price,
        "as_of": stock_data.get("as_of"),
        "reported_period": reported_period,
        "archetype": archetype,
        "sector_name": stock_data.get("sector_name"),
        "has_data": True,
        "snapshot_id": snapshot_id,
        "metrics": metrics_pack,
        "metric_source": fin_source,
        "price_source": price_source,
    }
    PEER_METRICS_CACHE[symbol_upper] = (time.time(), result)
    return result


METRIC_DEFINITIONS = [
    # 1. QUY MÔ & ĐỊNH GIÁ
    {"key": "market_cap", "name": "Vốn hóa thị trường", "unit": "Tỷ VNĐ", "category": "1. QUY MÔ & ĐỊNH GIÁ", "icon": "fa-coins", "better": "higher"},
    {"key": "pe", "name": "P/E (TTM)", "unit": "Lần", "category": "1. QUY MÔ & ĐỊNH GIÁ", "icon": "fa-chart-pie", "better": "lower"},
    {"key": "pb", "name": "P/B (D)", "unit": "Lần", "category": "1. QUY MÔ & ĐỊNH GIÁ", "icon": "fa-book-bookmark", "better": "lower"},
    {"key": "peg", "name": "PEG", "unit": "Hệ số", "category": "1. QUY MÔ & ĐỊNH GIÁ", "icon": "fa-arrow-up-right-dots", "better": "lower", "note": "Chỉ hiển thị khi tăng trưởng LNST TTM dương 5-100%; ngoài vùng này PEG dễ bị méo bởi nền thấp hoặc khoản bất thường."},
    {"key": "ev_ebitda", "name": "EV/EBITDA", "unit": "Lần", "category": "1. QUY MÔ & ĐỊNH GIÁ", "icon": "fa-calculator", "better": "lower"},

    # 2. SỨC MẠNH SINH LỜI
    {"key": "roe", "name": "ROE (TTM)", "unit": "%", "category": "2. SỨC MẠNH SINH LỜI", "icon": "fa-percent", "better": "higher"},
    {"key": "roa", "name": "ROA (TTM)", "unit": "%", "category": "2. SỨC MẠNH SINH LỜI", "icon": "fa-chart-line", "better": "higher"},
    {"key": "gross_margin", "name": "Biên Lợi Nhuận Gộp", "unit": "%", "category": "2. SỨC MẠNH SINH LỜI", "icon": "fa-sack-dollar", "better": "higher"},
    {"key": "net_margin", "name": "Biên Lợi Nhuận Sau Thuế", "unit": "%", "category": "2. SỨC MẠNH SINH LỜI", "icon": "fa-money-bill-trend-up", "better": "higher"},

    # 3. HIỆU QUẢ VẬN HÀNH
    {"key": "dsi", "name": "Số ngày tồn kho (DSI)", "unit": "Ngày", "category": "3. HIỆU QUẢ VẬN HÀNH", "icon": "fa-boxes-stacked", "better": "lower", "note": "Không áp dụng cho bất động sản, ngân hàng và chứng khoán.", "not_applicable_sectors": ["BANKING", "BANK", "SECURITIES", "FINANCIAL_SERVICES", "REAL_ESTATE"]},
    {"key": "ccc", "name": "Chu kỳ tiền mặt (CCC)", "unit": "Ngày", "category": "3. HIỆU QUẢ VẬN HÀNH", "icon": "fa-rotate", "better": "lower", "note": "Không áp dụng cho bất động sản, ngân hàng và chứng khoán.", "not_applicable_sectors": ["BANKING", "BANK", "SECURITIES", "FINANCIAL_SERVICES", "REAL_ESTATE"]},
    {"key": "asset_turnover", "name": "Vòng quay tài sản", "unit": "Lần", "category": "3. HIỆU QUẢ VẬN HÀNH", "icon": "fa-gauge-high", "better": "higher"},

    # 4. TĂNG TRƯỞNG & RỦI RO
    {"key": "revenue_yoy", "name": "Tăng trưởng Doanh Thu YoY", "unit": "%", "category": "4. TĂNG TRƯỞNG & RỦI RO", "icon": "fa-arrow-trend-up", "better": "higher"},
    {"key": "npat_yoy", "name": "Tăng trưởng LNST Mẹ YoY", "unit": "%", "category": "4. TĂNG TRƯỞNG & RỦI RO", "icon": "fa-chart-simple", "better": "higher"},
    {"key": "debt_to_assets", "name": "Tổng Nợ / Tổng Tài Sản", "unit": "%", "category": "4. TĂNG TRƯỞNG & RỦI RO", "icon": "fa-shield-cat", "better": "lower"}
]

def get_peer_comparison(target_symbol: str, peer_symbols: Optional[List[str]] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Returns peer comparison matrix for 15 metrics across target symbol and peer list with real-time price accuracy & SHA-256 provenance audit.
    """
    target_symbol = target_symbol.upper().strip()
    if force_refresh:
        PEER_METRICS_CACHE.clear()

    if peer_symbols is None:
        if target_symbol in DEFAULT_SECTOR_PEERS:
            peer_symbols = DEFAULT_SECTOR_PEERS[target_symbol]
        else:
            from sector_mapping import get_dynamic_icb_peers, SECTOR_DEFINITIONS
            dynamic_peers = get_dynamic_icb_peers(target_symbol, limit=4)
            if dynamic_peers and len(dynamic_peers) >= 2:
                peer_symbols = dynamic_peers
            else:
                info = get_sector_info(target_symbol)
                sec_name = info.get("sector")
                found_peers = []
                for sec_key, sec_data in SECTOR_DEFINITIONS.items():
                    if sec_data.get("sector") == sec_name or sec_key == sec_name:
                        found_peers = [s for s in sec_data.get("symbols", []) if s != target_symbol]
                        break

                if found_peers and len(found_peers) >= 2:
                    peer_symbols = found_peers[:4]
                else:
                    arch = info.get("archetype", "SECURITIES")
                    peer_symbols = ARCHETYPE_FALLBACK_PEERS.get(arch, ARCHETYPE_FALLBACK_PEERS["SECURITIES"])

    # Filter out target symbol from peer list, deduplicate, and limit to max 8 peers
    seen = set()
    clean_peers = []
    for p in peer_symbols:
        sym = p.upper().strip()
        if sym and sym != target_symbol and sym not in seen:
            seen.add(sym)
            clean_peers.append(sym)
    clean_peers = clean_peers[:8]
    all_symbols = [target_symbol] + clean_peers

    # Concurrently fetch all company metrics with live price & SHA-256 provenance
    results_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(all_symbols))) as executor:
        future_to_symbol = {executor.submit(get_single_company_metrics, s, force_refresh): s for s in all_symbols}
        for future in concurrent.futures.as_completed(future_to_symbol):
            sym = future_to_symbol[future]
            try:
                results_map[sym] = future.result(timeout=15)
            except (Exception, SystemExit, BaseException) as e:
                print(f"Error fetching peer metrics for {sym}: {e}")
                results_map[sym] = get_fallback_company_metrics(sym)

    companies_data = [results_map.get(s) or get_fallback_company_metrics(s) for s in all_symbols]

    # Calculate median metrics and coverage
    industry_avg = {}
    metric_coverage = {}
    provenance_by_company = {}

    for comp in companies_data:
        if comp and comp.get("symbol"):
            provenance_by_company[comp["symbol"]] = {
                "snapshot_id": comp.get("snapshot_id"),
                "reported_period": comp.get("reported_period"),
                "price_source": comp.get("price_source"),
                "metric_source": comp.get("metric_source"),
            }

    for mdef in METRIC_DEFINITIONS:
        k = mdef["key"]
        vals = [c["metrics"][k] for c in companies_data if c and "metrics" in c and c["metrics"].get(k) is not None]
        industry_avg[k] = round(float(np.median(vals)), 2) if vals else None
        metric_coverage[k] = len(vals)

    return {
        "target_symbol": target_symbol,
        "peer_symbols": clean_peers,
        "companies": companies_data,
        "metric_definitions": METRIC_DEFINITIONS,
        "industry_average": industry_avg,
        "aggregation_label": "Trung vị nhóm so sánh",
        "metric_coverage": metric_coverage,
        "generated_at": now_vn_iso(),
        "period_policy": "Mỗi doanh nghiệp giữ kỳ BCTC mới nhất riêng; UI phải hiển thị kỳ khi so sánh.",
        "source_policy": "BCTC chuẩn hóa Vietcap; DNSE live trade snapshot cho giá khớp lệnh real-time. Provenance băm SHA-256 100%.",
        "data_accuracy": {
            "provenance_by_company": provenance_by_company,
            "data_source_mode": "realtime_pack",
            "store_summary": store_summary()
        }
    }
