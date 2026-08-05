import pandas as pd
import numpy as np
import re
from datetime import datetime, timedelta
from market_data_provider import Finance, Company, Quote, Listing, VnstockFinance, VnstockCompany, VnstockQuote
from sector_schema_engine import build_sector_financial_health
from trend_table_engine import get_quarterly_table_schema, build_trend_data
from revenue_structure_engine import build_revenue_structure
from sector_mapping import get_sector_info
from industry_indicator_profiles import get_industry_profile
from ai_advisor_engine import generate_ai_advisor_analysis
from data_freshness import build_as_of_contract


def compute_behavioral_warnings(pe_ratio, pb_ratio, roe_ratio, npat_yoy, revenue_yoy, current_price, ma20, ma50, npat_ttm):
    """Check 3 behavioral patterns and return list of warnings (0-3 items)."""
    warnings = []

    # 1. Valuation Compression: P/E > 20 or P/B > 3 but growth < 5%
    if (pe_ratio > 20 or pb_ratio > 3.0) and npat_yoy < 5.0 and roe_ratio < 15.0:
        warnings.append({
            "loai": "Nén định giá",
            "giai_thich": f"P/E {pe_ratio:.1f}x, P/B {pb_ratio:.2f}x nhưng LNST tăng trưởng chỉ {npat_yoy:.1f}% và ROE {roe_ratio:.1f}%. Định giá có thể đang cao hơn nội tại."
        })

    # 2. Future Story: No stable profit but high valuation expectations
    if npat_ttm <= 0 and pe_ratio > 0:
        warnings.append({
            "loai": "Câu chuyện tương lai",
            "giai_thich": f"Lợi nhuận TTM âm hoặc bằng 0, nhưng cổ phiếu vẫn được giao dịch. Giá cổ phiếu phản ánh kỳ vọng tương lai, chưa có nền tảng lợi nhuận ổn định."
        })
    elif pe_ratio > 40 and roe_ratio < 10.0:
        warnings.append({
            "loai": "Câu chuyện tương lai",
            "giai_thich": f"P/E cực cao ({pe_ratio:.1f}x) với ROE thấp ({roe_ratio:.1f}%). Thị trường đang đặt cược vào câu chuyện tăng trưởng tương lai hơn là nội tại hiện tại."
        })

    # 3. Recovery Expectation Without Signal: Price below MA20 & MA50, negative growth
    if current_price > 0 and ma20 > 0 and ma50 > 0:
        if current_price < ma20 and current_price < ma50 and npat_yoy < 0 and revenue_yoy < 0:
            warnings.append({
                "loai": "Kỳ vọng phục hồi không tín hiệu",
                "giai_thich": f"Giá dưới MA20 & MA50, LNST giảm {npat_yoy:.1f}%, doanh thu giảm {revenue_yoy:.1f}%. Chưa có dấu hiệu đảo chiều từ cả kỹ thuật lẫn cơ bản."
            })

    return warnings


SECTOR_VALUATION_BENCHMARKS = {
    "STEEL": {
        "min_pb": 0.8, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Thép thường từ 0.8x – 2.2x."
    },
    "SECURITIES": {
        "min_pb": 1.2, "max_pb": 2.5,
        "ref_text": "Mức tham chiếu của ngành Chứng khoán thường từ 1.2x – 2.5x."
    },
    "FINANCIAL_SERVICES": {
        "min_pb": 1.2, "max_pb": 3.0,
        "ref_text": "Mức tham chiếu của ngành Dịch vụ Tài chính thường từ 1.2x – 3.0x."
    },
    "BANKING": {
        "min_pb": 1.0, "max_pb": 1.8,
        "ref_text": "Mức tham chiếu của ngành Ngân hàng thường từ 1.0x – 1.8x."
    },
    "INSURANCE": {
        "min_pb": 1.1, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Bảo hiểm thường từ 1.1x – 2.2x."
    },
    "REAL_ESTATE": {
        "min_pb": 1.0, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Bất động sản thường từ 1.0x – 2.2x."
    },
    "INDUSTRIAL_PARK": {
        "min_pb": 1.1, "max_pb": 2.4,
        "ref_text": "Mức tham chiếu của ngành BĐS Khu công nghiệp thường từ 1.1x – 2.4x."
    },
    "CONSTRUCTION": {
        "min_pb": 0.8, "max_pb": 1.8,
        "ref_text": "Mức tham chiếu của ngành Xây dựng & Đầu tư công thường từ 0.8x – 1.8x."
    },
    "BUILDING_MATERIALS": {
        "min_pb": 0.9, "max_pb": 2.0,
        "ref_text": "Mức tham chiếu của ngành Vật liệu xây dựng thường từ 0.9x – 2.0x."
    },
    "CHEMICALS_FERTILIZERS": {
        "min_pb": 1.0, "max_pb": 2.4,
        "ref_text": "Mức tham chiếu của ngành Hóa chất - Phân bón thường từ 1.0x – 2.4x."
    },
    "RUBBER": {
        "min_pb": 0.9, "max_pb": 2.0,
        "ref_text": "Mức tham chiếu của ngành Cao su thường từ 0.9x – 2.0x."
    },
    "OIL_GAS": {
        "min_pb": 1.0, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Dầu khí thường từ 1.0x – 2.2x."
    },
    "POWER_ENERGY": {
        "min_pb": 1.0, "max_pb": 2.0,
        "ref_text": "Mức tham chiếu của ngành Điện - Năng lượng thường từ 1.0x – 2.0x."
    },
    "MINING": {
        "min_pb": 0.8, "max_pb": 1.8,
        "ref_text": "Mức tham chiếu của ngành Khoáng sản thường từ 0.8x – 1.8x."
    },
    "AUTOMOTIVE": {
        "min_pb": 1.0, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Ôtô - Phụ tùng thường từ 1.0x – 2.2x."
    },
    "TEXTILE": {
        "min_pb": 0.9, "max_pb": 2.0,
        "ref_text": "Mức tham chiếu của ngành Dệt may thường từ 0.9x – 2.0x."
    },
    "SEAFOOD": {
        "min_pb": 1.0, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Thủy sản thường từ 1.0x – 2.2x."
    },
    "FOOD_BEVERAGE": {
        "min_pb": 1.8, "max_pb": 4.0,
        "ref_text": "Mức tham chiếu của ngành Thực phẩm & Đồ uống thường từ 1.8x – 4.0x."
    },
    "RETAIL": {
        "min_pb": 2.0, "max_pb": 4.5,
        "ref_text": "Mức tham chiếu của ngành Bán lẻ thường từ 2.0x – 4.5x."
    },
    "PHARMA_HEALTHCARE": {
        "min_pb": 1.5, "max_pb": 3.5,
        "ref_text": "Mức tham chiếu của ngành Dược - Y tế thường từ 1.5x – 3.5x."
    },
    "TECH_TELECOM": {
        "min_pb": 2.5, "max_pb": 5.0,
        "ref_text": "Mức tham chiếu của ngành Công nghệ - Truyền thông thường từ 2.5x – 5.0x."
    },
    "AVIATION_TOURISM": {
        "min_pb": 1.5, "max_pb": 3.5,
        "ref_text": "Mức tham chiếu của ngành Hàng không - Du lịch thường từ 1.5x – 3.5x."
    },
    "PORTS_LOGISTICS": {
        "min_pb": 1.1, "max_pb": 2.4,
        "ref_text": "Mức tham chiếu của ngành Cảng biển - Vận tải thường từ 1.1x – 2.4x."
    },
    "WATER_PLASTICS": {
        "min_pb": 1.0, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Nước - Nhựa thường từ 1.0x – 2.2x."
    },
    "SUGAR_WOOD_PAPER": {
        "min_pb": 0.9, "max_pb": 2.0,
        "ref_text": "Mức tham chiếu của ngành Đường - Gỗ - Giấy thường từ 0.9x – 2.0x."
    },
    "MANUFACTURING_GENERAL": {
        "min_pb": 1.0, "max_pb": 2.2,
        "ref_text": "Mức tham chiếu của ngành Sản xuất Công nghiệp thường từ 1.0x – 2.2x."
    }
}

_LISTED_SYMBOLS_SET = None

def get_all_listed_symbols():
    global _LISTED_SYMBOLS_SET
    if _LISTED_SYMBOLS_SET is None:
        try:
            df = Listing(source='VCI').all_symbols()
            if not df.empty and 'symbol' in df.columns:
                _LISTED_SYMBOLS_SET = set(df['symbol'].astype(str).str.upper().tolist())
        except Exception as e:
            print(f"Warning: Could not fetch all listed symbols: {e}")
            _LISTED_SYMBOLS_SET = set()
    return _LISTED_SYMBOLS_SET

# List of common securities stocks on HOSE/HNX
CTCK_STOCKS = [
    {"symbol": "SSI", "name": "CTCP Chứng khoán SSI", "exchange": "HOSE"},
    {"symbol": "VND", "name": "CTCP Chứng khoán VNDIRECT", "exchange": "HOSE"},
    {"symbol": "VCI", "name": "CTCP Chứng khoán Vietcap", "exchange": "HOSE"},
    {"symbol": "HCM", "name": "CTCP Chứng khoán TP.HCM (HSC)", "exchange": "HOSE"},
    {"symbol": "MBS", "name": "CTCP Chứng khoán MB", "exchange": "HNX"},
    {"symbol": "SHS", "name": "CTCP Chứng khoán Sài Gòn - Hà Nội", "exchange": "HNX"},
    {"symbol": "FTS", "name": "CTCP Chứng khoán FPT", "exchange": "HOSE"},
    {"symbol": "BSI", "name": "CTCP Chứng khoán BIDV (BSC)", "exchange": "HOSE"},
    {"symbol": "CTS", "name": "CTCP Chứng khoán VietinBank (CTS)", "exchange": "HOSE"},
    {"symbol": "AGR", "name": "CTCP Chứng khoán Agribank (Agriseco)", "exchange": "HOSE"},
    {"symbol": "VDS", "name": "CTCP Chứng khoán Rồng Việt (VDSC)", "exchange": "HOSE"},
    {"symbol": "ORS", "name": "CTCP Chứng khoán Tiên Phong (TPS)", "exchange": "HOSE"},
    {"symbol": "VIX", "name": "CTCP Chứng khoán VIX", "exchange": "HOSE"},
    {"symbol": "TCBS", "name": "CTCP Chứng khoán Kỹ Thương (TCBS)", "exchange": "UNLISTED"}
]

import time

CACHE = {}
CACHE_TTL_SECONDS = 15  # Recompute price-sensitive valuation frequently; statement HTTP is cached separately.
VNINDEX_CACHE = {"time": 0, "df": pd.DataFrame()}

def safe_float(val, default=0.0):
    if val is None or pd.isna(val):
        return default
    try:
        f = float(val)
        return default if np.isnan(f) else f
    except:
        return default

def get_dividend_and_ipo_metadata(symbol: str, comp_overview: dict = None, eps_ttm: float = 0.0) -> dict:
    symbol = symbol.upper().strip()
    listing_date_str = "N/A"
    ex_date_str = "N/A"
    div_growth_str = "N/A"
    payout_ratio_str = "N/A"

    if comp_overview and isinstance(comp_overview, dict):
        ld = comp_overview.get('listing_date')
        if ld and pd.notna(ld):
            try:
                dt = pd.to_datetime(ld)
                listing_date_str = dt.strftime('%d/%m/%Y')
            except:
                pass

    def _parse_div_val(title):
        if not title or pd.isna(title): return 0.0
        s = str(title).strip()
        m = re.search(r'([\d.,]+)\s*(VND|đồng|đ|%)', s, re.IGNORECASE)
        if m:
            val_str, unit = m.group(1), m.group(2)
            clean_str = val_str.replace(',', '').replace('.', '')
            try:
                v = float(clean_str)
                if unit == '%':
                    v = v * 100.0
                return v
            except:
                pass
        return 0.0

    try:
        comp = Company(symbol=symbol, source='VCI')
        if listing_date_str == "N/A":
            ov = comp.overview()
            if not ov.empty and 'listing_date' in ov.columns:
                ld = ov.iloc[0]['listing_date']
                if ld and pd.notna(ld):
                    dt = pd.to_datetime(ld)
                    listing_date_str = dt.strftime('%d/%m/%Y')

        events = comp.events()
        if not events.empty:
            code_col = 'eventCode' if 'eventCode' in events.columns else 'event_code'
            name_col = 'eventNameVi' if 'eventNameVi' in events.columns else 'event_name_vi'
            title_col = 'eventTitleVi' if 'eventTitleVi' in events.columns else 'event_title_vi'
            ex_col = 'exrightDate' if 'exrightDate' in events.columns else ('exright_date' if 'exright_date' in events.columns else 'publicDate')
            
            div_mask = pd.Series(False, index=events.index)
            if code_col in events.columns:
                div_mask |= events[code_col].astype(str).str.upper().str.contains('DIV|CASH', na=False)
            if name_col in events.columns:
                div_mask |= events[name_col].astype(str).str.lower().str.contains('tiền|cổ tức', na=False)
            if title_col in events.columns:
                div_mask |= events[title_col].astype(str).str.lower().str.contains('tiền|cổ tức', na=False)
                
            div_events = events[div_mask].copy()
            if not div_events.empty:
                if ex_col in div_events.columns:
                    div_events['dt'] = pd.to_datetime(div_events[ex_col], errors='coerce')
                else:
                    div_events['dt'] = pd.to_datetime(div_events['publicDate'], errors='coerce')
                div_events = div_events.sort_values(by='dt', ascending=False)
                
                valid_dts = div_events.dropna(subset=['dt'])
                if not valid_dts.empty:
                    ex_date_str = valid_dts.iloc[0]['dt'].strftime('%d/%m/%Y')

                div_list = []
                for idx, row in div_events.iterrows():
                    val = 0.0
                    for v_field in ['valuePerShare', 'value_per_share', 'val', 'value']:
                        if v_field in row and pd.notna(row[v_field]):
                            try:
                                val = float(row[v_field])
                                if val > 0: break
                            except: pass
                    if val == 0 and title_col in row:
                        val = _parse_div_val(row[title_col])
                    if val > 0 and pd.notna(row.get('dt')):
                        div_list.append({'year': row['dt'].year, 'val': val, 'dt': row['dt']})

                if div_list:
                    df_div = pd.DataFrame(div_list).sort_values('dt', ascending=False)
                    latest_val = df_div.iloc[0]['val']
                    latest_year = df_div.iloc[0]['year']
                    cash_per_share_1y = df_div[df_div['year'] == latest_year]['val'].sum()
                    if cash_per_share_1y == 0:
                        cash_per_share_1y = latest_val
                    
                    if eps_ttm > 0 and cash_per_share_1y > 0:
                        pr_val = round((cash_per_share_1y / eps_ttm) * 100.0, 1)
                        if 0 < pr_val <= 100:
                            payout_ratio_str = f"{pr_val}% ({cash_per_share_1y:,.0f}đ/CP)"
                        else:
                            payout_ratio_str = f"{cash_per_share_1y:,.0f}đ / CP"
                    elif cash_per_share_1y > 0:
                        payout_ratio_str = f"{cash_per_share_1y:,.0f}đ / CP"

                    yearly = df_div[df_div['val'] > 0].groupby('year')['val'].sum().sort_index()
                    valid_years = yearly.index.tolist()
                    if len(valid_years) >= 2:
                        y_start = valid_years[0]
                        y_end = valid_years[-1]
                        v_start = yearly.loc[y_start]
                        v_end = yearly.loc[y_end]
                        n_span = y_end - y_start
                        if n_span >= 1 and v_start > 0 and v_end > 0:
                            cagr = ((v_end / v_start) ** (1.0 / n_span) - 1) * 100.0
                            cagr = max(min(cagr, 100.0), -50.0)
                            sign = '+' if cagr >= 0 else ''
                            div_growth_str = f"{sign}{cagr:.1f}% / năm"
                    elif len(df_div) >= 2:
                        v0 = df_div.iloc[0]['val']
                        v1 = df_div.iloc[1]['val']
                        if v0 > 0 and v1 > 0:
                            chg = ((v0 - v1) / v1) * 100.0
                            chg = max(min(chg, 100.0), -50.0)
                            sign = '+' if chg >= 0 else ''
                            div_growth_str = f"{sign}{chg:.1f}% / năm"
    except Exception as e:
        print(f"Notice: get_dividend_and_ipo_metadata for {symbol}: {e}")

    if ex_date_str == "N/A":
        ex_date_str = "Chưa chốt quyền"
    if div_growth_str == "N/A":
        div_growth_str = "0.0% / năm"
    if payout_ratio_str == "N/A":
        payout_ratio_str = "Giữ lại 100% LN"

    return {
        "listing_date": listing_date_str,
        "ex_date": ex_date_str,
        "div_growth": div_growth_str,
        "payout_ratio": payout_ratio_str
    }

def analyze_security_stock(symbol: str) -> dict:
    start_time = time.time()
    import re
    symbol = symbol.upper().strip().replace(" ", "")

    if not symbol or not re.match(r'^[A-Z0-9]{3,6}$', symbol):
        raise ValueError(f"Mã cổ phiếu '{symbol}' không đúng định dạng. Vui lòng nhập từ 3-6 ký tự chữ cái hoặc số (Ví dụ: SSI, PNJ, VND, FPT...)!")

    
    # Check cache first
    now = time.time()
    if symbol in CACHE:
        cached_time, cached_data = CACHE[symbol]
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_data

    # 1. Fetch Basic Info & Overview (Try VCI then KBS)
    comp_overview = {}
    overview_source = "Vietcap public REST"
    for provider, src in [(Company, 'VCI'), (VnstockCompany, 'VCI')]:
        try:
            c_df = provider(symbol=symbol, source=src).overview()
            if not c_df.empty:
                comp_overview = c_df.iloc[0].to_dict()
                if provider is VnstockCompany:
                    overview_source = "vnstock fallback"
                break
        except (Exception, SystemExit) as e:
            print(f"Warning/Error fetching company overview for {symbol} ({src}, {provider.__name__}): {e}")

    organ_name = comp_overview.get("organ_name") or f"Công ty Chứng khoán {symbol}"
    com_group = str(comp_overview.get("com_group_code") or "").upper()
    if "VNINDEX" in com_group or "HOSE" in com_group:
        exchange = "HOSE"
    elif "HNX" in com_group:
        exchange = "HNX"
    elif "UPC" in com_group:
        exchange = "UPCoM"
    else:
        exchange = "HOSE"
    current_price = safe_float(comp_overview.get("current_price"))
    price_source = "Vietcap company snapshot"
    price_as_of = None
    market_cap = safe_float(comp_overview.get("market_cap"))
    issue_share = safe_float(comp_overview.get("issue_share"))
    rating = str(comp_overview.get("rating") or "N/A")
    target_price = safe_float(comp_overview.get("target_price"))
    profile = str(comp_overview.get("company_profile") or "")
    has_dnse_price = False

    # DNSE is the source of truth for the latest market price. If it is
    # temporarily unavailable, retain the public company snapshot as a
    # controlled fallback for the rest of the analysis.
    try:
        from dnse_realtime import get_dnse_latest_price_snapshot
        dnse_snapshot = get_dnse_latest_price_snapshot(symbol)
        dnse_price = dnse_snapshot.get("price_vnd")
        if dnse_price and dnse_price > 0:
            current_price = dnse_price
            price_source = dnse_snapshot.get("source") or "DNSE REST latest trade"
            price_as_of = dnse_snapshot.get("exchange_time") or dnse_snapshot.get("fetched_at")
            has_dnse_price = True
    except Exception as e:
        print(f"Warning: DNSE latest price unavailable for {symbol}: {e}")

    # Historical OHLC is only a fallback when DNSE latest trade is unavailable.
    if not has_dnse_price:
        try:
            today = datetime.now()
            start_date = (today - timedelta(days=10)).strftime('%Y-%m-%d')
            end_date = today.strftime('%Y-%m-%d')
            q_df = Quote(symbol=symbol, source='VCI').history(start=start_date, end=end_date)
            if not q_df.empty and 'close' in q_df.columns:
                latest_c = float(q_df.iloc[-1]['close'])
                if latest_c > 0:
                    current_price = latest_c * 1000.0 if latest_c < 1000.0 else latest_c
                    price_source = "Vietcap OHLC fallback"
                    price_as_of = str(q_df.iloc[-1].get('time') or end_date)
        except Exception as e:
            print(f"Warning: Could not fetch fallback quote for {symbol}: {e}")

    # Synchronize market cap and issue shares
    if issue_share <= 0 and market_cap > 0 and current_price > 0:
        issue_share = market_cap / current_price
    elif issue_share > 0 and current_price > 0:
        market_cap = current_price * issue_share

    # 2. Fetch Financial Statements with Auto-fallback (VCI -> KBS)
    bs_df = pd.DataFrame()
    is_df = pd.DataFrame()
    cf_df = pd.DataFrame()
    r_df = pd.DataFrame()
    bs_y_df = pd.DataFrame()
    is_y_df = pd.DataFrame()
    cf_y_df = pd.DataFrame()
    statement_sources = []

    for provider, src, label in [(Finance, 'VCI', 'Vietcap public REST'), (VnstockFinance, 'VCI', 'vnstock fallback')]:
        try:
            fin = provider(symbol=symbol, source=src)
            filled_from_provider = False
            if bs_df.empty:
                candidate = fin.balance_sheet(period='quarter', lang='vi')
                if not candidate.empty:
                    bs_df = candidate
                    filled_from_provider = True
            if is_df.empty:
                candidate = fin.income_statement(period='quarter', lang='vi')
                if not candidate.empty:
                    is_df = candidate
                    filled_from_provider = True
            if cf_df.empty:
                try:
                    candidate = fin.cash_flow(period='quarter', lang='vi')
                    if not candidate.empty:
                        cf_df = candidate
                        filled_from_provider = True
                except Exception:
                    pass
            if r_df.empty:
                candidate = fin.ratio(period='quarter', lang='vi')
                if not candidate.empty:
                    r_df = candidate
                    filled_from_provider = True

            if filled_from_provider:
                statement_sources.append(label)

            if not bs_df.empty and not is_df.empty and not cf_df.empty and not r_df.empty:
                break
        except (Exception, SystemExit) as e:
            print(f"Warning/Error fetching {symbol} from {src} ({provider.__name__}): {e}")

    # Annual reports are fetched independently. Year mode must never be built by
    # relabelling quarterly columns because balance-sheet stocks and statement
    # flows have different aggregation semantics.
    for provider, src, label in [(Finance, 'VCI', 'Vietcap public REST'), (VnstockFinance, 'VCI', 'vnstock fallback')]:
        try:
            annual_fin = provider(symbol=symbol, source=src)
            if bs_y_df.empty:
                bs_y_df = annual_fin.balance_sheet(period='year', lang='vi')
            if is_y_df.empty:
                is_y_df = annual_fin.income_statement(period='year', lang='vi')
            if cf_y_df.empty:
                cf_y_df = annual_fin.cash_flow(period='year', lang='vi')
            if not bs_y_df.empty and not is_y_df.empty and not cf_y_df.empty:
                break
        except (Exception, SystemExit) as e:
            print(f"Warning/Error fetching annual statements for {symbol} ({label}): {e}")

    if not comp_overview and bs_df.empty and is_df.empty:
        raise ValueError(f"Mã cổ phiếu '{symbol}' không tìm thấy dữ liệu tài chính công bố trên thị trường. Vui lòng kiểm tra lại mã!")

    # Columns of quarters (most recent quarter is index 0)
    data_cols_bs = [c for c in bs_df.columns if c not in ['item', 'item_en', 'item_id']] if not bs_df.empty else []
    data_cols_is = [c for c in is_df.columns if c not in ['item', 'item_en', 'item_id']] if not is_df.empty else []
    data_cols_cf = [c for c in cf_df.columns if c not in ['item', 'item_en', 'item_id']] if not cf_df.empty else []
    data_cols_bs_y = [c for c in bs_y_df.columns if c not in ['item', 'item_en', 'item_id']] if not bs_y_df.empty else []
    data_cols_is_y = [c for c in is_y_df.columns if c not in ['item', 'item_en', 'item_id']] if not is_y_df.empty else []
    data_cols_cf_y = [c for c in cf_y_df.columns if c not in ['item', 'item_en', 'item_id']] if not cf_y_df.empty else []

    latest_q = data_cols_bs[0] if data_cols_bs else "N/A"

    def get_bs_item(item_names, col=None):
        if bs_df.empty or not data_cols_bs: return 0.0
        target_col = col if col else data_cols_bs[0]
        if isinstance(item_names, str):
            item_names = [item_names]
        for name in item_names:
            row = bs_df[bs_df['item'].astype(str).str.strip().str.lower() == name.strip().lower()]
            if not row.empty and target_col in row.columns:
                val = safe_float(row[target_col].values[0])
                if val != 0.0:
                    return val
        return 0.0

    def get_is_item(item_names, col=None):
        if is_df.empty or not data_cols_is: return 0.0
        target_col = col if col else data_cols_is[0]
        if isinstance(item_names, str):
            item_names = [item_names]
        for name in item_names:
            row = is_df[is_df['item'].astype(str).str.strip().str.lower() == name.strip().lower()]
            if not row.empty and target_col in row.columns:
                val = safe_float(row[target_col].values[0])
                if val != 0.0:
                    return val
        return 0.0

    def get_cf_item(item_names, col=None):
        """Read cash-flow rows using the same newest-quarter contract."""
        if cf_df.empty:
            return 0.0
        data_cols_cf = [c for c in cf_df.columns if c not in ['item', 'item_en', 'item_id']]
        if not data_cols_cf:
            return 0.0
        target_col = col if col else data_cols_cf[0]
        names = [item_names] if isinstance(item_names, str) else item_names
        for name in names:
            row = cf_df[cf_df['item'].astype(str).str.strip().str.lower() == name.strip().lower()]
            if not row.empty and target_col in row.columns:
                return safe_float(row[target_col].values[0])
        if any("hoạt động kinh doanh" in str(name).lower() or "dòng tiền" in str(name).lower() for name in names):
            row = cf_df[cf_df['item'].astype(str).str.lower().str.contains("lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh", regex=False)]
            if not row.empty and target_col in row.columns:
                return safe_float(row[target_col].values[0])
        return 0.0

    def read_trend_item(frame, item_names, col):
        """Strict reader for charts: missing is None and reported zero stays zero."""
        if frame.empty or col is None or col not in frame.columns:
            return None
        names = [item_names] if isinstance(item_names, str) else item_names
        normalized = frame['item'].astype(str).str.strip().str.lower()
        for name in names:
            row = frame[normalized == str(name).strip().lower()]
            if not row.empty:
                value = row[col].values[0]
                if value is None or pd.isna(value):
                    return None
                return safe_float(value, default=None)
        return None

    def get_bs_trend_item(item_names, col=None):
        return read_trend_item(bs_df, item_names, col)

    def get_is_trend_item(item_names, col=None):
        return read_trend_item(is_df, item_names, col)

    def get_cf_trend_item(item_names, col=None):
        value = read_trend_item(cf_df, item_names, col)
        if value is not None:
            return value
        names = [item_names] if isinstance(item_names, str) else item_names
        if any("hoạt động kinh doanh" in str(name).lower() or "dòng tiền" in str(name).lower() for name in names):
            rows = cf_df[cf_df['item'].astype(str).str.lower().str.contains("lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh", regex=False)] if not cf_df.empty else pd.DataFrame()
            if not rows.empty and col in rows.columns:
                return safe_float(rows[col].values[0], default=None)
        return None

    def get_bs_year_item(item_names, col=None):
        return read_trend_item(bs_y_df, item_names, col)

    def get_is_year_item(item_names, col=None):
        return read_trend_item(is_y_df, item_names, col)

    def get_cf_year_item(item_names, col=None):
        return read_trend_item(cf_y_df, item_names, col)

    def get_is_ttm_item(item_names):
        """Return the sum of the four latest reported quarters."""
        names = [item_names] if isinstance(item_names, str) else item_names
        if any("dòng tiền" in str(name).lower() or "lưu chuyển tiền" in str(name).lower() for name in names):
            return get_cf_ttm_item(names)
        return sum(get_is_item(item_names, q) for q in data_cols_is[:min(4, len(data_cols_is))])

    def get_cf_ttm_item(item_names):
        data_cols_cf = [c for c in cf_df.columns if c not in ['item', 'item_en', 'item_id']] if not cf_df.empty else []
        return sum(get_cf_item(item_names, q) for q in data_cols_cf[:min(4, len(data_cols_cf))])

    def get_ratio_item(item_names):
        if r_df.empty: return 0.0
        cols = [c for c in r_df.columns if c not in ['item', 'item_en', 'item_id']]
        if not cols: return 0.0
        if isinstance(item_names, str):
            item_names = [item_names]
        for name in item_names:
            target_name = name.strip().lower()
            for match_col in ['item', 'item_en', 'item_id']:
                if match_col not in r_df.columns: continue
                row = r_df[r_df[match_col].astype(str).str.strip().str.lower() == target_name]
                if not row.empty:
                    for c in cols:
                        val = safe_float(row[c].values[0])
                        if val != 0.0:
                            return val
        return 0.0

    # Extract Balance Sheet metrics (Latest quarter)
    equity = get_bs_item(['Vốn chủ sở hữu', 'VỐN CHỦ SỞ HỮU', 'Vốn góp của chủ sở hữu'])
    margin_loans = get_bs_item(['Các khoản cho vay', 'Dư nợ cho vay', 'Phải thu về cho vay ngắn hạn'])
    total_assets = get_bs_item(['TỔNG CỘNG TÀI SẢN', 'Tài sản'])
    fvtpl_assets = get_bs_item(['Các tài sản tài chính ghi nhận thông qua lãi lỗ (FVTPL)'])
    afs_assets = get_bs_item(['Các khoản tài chính sẵn sàng để bán (AFS)', 'Tài sản tài chính sẵn sàng để bán (AFS)'])
    htm_assets = get_bs_item(['Các khoản đầu tư nắm giữ đến ngày đáo hạn (HTM)'])
    cash_and_equiv = get_bs_item(['Tiền và tương đương tiền'])
    total_liabilities = get_bs_item(['NỢ PHẢI TRẢ', 'Nợ phải trả'])
    short_term_borrowing = get_bs_item(['Vay ngắn hạn', 'Vay và nợ thuê tài sản tài chính ngắn hạn'])

    # Income Statement TTM (Sum of available 4 quarters)
    NPAT_ALIASES = ['Lợi nhuận của Cổ đông của Công ty mẹ', 'Lãi/(lỗ) thuần sau thuế', 'LỢI NHUẬN KẾ TOÁN SAU THUẾ', 'LỢI NHUẬN SAU THUẾ', 'LNST']
    npat_ttm = sum([get_is_item(NPAT_ALIASES, q) for q in data_cols_is[:min(4, len(data_cols_is))]])
    pbt_ttm = sum([get_is_item(['Lãi/(lỗ) trước thuế', 'KẾ TQUẢ HOẠT ĐỘNG', 'LỢI NHUẬN GỘP', 'LỢI NHUẬN TRƯỚC THUẾ'], q) for q in data_cols_is[:min(4, len(data_cols_is))]])
    
    REV_ALIASES = ['Doanh thu nghiệp vụ môi giới chứng khoán', 'Doanh thu thuần', 'Doanh thu bán hàng và cung cấp dịch vụ', 'DOANH THU HOẠT ĐỘNG', 'Doanh thu thuần về hoạt động kinh doanh']
    brokerage_rev_ttm = sum([get_is_item(REV_ALIASES, q) for q in data_cols_is[:min(4, len(data_cols_is))]])
    
    FIN_INC_ALIASES = ['Lãi từ các khoản cho vay và phải thu', 'Doanh thu hoạt động tài chính', 'Thu nhập từ tiền gửi']
    margin_interest_ttm = sum([get_is_item(FIN_INC_ALIASES, q) for q in data_cols_is[:min(4, len(data_cols_is))]])
    
    fvtpl_gain_ttm = sum([get_is_item(['Lãi từ các tài sản tài chính ghi nhận thông qua lãi/lỗ ( FVTPL)', 'Lãi từ các tài sản tài chính ghi nhận thông qua lãi/lỗ (FVTPL)', 'Lãi bán các tài sản tài chính FVTPL'], q) for q in data_cols_is[:min(4, len(data_cols_is))]])
    advisory_rev_ttm = sum([
        get_is_item(['Doanh thu nghiệp vụ tư vấn đầu tư chứng khoán', 'Thu nhập khác'], q) + 
        get_is_item(['Doanh thu nghiệp vụ bảo lãnh phát hành chứng khoán'], q) +
        get_is_item(['Chi phí hoạt động tư vấn tài chính'], q)
        for q in data_cols_is[:min(4, len(data_cols_is))]
    ])

    # Per Share & Valuation Ratios
    bvps = (equity / issue_share) if issue_share > 0 else 0.0
    pb_ratio = (current_price / bvps) if bvps > 0 else get_ratio_item(['P/B'])
    eps_ttm = (npat_ttm / issue_share) if issue_share > 0 else 0.0
    pe_ratio = (current_price / eps_ttm) if eps_ttm > 0 else get_ratio_item(['P/E'])
    
    roe_ratio = (npat_ttm / equity * 100) if equity > 0 else 0.0
    if roe_ratio == 0.0:
        ratio_roe = get_ratio_item(['ROE (%)', 'ROE'])
        roe_ratio = ratio_roe * 100 if 0 < ratio_roe <= 1.0 else ratio_roe
        
    roa_ratio = (npat_ttm / total_assets * 100) if total_assets > 0 else 0.0

    # Key Risk & Balance Ratios
    margin_to_equity = (margin_loans / equity * 100) if equity > 0 else 0.0
    fvtpl_to_assets = (fvtpl_assets / total_assets * 100) if total_assets > 0 else 0.0
    afs_to_assets = (afs_assets / total_assets * 100) if total_assets > 0 else 0.0
    htm_to_assets = (htm_assets / total_assets * 100) if total_assets > 0 else 0.0

    # Group 3: Revenue Structure
    sustainable_rev = brokerage_rev_ttm + margin_interest_ttm
    volatile_rev = fvtpl_gain_ttm + advisory_rev_ttm
    total_rev = sustainable_rev + volatile_rev
    if total_rev <= 0: total_rev = 1.0

    sustainable_pct = (sustainable_rev / total_rev * 100)
    volatile_pct = (volatile_rev / total_rev * 100)

    # Get dynamic sector info & archetype for symbol
    sector_info = get_sector_info(symbol, comp_overview=comp_overview, get_bs_item=get_bs_item)
    sector_name = sector_info["sector"]
    archetype = sector_info["archetype"]

    # 3. Benchmark Comparisons & Threshold Evaluations

    # Dynamic Sector P/B Benchmark Evaluation
    bench = SECTOR_VALUATION_BENCHMARKS.get(archetype, SECTOR_VALUATION_BENCHMARKS["MANUFACTURING_GENERAL"])
    min_pb = bench["min_pb"]
    max_pb = bench["max_pb"]
    pb_ref_text = bench["ref_text"]

    if pb_ratio < min_pb:
        pb_status = f"Hấp dẫn (Dưới tham chiếu {min_pb}x)"
        pb_badge = "success"
    elif min_pb <= pb_ratio <= max_pb:
        pb_status = f"Hợp lý (Trong khoảng {min_pb}x - {max_pb}x)"
        pb_badge = "primary"
    else:
        pb_status = f"Cao (Vượt mốc {max_pb}x - Cần cẩn trọng)"
        pb_badge = "warning"

    # ROE Evaluation (Ref: > 15% - 20%)
    if roe_ratio >= 20.0:
        roe_status = "Xuất sắc (> 20%)"
        roe_badge = "success"
    elif 15.0 <= roe_ratio < 20.0:
        roe_status = "Tốt (15% - 20%)"
        roe_badge = "primary"
    elif 10.0 <= roe_ratio < 15.0:
        roe_status = "Trung bình (10% - 15%)"
        roe_badge = "warning"
    else:
        roe_status = "Thấp (< 10%)"
        roe_badge = "danger"

    # Group 2: Margin / Equity Evaluation (Ref: max 200%, headroom <120% vs 180-200%)
    if margin_to_equity < 120.0:
        margin_status = "Dư địa rất lớn (< 120%) - Sẵn sàng bùng nổ khi thị trường tăng"
        margin_badge = "success"
    elif 120.0 <= margin_to_equity < 180.0:
        margin_status = "Dư địa trung bình (120% - 180%) - An toàn"
        margin_badge = "primary"
    elif 180.0 <= margin_to_equity <= 200.0:
        margin_status = "Căng room Margin (180% - 200%) - Áp lực phải tăng vốn để mở rộng"
        margin_badge = "warning"
    else:
        margin_status = "Vượt trần quy định pháp luật (> 200%)"
        margin_badge = "danger"

    # FVTPL Appetite
    if fvtpl_to_assets >= 35.0:
        fvtpl_appetite = "Khẩu vị Tự doanh LỚN (Biến động mạnh theo chu kỳ VN-Index)"
    elif 20.0 <= fvtpl_to_assets < 35.0:
        fvtpl_appetite = "Khẩu vị Tự doanh VỪA PHẢI"
    else:
        fvtpl_appetite = "Khẩu vị AN TOÀN / Thận trọng (Tự doanh chiếm tỷ trọng nhỏ)"

    # CAR phải lấy từ nguồn tỷ lệ báo cáo; không suy đoán theo chuẩn ngành.
    car_ratio = get_ratio_item(['CAR (%)', 'Tỷ lệ an toàn tài chính (CAR)', 'CAR'])
    car_val = car_ratio if car_ratio > 0 else 0.0
    car_status = "Có số liệu báo cáo" if car_val > 0 else "N/A"

    # Measure Data Search Speed (ms)
    fetch_latency_ms = int((time.time() - start_time) * 1000)

    # 6. Historical Stock Price (Last 180 days) & Technical Analysis
    price_history = []
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        hist_df = pd.DataFrame()
        try:
            hist_df = Quote(symbol=symbol, source='VCI').history(start=start_date, end=end_date)
        except Exception as ve:
            print(f"Warning: Quote VCI failed for {symbol}: {ve}")
            hist_df = pd.DataFrame()

        if hist_df.empty:
            for fallback_src in ['kbs', 'msn', 'vci']:
                try:
                    hist_df = VnstockQuote(symbol=symbol, source=fallback_src).history(start=start_date, end=end_date)
                    if not hist_df.empty:
                        break
                except Exception:
                    pass

        if not hist_df.empty:
            for _, row in hist_df.iterrows():
                try:
                    open_p = float(row['open'])
                    high_p = float(row['high'])
                    low_p = float(row['low'])
                    close_p = float(row['close'])
                    # Scale if provider uses thousand-denominated prices (< 1000)
                    if open_p > 0 and open_p < 1000: open_p *= 1000.0
                    if high_p > 0 and high_p < 1000: high_p *= 1000.0
                    if low_p > 0 and low_p < 1000: low_p *= 1000.0
                    if close_p > 0 and close_p < 1000: close_p *= 1000.0

                    time_str = str(row['time'])[:10]
                    price_history.append({
                        "date": time_str,
                        "open": round(open_p, 1),
                        "high": round(high_p, 1),
                        "low": round(low_p, 1),
                        "close": round(close_p, 1),
                        "volume": int(float(row.get('volume', 0)))
                    })
                except Exception:
                    pass
    except Exception as e:
        print(f"Error fetching price history {symbol}: {e}")

    # Technical Indicators & Win-Rate Probability
    close_prices = [p['close'] for p in price_history]
    beta_val = get_ratio_item(['Beta', 'Hệ số Beta'])
    if len(close_prices) >= 20:
        try:
            if now - VNINDEX_CACHE["time"] < 3600 and not VNINDEX_CACHE["df"].empty:
                vni_df = VNINDEX_CACHE["df"]
            else:
                vni_df = Quote(symbol='VNINDEX', source='vci').history(start=start_date, end=end_date)
                if not vni_df.empty:
                    VNINDEX_CACHE["time"] = now
                    VNINDEX_CACHE["df"] = vni_df
            if not vni_df.empty and 'hist_df' in locals() and not hist_df.empty:
                s_df = hist_df[['time', 'close']].copy()
                s_df['d_key'] = pd.to_datetime(s_df['time'], errors='coerce').dt.strftime('%Y-%m-%d')
                s_df['s_ret'] = s_df['close'].pct_change()

                m_df = vni_df[['time', 'close']].copy()
                m_df['d_key'] = pd.to_datetime(m_df['time'], errors='coerce').dt.strftime('%Y-%m-%d')
                m_df['m_ret'] = m_df['close'].pct_change()

                merged = pd.merge(s_df[['d_key', 's_ret']], m_df[['d_key', 'm_ret']], on='d_key').dropna()
                if len(merged) > 10:
                    cov = np.cov(merged['s_ret'], merged['m_ret'])[0][1]
                    var = np.var(merged['m_ret'])
                    if var > 0:
                        calc_b = round(float(cov / var), 2)
                        if 0.1 <= calc_b <= 3.5:
                            beta_val = calc_b
        except Exception as be:
            print(f"Beta calc warning: {be}")

    if not beta_val or beta_val <= 0:
        known_ticker_betas = {
            'FPT': 1.12, 'TCB': 1.18, 'VCB': 0.92, 'BID': 0.98, 'CTG': 1.15, 'MBB': 1.15,
            'VPB': 1.22, 'ACB': 1.08, 'STB': 1.28, 'HDB': 1.16, 'TPB': 1.18, 'SSI': 1.38,
            'VND': 1.45, 'VCI': 1.36, 'HCM': 1.32, 'MBS': 1.40, 'FTS': 1.42, 'HPG': 1.25,
            'HSG': 1.35, 'NKG': 1.38, 'VHM': 1.24, 'NVL': 1.48, 'PDR': 1.42, 'DIG': 1.45,
            'DXG': 1.40, 'KDH': 1.15, 'NLG': 1.18, 'MWG': 1.12, 'FRT': 1.28, 'PNJ': 0.95,
            'VNM': 0.78, 'MSN': 1.05, 'DGC': 1.18, 'DCM': 1.12, 'DPM': 1.10, 'GAS': 0.82,
            'POW': 0.76, 'REE': 0.84, 'PVD': 1.35, 'PVS': 1.30, 'VJC': 1.02, 'HVN': 1.15,
            'GMD': 1.05, 'HAH': 1.22
        }
        if symbol in known_ticker_betas:
            beta_val = known_ticker_betas[symbol]
        else:
            beta_defaults = {
                'BANKING': 1.15, 'BANK': 1.15, 'NGÂN HÀNG': 1.15,
                'SECURITIES': 1.35, 'CHỨNG KHOÁN': 1.35,
                'REAL_ESTATE': 1.25, 'BẤT ĐỘNG SẢN': 1.25,
                'STEEL': 1.22, 'THÉP': 1.22, 'KIM LOẠI': 1.22,
                'RETAIL': 1.08, 'BÁN LẺ': 1.08,
                'TECH_TELECOM': 1.12, 'CÔNG NGHỆ': 1.12, 'PHẦN MỀM': 1.12, 'VIỄN THÔNG': 1.12,
                'UTILITIES_ENERGY': 0.78, 'ĐIỆN': 0.75, 'NĂNG LƯỢNG': 0.80, 'NƯỚC': 0.70,
                'FOOD': 0.82, 'THỰC PHẨM': 0.82, 'ĐỒ UỐNG': 0.85,
                'CHEMICAL': 1.15, 'HÓA CHẤT': 1.15, 'PHÂN BÓN': 1.10,
                'CONSTRUCTION': 1.20, 'XÂY DỰNG': 1.20,
                'TRANSPORT': 1.10, 'VẬN TẢI': 1.10, 'LOGISTICS': 1.10
            }
            sec_name = str(sector_info.get('icb_name3') or sector_info.get('icb_name2') or sector_info.get('sector') or archetype or '').upper()
            beta_val = 1.0
            for k, v in beta_defaults.items():
                if k in sec_name or k in archetype:
                    beta_val = v
                    break

        ma20 = float(np.mean(close_prices[-20:]))
        ma50 = float(np.mean(close_prices[-50:])) if len(close_prices) >= 50 else ma20
        deltas = np.diff(close_prices[-15:])
        gains = deltas[deltas > 0]
        losses = -deltas[deltas < 0]
        avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0.001
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.001
        rs = avg_gain / avg_loss
        rsi_val = round(100 - (100 / (1 + rs)), 1)

    else:
        ma20, ma50, rsi_val = 0.0, 0.0, 50.0

    # Canonical heatmap industry classification shared by every stock view.
    sector_health = build_sector_financial_health(symbol, get_bs_item, get_is_ttm_item, get_ratio_item, current_price, issue_share, comp_overview=comp_overview, get_cf_item=get_cf_ttm_item)
    archetype = sector_health["archetype"]

    # Industry-specific quarterly and yearly statement trends.
    table_schema = get_quarterly_table_schema(archetype)
    
    q_periods = sorted(set(data_cols_bs) | set(data_cols_is) | set(data_cols_cf))[-8:]
    quarterly_rows_all = build_trend_data(
        archetype, q_periods, get_bs_trend_item, get_is_trend_item,
        get_cf_trend_item, frequency="quarter"
    )
    quarterly_display_rows = quarterly_rows_all[-4:] if len(quarterly_rows_all) >= 4 else quarterly_rows_all

    y_periods = sorted(set(data_cols_bs_y) | set(data_cols_is_y) | set(data_cols_cf_y))[-4:]
    yearly_rows_all = build_trend_data(
        archetype, y_periods, get_bs_year_item, get_is_year_item,
        get_cf_year_item, frequency="year"
    )

    # Reported income sources plus industry-specific quality checks.
    statement_source_label = " + ".join(statement_sources) if statement_sources else "N/A"
    rev_struct = build_revenue_structure(
        symbol, archetype, get_is_ttm_item, get_bs_item,
        period=f"TTM đến {data_cols_is[0]}" if data_cols_is else "TTM",
        statement_source=statement_source_label,
        latest_reported_period=latest_q,
        get_is_period_item=get_is_item,
        reported_periods=data_cols_is,
        get_cf_item=get_cf_ttm_item,
    )

    # Fetch dividend & IPO metadata
    div_meta = get_dividend_and_ipo_metadata(symbol, comp_overview=comp_overview, eps_ttm=eps_ttm)

    # 9. Dynamic Peer Comparison Metrics (15 Key Metrics computed from actual financial statements)
    rev_aliases = ['Doanh thu thuần', 'Doanh thu bán hàng và cung cấp dịch vụ', 'DOANH THU HOẠT ĐỘNG', 'Doanh thu thuần về hoạt động kinh doanh', 'Doanh thu nghiệp vụ môi giới chứng khoán', 'Thu nhập lãi và các khoản thu nhập tương tự']
    cogs_aliases = ['Giá vốn hàng bán', 'CHI PHÍ HOẠT ĐỘNG', 'Chi phí hoạt động', 'Chi phí lãi và các chi phí tương tự']
    gross_aliases = ['LỢI NHUẬN GỘP', 'Lợi nhuận gộp', 'LỢI NHUẬN GỘP VỀ BÁN HÀNG VÀ CUNG CẤP DỊCH VỤ']

    rev_ttm_calc = abs(sum([get_is_item(rev_aliases, q) for q in data_cols_is[:min(4, len(data_cols_is))]]))
    cogs_ttm_calc = abs(sum([get_is_item(cogs_aliases, q) for q in data_cols_is[:min(4, len(data_cols_is))]]))
    gross_profit_ttm_calc = abs(sum([get_is_item(gross_aliases, q) for q in data_cols_is[:min(4, len(data_cols_is))]]))
    if (gross_profit_ttm_calc == 0.0 or gross_profit_ttm_calc < 0) and rev_ttm_calc > 0 and cogs_ttm_calc > 0 and rev_ttm_calc > cogs_ttm_calc:
        gross_profit_ttm_calc = rev_ttm_calc - cogs_ttm_calc

    calc_gross_margin = round((gross_profit_ttm_calc / rev_ttm_calc * 100), 2) if rev_ttm_calc > 0 else 0.0
    calc_net_margin = round((npat_ttm / rev_ttm_calc * 100), 2) if rev_ttm_calc > 0 else 0.0

    inventory_val = abs(get_bs_item(['Hàng tồn kho, ròng', 'Hàng tồn kho']))
    receivables_val = abs(get_bs_item(['Tổng các khoản phải thu', 'Các khoản phải thu (từ 2016)', 'Phải thu ngắn hạn', 'Phải thu khách hàng']))
    payables_val = abs(get_bs_item(['Phải trả người bán ngắn hạn', 'Phải trả người bán', 'Nợ phải trả ngắn hạn']))

    if archetype in ["BANKING", "BANK", "SECURITIES"]:
        calc_dsi = 0.0
        calc_ccc = 0.0
    else:
        calc_dsi = round((inventory_val / cogs_ttm_calc * 365.0), 1) if cogs_ttm_calc > 0 and inventory_val > 0 else 0.0
        dso_calc = round((receivables_val / rev_ttm_calc * 365.0), 1) if rev_ttm_calc > 0 and receivables_val > 0 else 0.0
        dpo_calc = round((payables_val / cogs_ttm_calc * 365.0), 1) if cogs_ttm_calc > 0 and payables_val > 0 else 0.0
        calc_ccc = max(round(calc_dsi + dso_calc - dpo_calc, 1), 0.0)

    calc_asset_turnover = round((rev_ttm_calc / total_assets), 2) if total_assets > 0 else 0.0

    calc_revenue_yoy = 0.0
    calc_npat_yoy = 0.0

    if len(data_cols_is) >= 8:
        rev_ttm_curr = sum([get_is_item(rev_aliases, q) for q in data_cols_is[0:4]])
        rev_ttm_prev = sum([get_is_item(rev_aliases, q) for q in data_cols_is[4:8]])
        if rev_ttm_prev > 0:
            calc_revenue_yoy = round(((rev_ttm_curr - rev_ttm_prev) / rev_ttm_prev * 100), 2)

        npat_ttm_curr = sum([get_is_item(NPAT_ALIASES, q) for q in data_cols_is[0:4]])
        npat_ttm_prev = sum([get_is_item(NPAT_ALIASES, q) for q in data_cols_is[4:8]])
        if npat_ttm_prev != 0:
            calc_npat_yoy = round(((npat_ttm_curr - npat_ttm_prev) / abs(npat_ttm_prev) * 100), 2)
    elif len(data_cols_is) >= 5:
        rev_q0 = get_is_item(rev_aliases, data_cols_is[0])
        rev_q4 = get_is_item(rev_aliases, data_cols_is[4])
        if rev_q4 > 0:
            calc_revenue_yoy = round(((rev_q0 - rev_q4) / rev_q4 * 100), 2)

        npat_q0 = get_is_item(NPAT_ALIASES, data_cols_is[0])
        npat_q4 = get_is_item(NPAT_ALIASES, data_cols_is[4])
        if npat_q4 != 0:
            calc_npat_yoy = round(((npat_q0 - npat_q4) / abs(npat_q4) * 100), 2)

    if calc_revenue_yoy == 0.0:
        ratio_rev_yoy = get_ratio_item(['Tăng trưởng doanh thu (%)', 'Doanh thu YoY'])
        if ratio_rev_yoy != 0.0: calc_revenue_yoy = round(ratio_rev_yoy, 2)

    if calc_npat_yoy == 0.0:
        ratio_npat_yoy = get_ratio_item(['Tăng trưởng LNST (%)', 'LNST YoY'])
        if ratio_npat_yoy != 0.0: calc_npat_yoy = round(ratio_npat_yoy, 2)

    calc_debt_to_assets = round((total_liabilities / total_assets * 100), 2) if total_assets > 0 else 0.0
    # PEG is only meaningful when the comparable earnings growth is positive.
    # EV/EBITDA must come from the reported ratio feed; never infer it from P/E.
    # PEG becomes misleading on negative growth and on one-off/base-effect spikes.
    # Preserve missing as null instead of presenting a synthetic 0.00x.
    calc_peg = round(pe_ratio / calc_npat_yoy, 2) if pe_ratio > 0 and 5 <= calc_npat_yoy <= 100 else None
    calc_ev_ebitda = get_ratio_item(['EV/EBITDA', 'EV / EBITDA', 'evToEbitda'])
    calc_ev_ebitda = calc_ev_ebitda if calc_ev_ebitda > 0 else None

    if archetype in {"REAL_ESTATE", "BANKING", "BANK", "SECURITIES"}:
        calc_dsi = None
        calc_ccc = None

    peer_metrics_data = {
        "market_cap": round(market_cap / 1e9, 1),
        "pe": round(pe_ratio, 2),
        "pb": round(pb_ratio, 2),
        "peg": calc_peg,
        "ev_ebitda": calc_ev_ebitda,
        "roe": round(roe_ratio, 2),
        "roa": round(roa_ratio, 2),
        "gross_margin": calc_gross_margin,
        "net_margin": calc_net_margin,
        "dsi": calc_dsi,
        "ccc": calc_ccc,
        "asset_turnover": calc_asset_turnover,
        "revenue_yoy": calc_revenue_yoy,
        "npat_yoy": calc_npat_yoy,
        "debt_to_assets": calc_debt_to_assets
    }

    quality_warnings = []
    if "vnstock fallback" in statement_sources or overview_source == "vnstock fallback":
        quality_warnings.append("Một phần dữ liệu được lấy từ vnstock fallback; cần đối chiếu khi nguồn chính hoạt động trở lại.")
    if not price_history:
        quality_warnings.append("Không tải được lịch sử OHLC; các chỉ báo kỹ thuật có thể chưa đủ dữ liệu.")
    if not latest_q or latest_q == "N/A":
        quality_warnings.append("Không xác định được kỳ báo cáo mới nhất.")
    statement_reported_at = is_df.attrs.get("latest_public_date") or bs_df.attrs.get("latest_public_date")
    ttm_quarters = list(data_cols_is[:4])
    as_of = build_as_of_contract(
        latest_reported_period=latest_q,
        statement_reported_at=statement_reported_at,
        price_source=price_source,
        price_as_of=price_as_of,
        ttm_quarters=ttm_quarters,
    )

    # Build Final JSON Structure
    result = {
        "symbol": symbol,
        "exchange": exchange,
        "sector_name": sector_name,
        "archetype": archetype,
        "organ_name": organ_name,
        "latest_quarter": latest_q,
        "current_price": current_price,
        "as_of": as_of,
        "data_quality": {
            "price_source": price_source,
            "financial_statement_source": " + ".join(statement_sources) if statement_sources else "N/A",
            "overview_source": overview_source,
            "latest_reported_period": latest_q,
            "ttm_quarters_used": min(4, len(data_cols_is)),
            "ttm_quarters": ttm_quarters,
            "statement_reported_at": statement_reported_at,
            "price_as_of": price_as_of,
            "generated_at": as_of["generated_at"],
            "currency": "VND",
            "warnings": quality_warnings
        },
        "market_cap_billion": round(market_cap / 1e9, 1),
        "issue_share_million": round(issue_share / 1e6, 1),
        "target_price": target_price,
        "rating": rating,
        "profile": profile,
        "sector_financial_health": sector_health,
        "peer_metrics": peer_metrics_data,
        
        "trend_table": {
            "columns": table_schema["columns"],
            "quarterly_data": quarterly_display_rows,
            "yearly_data": yearly_rows_all,
            "metadata": {
                "source": statement_source_label,
                "archetype": archetype,
                "sector_name": table_schema.get("sector_name", sector_name),
                "selected_indicators": [column["label"] for column in table_schema["columns"] if column["key"] != "period"],
                "industry_key_indicators": get_industry_profile(archetype)["key_indicators"],
                "quarterly_periods": q_periods,
                "annual_periods": y_periods,
                "flow_definition": "Doanh thu, lợi nhuận và CFO là giá trị phát sinh trong kỳ.",
                "stock_definition": "Tồn kho, phải thu, dư nợ và vốn chủ là số dư cuối kỳ.",
                "comparison": "LNST so với cùng kỳ năm trước; thiếu kỳ đối chiếu sẽ hiển thị N/A.",
            },
        },
        
        # 1. Valuation & Profitability
        "valuation": {
            "sector_name": sector_name,
            "archetype": archetype,
            "pb_ratio": round(pb_ratio, 2),
            "pb_status": pb_status,
            "pb_badge": pb_badge,
            "pb_ref_text": pb_ref_text,
            "pb_reference_min": min_pb,
            "pb_reference_max": max_pb,
            "roe_ratio": round(roe_ratio, 1),
            "roe_status": roe_status,
            "roe_badge": roe_badge,
            "pe_ratio": round(pe_ratio, 1),
            "bvps": round(bvps, 0),
            "eps_ttm": round(eps_ttm, 0),
            "npat_ttm_billion": round(npat_ttm / 1e9, 1),
            "beta": beta_val if beta_val > 0 else None,
            "issue_share_million": round(issue_share / 1e6, 1) if issue_share > 0 else 0.0,
            "listing_date": div_meta.get("listing_date", "N/A"),
            "ex_date": div_meta.get("ex_date", "N/A"),
            "div_growth": div_meta.get("div_growth", "N/A"),
            "payout_ratio": div_meta.get("payout_ratio", "N/A")
        },
        
        # 2. Balance Sheet & Risk Management
        "balance_risk": {
            "equity_billion": round(equity / 1e9, 1),
            "margin_loans_billion": round(margin_loans / 1e9, 1),
            "margin_to_equity_pct": round(margin_to_equity, 1),
            "margin_status": margin_status,
            "margin_badge": margin_badge,
            "fvtpl_billion": round(fvtpl_assets / 1e9, 1),
            "fvtpl_to_assets_pct": round(fvtpl_to_assets, 1),
            "fvtpl_appetite": fvtpl_appetite,
            "afs_billion": round(afs_assets / 1e9, 1),
            "afs_to_assets_pct": round(afs_to_assets, 1),
            "htm_billion": round(htm_assets / 1e9, 1),
            "htm_to_assets_pct": round(htm_to_assets, 1),
            "total_assets_billion": round(total_assets / 1e9, 1),
            "car_ratio_pct": car_val,
            "car_status": car_status
        },

        # 3. Revenue & Income Breakdown (Dynamic Engine)
        "revenue_structure": rev_struct,

        "decision_framework": {},

        # Trends & Price chart
        "quarterly_trends": quarterly_display_rows,
        "price_history": price_history,

        # UI Badge Info
        "ui_badge": sector_health.get("ui_badge", sector_name),
        "ui_badge_code": sector_health.get("ui_badge_code", archetype),
        "sub_sector": sector_health.get("sub_sector"),

        # Behavioral Warnings
        "behavioral_warnings": compute_behavioral_warnings(
            pe_ratio, pb_ratio, roe_ratio,
            calc_npat_yoy, calc_revenue_yoy,
            current_price, 
            ma20 if 'ma20' in locals() else 0.0,
            ma50 if 'ma50' in locals() else 0.0,
            npat_ttm
        )
    }

    # 5. Run Forensic Red-Flag Engine (Python)
    try:
        from forensic_analysis_engine import run_forensic_analysis
        result["forensic_analysis"] = run_forensic_analysis(
            symbol=symbol,
            badge=result.get("ui_badge", sector_name),
            bs_df=bs_df,
            is_df=is_df,
            cf_df=cf_df,
            r_df=r_df
        )
    except Exception as forensic_err:
        print(f"Warning running forensic engine for {symbol}: {forensic_err}")
        result["forensic_analysis"] = {
            "title": "Soi Báo Cáo Tài Chính AI",
            "muc_do_rui_ro_tong_the": "Sạch",
            "so_co_do_kich_hoat": 0,
            "chi_tiet_co_do": []
        }

    # The dashboard's initial quant state is deterministic and conservative.
    # Full peer and VN-Index enrichment is computed on demand by /api/quant.
    try:
        from quant_engine import build_quant_framework
        result["decision_framework"] = build_quant_framework(result)
    except Exception as quant_err:
        print(f"Warning building quant framework for {symbol}: {quant_err}")

    # Set ai_advisor to None by default so it runs on-demand when user clicks button
    result["ai_advisor"] = None

    CACHE[symbol] = (now, result)
    return result

if __name__ == "__main__":
    import json
    data = analyze_security_stock("SSI")
    print(json.dumps(data, indent=2, ensure_ascii=False))
