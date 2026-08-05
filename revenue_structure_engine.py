"""Revenue structure with explicit disclosure provenance and honest fallbacks.
   Enhanced with historical trends, growth metrics, and sector-specific insights."""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple
from data_freshness import periods_compatible
from industry_indicator_profiles import get_industry_profile


COLORS = ["#10b981", "#38bdf8", "#f59e0b", "#a855f7", "#94a3b8", "#ef4444", "#ec4899", "#8b5cf6"]
SEGMENT_COLORS = {
    "core": "#10b981",
    "financial": "#38bdf8",
    "other": "#94a3b8",
}

# Issuer disclosures are deliberately curated. A flat financial statement cannot
# prove product mix; every entry here must point to an issuer-originated source.
COMPANY_DISCLOSURES = {
    "PNJ": {
        "period": "FY2025",
        "period_end": "2025-12-31",
        "title": "Cơ cấu doanh thu theo kênh/nhóm sản phẩm được PNJ công bố",
        "total_revenue_billion": 34976.0,
        "segments": [
            {"name": "Trang sức bán lẻ", "percentage": 69.6},
            {"name": "Trang sức bán sỉ", "percentage": 11.0},
            {"name": "Vàng 24K & hoạt động khác", "percentage": 19.4},
        ],
        "source": {
            "publisher": "Công ty Cổ phần Vàng bạc Đá quý Phú Nhuận (PNJ)",
            "document": "PNJ đạt doanh thu gần 35.000 tỷ trong năm 2025",
            "url": "https://www.pnj.com.vn/blog/pnj-dat-doanh-thu-gan-35-000-ty-trong-nam-2025/",
            "published_date": "2026-01-21",
            "evidence": "PNJ công bố trang sức chiếm 80,6% doanh thu, gồm bán lẻ 69,6% và bán sỉ 11,0%.",
        },
        "limitations": [
            "PNJ chưa công bố doanh thu tách riêng kim cương, vàng 18K/14K trong nguồn này.",
            "Nhóm 19,4% là phần còn lại được suy ra, không phải tỷ trọng TTM 2026.",
        ],
        "dimensions": [
            {"name": "Kênh bán", "values": ["Bán lẻ", "Bán sỉ"]},
            {"name": "Nhóm sản phẩm", "values": ["Trang sức", "Vàng 24K", "Khác"]},
            {"name": "Chất liệu/độ tinh khiết", "status": "not_disclosed"},
        ],
    }
}


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _ratio(numerator: Any, denominator: Any, multiplier: float = 100.0) -> Optional[float]:
    num, den = _number(numerator), _number(denominator)
    if num is None or den in (None, 0):
        return None
    return round(num / den * multiplier, 1)


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Tính phần trăm thay đổi giữa 2 kỳ."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _assessment_metric(key: str, label: str, value: Optional[float], unit: str, meaning: str) -> dict:
    return {"key": key, "label": label, "value": value, "unit": unit, "meaning": meaning, "status": "available" if value is not None else "unavailable"}


def _growth_indicator(current: Optional[float], previous: Optional[float]) -> dict:
    """Trả về dict với giá trị, % thay đổi, và trend."""
    change = _pct_change(current, previous)
    trend = "up" if change and change > 0 else "down" if change and change < 0 else "flat"
    return {
        "current": current,
        "previous": previous,
        "change_pct": change,
        "trend": trend,
        "formatted_current": f"{current:,.0f}" if current is not None else "N/A",
        "formatted_change": f"{'+' if change and change > 0 else ''}{change}%" if change is not None else "N/A",
    }


def _segment_bucket(name: str) -> str:
    text = str(name).lower()
    if "khác" in text:
        return "other"
    if any(token in text for token in ("tài chính", "chứng khoán", "fvtpl", "ngoại hối", "liên doanh", "liên kết")):
        return "non_core"
    return "core"


def _get_sector_revenue_breakdown(archetype: str) -> Optional[Dict[str, Any]]:
    """Trả về cấu trúc nguồn thu điển hình theo ngành. Trả về None nếu không có profile đặc thù."""
    sector_breakdowns = {
        # TÀI CHÍNH
        "BANKING": {
            "name": "Ngân hàng",
            "typical_sources": [
                {"name": "Thu nhập lãi thuần (NII)", "weight": 0.70, "description": "Chênh lệch lãi vay"},
                {"name": "Thu nhập phí & dịch vụ", "weight": 0.15, "description": "Phí ATM, thẻ, thanh toán"},
                {"name": "Thu nhập từ Trading", "weight": 0.08, "description": "FVTPL, ngoại hối"},
                {"name": "Thu nhập khác", "weight": 0.07, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["NIM (%)", "CASA Ratio", "Tỷ lệ NPL", "ROA", "ROE"],
            "red_flags": ["NIM giảm mạnh", "NPL tăng cao bất thường"],
        },
        "SECURITIES": {
            "name": "Chứng khoán",
            "typical_sources": [
                {"name": "Môi giới & Margin", "weight": 0.40, "description": "Phí môi giới + Lãi margin"},
                {"name": "Tự doanh (FVTPL)", "weight": 0.30, "description": "Lãi/lỗ từ danh mục đầu tư"},
                {"name": "Tư vấn & Bảo lãnh", "weight": 0.15, "description": "IPO, M&A advisory"},
                {"name": "Quản lý quỹ", "weight": 0.10, "description": "Phí quản lý quỹ"},
                {"name": "Khác", "weight": 0.05, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Vòng quay tài sản", "Biên lợi nhuận", "Revenue Mix", "AUM"],
            "red_flags": ["FVTPL chiếm >50%", "Doanh thu phụ thuộc thị trường"],
        },
        "FINANCIAL_SERVICES": {
            "name": "Dịch vụ tài chính",
            "typical_sources": [
                {"name": "Thu nhập lãi thuần", "weight": 0.55, "description": "Cho vay tiêu dùng, tín dụng"},
                {"name": "Thu nhập phí dịch vụ", "weight": 0.25, "description": "Phí bảo hiểm, thanh toán"},
                {"name": "Thu hồi nợ/xử lý tài sản", "weight": 0.12, "description": "Thu hồi nợ đã xử lý"},
                {"name": "Khác", "weight": 0.08, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["NIM", "Tỷ lệ NPL", "Coverage Ratio", "CIR"],
            "red_flags": ["NIM cao bất thường", "NPL vượt ngưỡng"],
        },
        "INSURANCE": {
            "name": "Bảo hiểm",
            "typical_sources": [
                {"name": "Phí bảo hiểm gốc", "weight": 0.75, "description": "Phí bảo hiểm nhận được"},
                {"name": "Thu nhập đầu tư", "weight": 0.15, "description": "Lãi từ quỹ dự phòng"},
                {"name": "Hoàn phí & bồi thường", "weight": -0.10, "description": "Chi phí hoàn phí"},
            ],
            "key_metrics": ["Combined Ratio", "Loss Ratio", "Expense Ratio", "Inforce"],
            "red_flags": ["Combined Ratio >100%", "Loss Ratio tăng mạnh"],
        },
        # BẤT ĐỘNG SẢN & HẠ TẦNG
        "REAL_ESTATE": {
            "name": "Bất động sản",
            "typical_sources": [
                {"name": "Bán căn hộ/dự án", "weight": 0.65, "description": "Doanh thu từ bán sản phẩm BĐS"},
                {"name": "Cho thuê BĐS", "weight": 0.15, "description": "Thu nhập từ cho thuê"},
                {"name": "Dịch vụ quản lý", "weight": 0.08, "description": "Phí quản lý và dịch vụ"},
                {"name": "Chuyển nhượng dự án", "weight": 0.07, "description": "Thu nhập bất thường"},
                {"name": "Khác", "weight": 0.05, "description": "Các nguồn thu khác"},
            ],
            "key_metrics": ["Chuỗi Doanh Thu", "Biên Lợi Nhuận", "Tỷ Lệ Chuyển Nhượng", "Pre-sales"],
            "red_flags": ["Phụ thuộc chuyển nhượng dự án", "Tồn kho BĐS cao bất thường"],
        },
        "INDUSTRIAL_PARK": {
            "name": "KCN - Hạ tầng",
            "typical_sources": [
                {"name": "Cho thuê đất KCN", "weight": 0.55, "description": "Tiền thuê đất và cơ sở hạ tầng"},
                {"name": "Chuyển nhượng quyền sử dụng", "weight": 0.25, "description": "Bán đất KCN"},
                {"name": "Dịch vụ tiện ích", "weight": 0.12, "description": "Điện, nước, xử lý nước thải"},
                {"name": "Khác", "weight": 0.08, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Giá thuê đất", "Tỷ lệ lấp đầy", "Diện tích quy hoạch", "Pre-sales"],
            "red_flags": ["Tỷ lệ lấp đầy thấp", "Phụ thuộc chuyển nhượng"],
        },
        # SẢN XUẤT & CÔNG NGHIỆP
        "INDUSTRIAL": {
            "name": "Công nghiệp",
            "typical_sources": [
                {"name": "Sản xuất chính", "weight": 0.85, "description": "Doanh thu sản xuất"},
                {"name": "Dịch vụ kỹ thuật", "weight": 0.08, "description": "Lắp đặt, bảo hành"},
                {"name": "Xuất khẩu", "weight": 0.05, "description": "Doanh thu xuất khẩu"},
                {"name": "Khác", "weight": 0.02, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Capacity Utilization", "Export ratio", "ASP Trend", "COGS"],
            "red_flags": ["Export ratio >80%", "Capacity utilization thấp"],
        },
        "PETROCHEMICAL": {
            "name": "Dầu khí - Hóa dầu",
            "typical_sources": [
                {"name": "Bán sản phẩm dầu khí", "weight": 0.60, "description": "Xăng dầu, khí đốt"},
                {"name": "Hóa dầu", "weight": 0.25, "description": "PE, PP, PVC"},
                {"name": "Dịch vụ khoan/cầu cảng", "weight": 0.10, "description": "Dịch vụ hỗ trợ dầu khí"},
                {"name": "Khác", "weight": 0.05, "description": "Các nguồn khác"},
            ],
            "key_metrics": [" crack spread", "Capacity Utilization", "Đơn giá bán", "Định giá DDM"],
            "red_flags": ["Crack spread âm", "Phụ thuộc giá dầu thế giới"],
        },
        "POWER": {
            "name": "Điện - Năng lượng",
            "typical_sources": [
                {"name": "Bán điện", "weight": 0.80, "description": "Doanh thu bán điện"},
                {"name": "Xây dựng BOT", "weight": 0.12, "description": "Doanh thu xây dựng BOT"},
                {"name": "Khác", "weight": 0.08, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["PPA price", "Capacity Factor", "Điện thương phẩm", "O&M Cost"],
            "red_flags": ["PPA hết hạn", "Capacity Factor thấp"],
        },
        "CEMENT": {
            "name": "Xi măng - Vật liệu",
            "typical_sources": [
                {"name": "Bán xi măng", "weight": 0.70, "description": "Doanh thu bán xi măng"},
                {"name": "Bán clinker", "weight": 0.15, "description": "Bán clinker cho nhà máy khác"},
                {"name": "Bê tông & vật liệu", "weight": 0.10, "description": "Bê tông tươi, vật liệu xây dựng"},
                {"name": "Khác", "weight": 0.05, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Biên lợi nhuận", "Tấn xi măng/chi phí năng lượng", "Volume"],
            "red_flags": ["Biên lợi nhuận giảm", "Cạnh tranh giá"],
        },
        # THƯƠNG MẠI & DỊCH VỤ
        "RETAIL": {
            "name": "Bán lẻ - Tiêu dùng",
            "typical_sources": [
                {"name": "Bán hàng", "weight": 0.90, "description": "Doanh thu bán hàng"},
                {"name": "Dịch vụ", "weight": 0.05, "description": "Phí dịch vụ"},
                {"name": "Khác", "weight": 0.05, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Số cửa hàng", "Revenue/cửa hàng", "Same-Store Growth", "Inventory Turnover"],
            "red_flags": ["Tăng trưởng SSS âm", "Inventory days cao"],
        },
        "FMCG": {
            "name": "Hàng tiêu dùng nhanh",
            "typical_sources": [
                {"name": "Bán sản phẩm", "weight": 0.92, "description": "Doanh thu bán hàng"},
                {"name": "Dịch vụ", "weight": 0.05, "description": "Phí dịch vụ"},
                {"name": "Khác", "weight": 0.03, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Market Share", "Distribution Coverage", "ASP", "Gross Margin"],
            "red_flags": ["Market share giảm", "Promotion cao"],
        },
        "AVIATION": {
            "name": "Hàng không - Logistics",
            "typical_sources": [
                {"name": "Vận chuyển hành khách", "weight": 0.65, "description": "Vé máy bay"},
                {"name": "Vận chuyển hàng hóa", "weight": 0.15, "description": "Vận chuyển hàng hóa"},
                {"name": "Dịch vụ mặt đất", "weight": 0.10, "description": "Ground services"},
                {"name": "Khác", "weight": 0.10, "description": "Hàng hóa, dịch vụ phụ"},
            ],
            "key_metrics": ["Load Factor", "Yield", "CASK", "RASK"],
            "red_flags": ["Load Factor <80%", "Fuel cost tăng"],
        },
        # NÔNG NGHIỆP & THỦY SẢN
        "AGRICULTURE": {
            "name": "Nông nghiệp",
            "typical_sources": [
                {"name": "Bán nông sản", "weight": 0.75, "description": "Cà phê, cao su, gạo"},
                {"name": "Chế biến", "weight": 0.15, "description": "Chế biến nông sản"},
                {"name": "Khác", "weight": 0.10, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Volume", "Export Price", "Yield", "Cost per hectare"],
            "red_flags": ["Giá thế giới giảm", "Thời tiết bất lợi"],
        },
        "SEAFOOD": {
            "name": "Thủy sản",
            "typical_sources": [
                {"name": "Xuất khẩu thủy sản", "weight": 0.80, "description": "Tôm, cá tra, cá ngừ"},
                {"name": "Nội địa", "weight": 0.12, "description": "Bán nội địa"},
                {"name": "Khác", "weight": 0.08, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Export Volume", "ASP Export", "Feed Cost", "Survival Rate"],
            "red_flags": ["Thuế chống trợ cấp", "Feed cost tăng"],
        },
        # CÔNG NGHỆ
        "TECHNOLOGY": {
            "name": "Công nghệ",
            "typical_sources": [
                {"name": "Phần mềm/SaaS", "weight": 0.60, "description": "Doanh thu từ phần mềm"},
                {"name": "Phần cứng", "weight": 0.25, "description": "Thiết bị, hạ tầng"},
                {"name": "Dịch vụ IT", "weight": 0.10, "description": "Tư vấn, triển khai"},
                {"name": "Khác", "weight": 0.05, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["ARR Growth", "Gross Margin", "Net Revenue Retention", "CAC/LTV"],
            "red_flags": ["Revenue concentration cao", "Churn rate cao"],
        },
        # Y TẾ & DƯỢC
        "HEALTHCARE": {
            "name": "Y tế - Dược phẩm",
            "typical_sources": [
                {"name": "Bán dược phẩm", "weight": 0.65, "description": "Thuốc generic, thực phẩm chức năng"},
                {"name": "Dịch vụ y tế", "weight": 0.25, "description": "Khám chữa bệnh, phẫu thuật"},
                {"name": "Thiết bị y tế", "weight": 0.07, "description": "Bán và cho thuê thiết bị"},
                {"name": "Khác", "weight": 0.03, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Bed Occupancy Rate", "ARPP", "Gross Margin", "Drug Pipeline"],
            "red_flags": ["Thuốc mất patent", "Giá thuốc bị kiểm soát"],
        },
        # VẬN TẢI & HẠ TẦNG
        "TRANSPORT": {
            "name": "Vận tải - Logistics",
            "typical_sources": [
                {"name": "Vận tải đường bộ", "weight": 0.50, "description": "Container, hàng hóa đường bộ"},
                {"name": "Cảng biển", "weight": 0.25, "description": "Bốc xếp cảng"},
                {"name": "Kho bãi", "weight": 0.15, "description": "Cho thuê kho bãi"},
                {"name": "Khác", "weight": 0.10, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Volume (TEUs/tonnes)", "Revenue/tonne", "Fleet utilization"],
            "red_flags": ["Cước vận tải giảm", "Competition giá"],
        },
        "CONSTRUCTION": {
            "name": "Xây dựng",
            "typical_sources": [
                {"name": "Xây lắp công trình", "weight": 0.75, "description": "Xây dựng hạ tầng, dân dụng"},
                {"name": "Kinh doanh BĐS", "weight": 0.15, "description": "Bán, cho thuê BĐS"},
                {"name": "Khác", "weight": 0.10, "description": "Các nguồn khác"},
            ],
            "key_metrics": ["Backlog", "Win Rate", "Gross Margin", "Progress Revenue"],
            "red_flags": ["Backlog giảm", "Working capital cao"],
        },
    }

    # Trả về None nếu không có profile đặc thù - UI sẽ ẩn panel
    return sector_breakdowns.get(archetype)


def _ensure_chronological_periods(periods: list[str]) -> list[str]:
    """Ensure period list is in ascending chronological order (oldest -> newest)."""
    clean = [str(p).strip() for p in periods if p]
    if len(clean) >= 2:
        if clean[0] > clean[-1]:
            clean.reverse()
    return clean


def _build_historical_trends(
    symbol: str,
    archetype: str,
    get_is_period_item: Callable,
    reported_periods: list[str],
    get_bs_period_item: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Build historical revenue trend data for visualization."""
    periods = _ensure_chronological_periods(reported_periods)

    if not periods or len(periods) < 2:
        return {
            "status": "insufficient_data",
            "message": "Cần ít nhất 2 kỳ báo cáo để vẽ biểu đồ xu hướng.",
            "data_points": [],
        }

    revenue_aliases = [
        "Tổng thu nhập hoạt động",
        "Thu nhập lãi và các khoản thu nhập tương tự",
        "Doanh thu thuần",
        "Doanh thu bán hàng và cung cấp dịch vụ",
        "Doanh thu thuần về hoạt động kinh doanh",
        "DOANH THU HOẠT ĐỘNG",
        "Tổng doanh thu",
    ]
    gross_profit_aliases = [
        "LỢI NHUẬN GỘP",
        "Lợi nhuận gộp",
        "LỢI NHUẬN GỘP VỀ BÁN HÀNG VÀ CUNG CẤP DỊCH VỤ",
    ]
    npat_aliases = [
        "Lợi nhuận sau thuế",
        "Lợi nhuận của Cổ đông của Công ty mẹ",
        "Lãi/(lỗ) thuần sau thuế",
        "LỢI NHUẬN KẾ TOÁN SAU THUẾ",
        "LNST",
    ]

    data_points = []
    for period in periods:
        revenue = _number(get_is_period_item(revenue_aliases, period))
        if revenue is None or revenue == 0:
            continue
        revenue = abs(revenue)
        gross_profit = abs(_number(get_is_period_item(gross_profit_aliases, period)) or 0)
        npat = abs(_number(get_is_period_item(npat_aliases, period)) or 0)

        data_points.append({
            "period": period,
            "revenue_billion": round(revenue / 1e9, 1),
            "gross_profit_billion": round(gross_profit / 1e9, 1) if gross_profit else None,
            "npat_billion": round(npat / 1e9, 1) if npat else None,
        })

    if len(data_points) < 2:
        return {
            "status": "insufficient_data",
            "message": "Không đủ dữ liệu từ BCTC để vẽ xu hướng.",
            "data_points": [],
        }

    # Calculate growth metrics
    total_revenue_current = data_points[-1]["revenue_billion"]
    current_period = str(data_points[-1]["period"])

    yoy_growth = None
    previous_revenue = None
    yoy_target_period = None

    import re
    q_match = re.match(r"^(\d{4})-Q([1-4])$", current_period)
    if q_match:
        curr_year = int(q_match.group(1))
        curr_q = int(q_match.group(2))
        yoy_target_period = f"{curr_year - 1}-Q{curr_q}"

        # 1. Search in data_points first for same quarter last year
        for dp in data_points:
            if str(dp.get("period")) == yoy_target_period and dp.get("revenue_billion") is not None:
                previous_revenue = dp["revenue_billion"]
                break

        # 2. If not found in data_points, attempt to query get_is_period_item directly
        if previous_revenue is None and get_is_period_item and callable(get_is_period_item):
            prev_val = _number(get_is_period_item(revenue_aliases, yoy_target_period))
            if prev_val is not None and prev_val > 0:
                previous_revenue = round(abs(prev_val) / 1e9, 1)

    elif re.match(r"^\d{4}$", current_period):
        curr_year = int(current_period)
        yoy_target_period = str(curr_year - 1)
        for dp in data_points:
            if str(dp.get("period")) == yoy_target_period and dp.get("revenue_billion") is not None:
                previous_revenue = dp["revenue_billion"]
                break
        if previous_revenue is None and get_is_period_item and callable(get_is_period_item):
            prev_val = _number(get_is_period_item(revenue_aliases, yoy_target_period))
            if prev_val is not None and prev_val > 0:
                previous_revenue = round(abs(prev_val) / 1e9, 1)

    # Fallback to sequential previous quarter/period if exact YoY period unavailable
    if previous_revenue is None and len(data_points) >= 2:
        previous_revenue = data_points[-2]["revenue_billion"]
        yoy_target_period = data_points[-2]["period"]

    if previous_revenue is not None and previous_revenue > 0:
        yoy_growth = _pct_change(total_revenue_current, previous_revenue)

    # Calculate CAGR for 3 years if available
    cagr = None
    if len(data_points) >= 4:
        first_rev = data_points[0]["revenue_billion"]
        years = len(data_points) - 1
        if first_rev > 0:
            cagr = round((pow(total_revenue_current / first_rev, 1 / years) - 1) * 100, 1)

    # Calculate average margins
    margins_with_data = [p for p in data_points if p.get("gross_profit_billion") and p.get("revenue_billion")]
    avg_gross_margin = None
    if margins_with_data:
        avg_gross_margin = round(sum(
            p["gross_profit_billion"] / p["revenue_billion"] * 100
            for p in margins_with_data
        ) / len(margins_with_data), 1)

    # Get sector context
    sector_info = _get_sector_revenue_breakdown(archetype)

    return {
        "status": "available",
        "data_points": data_points,
        "summary": {
            "current_revenue": total_revenue_current,
            "previous_revenue": previous_revenue,
            "yoy_target_period": yoy_target_period,
            "yoy_growth_pct": yoy_growth,
            "cagr_pct": cagr,
            "avg_gross_margin_pct": avg_gross_margin,
            "periods_count": len(data_points),
            "trend_direction": "up" if yoy_growth and yoy_growth > 0 else "down" if yoy_growth and yoy_growth < 0 else "flat",
        },
        "sector_context": sector_info,
        "chart_config": {
            "type": "area_line_combo",
            "primary_series": "revenue_billion",
            "secondary_series": ["gross_profit_billion", "npat_billion"],
            "y_axis_label": "Tỷ VND",
            "x_axis_label": "Kỳ báo cáo",
            "colors": {
                "revenue": "#10b981",
                "gross_profit": "#38bdf8",
                "npat": "#f59e0b",
            },
        },
    }


def _build_segment_trend_analysis(
    symbol: str,
    archetype: str,
    get_is_period_item: Callable,
    reported_periods: list[str],
) -> Dict[str, Any]:
    """Build segment-level trend analysis for multi-segment companies."""
    segments_map = {
        "SECURITIES": [
            ("Doanh thu môi giới", ["Doanh thu nghiệp vụ môi giới chứng khoán", "Doanh thu môi giới"]),
            ("Thu nhập lãi margin", ["Lãi từ các khoản cho vay và phải thu", "Lãi từ cho vay margin"]),
            ("Thu nhập FVTPL", ["Lãi từ các tài sản tài chính FVTPL", "Lãi từ FVTPL"]),
        ],
        "BANKING": [
            ("Thu nhập lãi thuần", ["Thu nhập lãi thuần"]),
            ("Thu nhập phí", ["Thu nhập từ hoạt động dịch vụ", "Lãi thuần từ hoạt động dịch vụ"]),
            ("Thu nhập Trading", ["Lãi thuần từ kinh doanh ngoại hối", "Lãi thuần từ mua bán chứng khoán"]),
        ],
        "REAL_ESTATE": [
            ("Doanh thu bán BĐS", ["Doanh thu thuần", "Doanh thu bán hàng"]),
            ("Thu nhập cho thuê", ["Thu nhập từ cho thuê", "Doanh thu cho thuê"]),
            ("Thu nhập tài chính", ["Doanh thu hoạt động tài chính"]),
        ],
    }

    segment_aliases = segments_map.get(archetype, [])
    if not segment_aliases:
        return {"status": "not_applicable", "message": f"Ngành {archetype} không có phân tích chi tiết theo phân khúc."}

    periods = _ensure_chronological_periods(reported_periods)
    trend_data = {}

    for segment_name, aliases in segment_aliases:
        segment_values = []
        for period in periods:
            value = _number(get_is_period_item(aliases, period))
            if value is not None:
                segment_values.append({
                    "period": period,
                    "value_billion": round(abs(value) / 1e9, 1),
                })
        if segment_values:
            trend_data[segment_name] = segment_values

    if not trend_data:
        return {"status": "no_data", "message": "Không tìm thấy dữ liệu phân khúc.", "segments": {}}

    # Calculate segment mix for latest period
    latest_period = periods[-1] if periods else None
    latest_segment_values = {}

    for segment_name, aliases in segment_aliases:
        value = _number(get_is_period_item(aliases, latest_period))
        if value is not None:
            latest_segment_values[segment_name] = round(abs(value) / 1e9, 1)

    total_latest = sum(latest_segment_values.values()) if latest_segment_values else 0
    segment_mix = {}
    if total_latest > 0:
        for name, val in latest_segment_values.items():
            segment_mix[name] = round(val / total_latest * 100, 1)

    return {
        "status": "available",
        "segments": trend_data,
        "latest_period": latest_period,
        "segment_mix": segment_mix,
        "chart_config": {
            "type": "stacked_bar",
            "colors": {
                "Doanh thu môi giới": "#10b981",
                "Thu nhập lãi margin": "#38bdf8",
                "Thu nhập FVTPL": "#f59e0b",
                "Thu nhập lãi thuần": "#10b981",
                "Thu nhập phí": "#38bdf8",
                "Thu nhập Trading": "#f59e0b",
                "Doanh thu bán BĐS": "#10b981",
                "Thu nhập cho thuê": "#38bdf8",
                "Thu nhập tài chính": "#94a3b8",
            },
        },
    }


def _profile_payload(archetype: str) -> dict:
    profile = get_industry_profile(archetype)
    return {
        "archetype": profile["archetype"],
        "sector_name": profile["name"],
        "expected_revenue_sources": profile["expected_revenue_sources"],
        "key_indicators": profile["key_indicators"],
        "cautions": profile["cautions"],
        "disclosure_status": "industry_context_not_reported_values",
    }


def _build_quality_assessment(
    archetype: str,
    segments: list[dict],
    get_is_ttm_item: Callable,
    get_bs_item: Callable,
    get_cf_ttm_item: Optional[Callable],
) -> dict:
    profile = get_industry_profile(archetype)
    positive_total = sum(max(0.0, float(item.get("amount_billion") or 0)) for item in segments)
    bucket_amounts = {"core": 0.0, "non_core": 0.0, "other": 0.0}
    for item in segments:
        bucket_amounts[_segment_bucket(item.get("name", ""))] += max(0.0, float(item.get("amount_billion") or 0))
    core_share = _ratio(bucket_amounts["core"], positive_total)
    finance_share = _ratio(bucket_amounts["non_core"], positive_total)
    other_share = _ratio(bucket_amounts["other"], positive_total)

    revenue = abs(_number(get_is_ttm_item(["Tổng thu nhập hoạt động", "Thu nhập lãi và các khoản thu nhập tương tự", "Doanh thu thuần", "Doanh thu bán hàng và cung cấp dịch vụ", "Doanh thu thuần về hoạt động kinh doanh", "DOANH THU HOẠT ĐỘNG"])) or 0)
    gross_profit = _number(get_is_ttm_item(["LỢI NHUẬN GỘP", "Lợi nhuận gộp", "LỢI NHUẬN GỘP VỀ BÁN HÀNG VÀ CUNG CẤP DỊCH VỤ", "Lợi nhuận thuần hoạt động trước khi trích lập dự phòng rủi ro tín dụng"]))
    npat = _number(get_is_ttm_item(["Lợi nhuận sau thuế", "Lợi nhuận của Cổ đông của Công ty mẹ", "Cổ đông của Công ty mẹ", "Lãi/(lỗ) thuần sau thuế", "LỢI NHUẬN KẾ TOÁN SAU THUẾ", "LNST"]))
    finance_cost = abs(_number(get_is_ttm_item(["Chi phí tài chính", "Chi phí lãi vay", "Trong đó: Chi phí lãi vay"])) or 0)
    inventory = abs(_number(get_bs_item(["Hàng tồn kho, ròng", "Hàng tồn kho", "Bất động sản dở dang"])) or 0)
    receivables = abs(_number(get_bs_item(["Tổng các khoản phải thu", "Các khoản phải thu (từ 2016)", "Phải thu ngắn hạn", "Phải thu khách hàng"])) or 0)
    prepayments = abs(_number(get_bs_item(["Người mua trả tiền trước", "Người mua trả tiền trước ngắn hạn", "Người mua trả tiền trước dài hạn"])) or 0)
    cfo = _number(get_cf_ttm_item(["Dòng tiền thuần từ hoạt động kinh doanh", "Lưu chuyển tiền thuần từ hoạt động kinh doanh"])) if get_cf_ttm_item else None

    metrics = [
        _assessment_metric("core_income_share", "Nguồn thu cốt lõi / tổng nguồn thu", core_share, "%", "Tỷ trọng nguồn thu đến từ hoạt động chính."),
        _assessment_metric("financial_income_share", "Nguồn thu tài chính / tổng nguồn thu", finance_share, "%", "Mức phụ thuộc vào nguồn thu tài chính hoặc mang tính chu kỳ."),
        _assessment_metric("other_income_share", "Thu nhập khác / tổng nguồn thu", other_share, "%", "Tỷ trọng nguồn thu có khả năng bất thường."),
    ]
    if revenue > 0:
        metrics.extend([
            _assessment_metric("gross_margin", "Biên lợi nhuận gộp TTM", _ratio(gross_profit, revenue), "%", "Hiệu quả hoạt động cốt lõi trong bốn quý gần nhất."),
            _assessment_metric("net_margin", "Biên lợi nhuận ròng TTM", _ratio(npat, revenue), "%", "LNST tạo ra trên một đồng doanh thu."),
            _assessment_metric("inventory_to_revenue", "Tồn kho / doanh thu TTM", _ratio(inventory, revenue), "%", "Quy mô tồn kho cuối kỳ so với doanh thu bốn quý."),
            _assessment_metric("receivables_to_revenue", "Phải thu / doanh thu TTM", _ratio(receivables, revenue), "%", "Mức doanh thu đang nằm ở công nợ."),
            _assessment_metric("finance_cost_to_revenue", "Chi phí tài chính / doanh thu TTM", _ratio(finance_cost, revenue), "%", "Áp lực tài chính trên doanh thu."),
        ])
        if profile["archetype"] in {"REAL_ESTATE", "INDUSTRIAL_PARK"}:
            metrics.append(_assessment_metric("prepayments_to_revenue", "Người mua trả trước / doanh thu TTM", _ratio(prepayments, revenue), "%", "Tín hiệu bán hàng và nguồn tiền khách hàng đã khóa."))
    if profile["archetype"] not in {"BANKING", "SECURITIES", "FINANCIAL_SERVICES", "INSURANCE"}:
        metrics.append(_assessment_metric("cfo_to_npat", "CFO / LNST TTM", _ratio(cfo, npat, 1.0), "x", "Mức lợi nhuận kế toán được hỗ trợ bởi tiền thật."))

    warnings = []
    if core_share is not None and core_share < 60:
        warnings.append("Nguồn thu cốt lõi dưới 60% tổng nguồn thu dương; chất lượng doanh thu cần thận trọng.")
    if finance_share is not None and finance_share > 25:
        warnings.append("Nguồn thu tài chính vượt 25%; cần đọc thuyết minh để đánh giá khả năng lặp lại.")
    cfo_to_npat = _ratio(cfo, npat, 1.0)
    if cfo_to_npat is not None and cfo_to_npat < 0:
        warnings.append("CFO TTM trái dấu với LNST; lợi nhuận chưa chuyển thành tiền từ hoạt động kinh doanh.")
    elif cfo_to_npat is not None and cfo_to_npat < 0.8:
        warnings.append("CFO/LNST TTM dưới 0,8x; khả năng chuyển đổi lợi nhuận thành tiền còn yếu.")
    if profile.get("cyclical"):
        warnings.append("Ngành có tính chu kỳ: không annualize một quý đơn lẻ để dự phóng cả năm.")
    if profile.get("project_based"):
        warnings.append("Doanh thu theo dự án có thể lệch mạnh giữa các quý; cần đọc tiến độ bàn giao và thuyết minh dự án.")

    score = 100
    score -= 25 if core_share is not None and core_share < 60 else 0
    score -= 20 if finance_share is not None and finance_share > 25 else 0
    score -= 25 if cfo_to_npat is not None and cfo_to_npat < 0 else (15 if cfo_to_npat is not None and cfo_to_npat < 0.8 else 0)
    label = "Tốt" if score >= 80 else "Cần theo dõi" if score >= 60 else "Cần thận trọng"
    return {
        "score": max(0, score),
        "label": label,
        "metrics": metrics,
        "warnings": warnings,
        "methodology": "Tỷ trọng dùng các dòng thu nhập dương của kỳ hiển thị; chỉ tiêu chất lượng dùng TTM và số dư cuối kỳ.",
    }


def _reported_disclosure(symbol: str) -> Optional[dict]:
    disclosure = COMPANY_DISCLOSURES.get(symbol)
    if not disclosure:
        return None
    total = disclosure["total_revenue_billion"]
    segments = []
    for index, item in enumerate(disclosure["segments"]):
        percentage = float(item["percentage"])
        segments.append({
            "name": item["name"],
            "amount_billion": round(total * percentage / 100, 1),
            "percentage": percentage,
            "color": COLORS[index % len(COLORS)],
            "children": item.get("children", []),
            "value_status": "reported_percentage_derived_amount",
        })
    reconciliation = round(sum(item["percentage"] for item in segments), 4)
    return {
        "symbol": symbol,
        "status": "available",
        "classification": "issuer_business_disclosure",
        "title": disclosure["title"],
        "period": disclosure["period"],
        "period_end": disclosure["period_end"],
        "total_revenue_billion": total,
        "segments": segments,
        "dimensions": disclosure.get("dimensions", []),
        "source": disclosure["source"],
        "limitations": disclosure.get("limitations", []),
        "reconciliation": {
            "percentage_sum": reconciliation,
            "passed": abs(reconciliation - 100) <= 0.1,
            "tolerance_pct": 0.1,
        },
        "confidence": "high",
    }


def _accounting_breakdown(
    symbol: str,
    archetype: str,
    get_is_item: Callable,
    period: str,
    statement_source: str,
    get_bs_item: Optional[Callable] = None,
    get_cf_item: Optional[Callable] = None,
) -> dict:
    if archetype == "SECURITIES":
        components = [
            ("Lãi từ cho vay & margin", ["Lãi từ các khoản cho vay và phải thu", "Doanh thu cho vay"]),
            ("Lãi tài sản FVTPL", ["Lãi từ các tài sản tài chính ghi nhận thông qua lãi/lỗ ( FVTPL)", "Lãi từ các tài sản tài chính ghi nhận thông qua lãi/lỗ (FVTPL)", "Lãi bán các tài sản tài chính FVTPL"]),
            ("Doanh thu môi giới", ["Doanh thu nghiệp vụ môi giới chứng khoán", "Doanh thu môi giới"]),
            ("Doanh thu tư vấn & bảo lãnh", ["Doanh thu nghiệp vụ tư vấn đầu tư chứng khoán", "Doanh thu nghiệp vụ bảo lãnh phát hành chứng khoán", "Doanh thu tư vấn tài chính"]),
            ("Thu nhập hoạt động khác", ["Doanh thu hoạt động khác", "Thu nhập khác"]),
        ]
        title = "Cơ cấu thu nhập hoạt động từ BCTC TTM"
    elif archetype in {"BANKING", "BANK"}:
        components = [
            ("Thu nhập lãi thuần (NII)", ["Thu nhập lãi thuần", "Thu nhập lãi và các khoản thu nhập tương tự"]),
            ("Lãi thuần dịch vụ & phí", ["Lãi/Lỗ thuần từ hoạt động dịch vụ", "Lãi thuần từ hoạt động dịch vụ", "Thu nhập từ dịch vụ", "Thu nhập từ hoạt động dịch vụ"]),
            ("Kinh doanh ngoại hối", ["Lãi/(lỗ) thuần từ kinh doanh ngoại hối", "Lãi thuần từ kinh doanh ngoại hối", "Lãi/lỗ thuần từ kinh doanh ngoại hối và vàng"]),
            ("Kinh doanh chứng khoán", ["Lãi/(lỗ) thuần từ mua bán chứng khoán kinh doanh", "Lãi/(lỗ) thuần từ mua bán chứng khoán đầu tư", "Lãi thuần từ mua bán chứng khoán kinh doanh", "Lãi thuần từ mua bán chứng khoán đầu tư"]),
            ("Thu nhập hoạt động khác", ["Lãi/(lỗ) thuần từ hoạt động khác", "Lãi thuần từ hoạt động khác", "Thu nhập hoạt động khác", "Thu nhập khác", "Thu nhập từ cổ tức"]),
        ]
        title = "Cơ cấu thu nhập hoạt động từ BCTC TTM"
    elif archetype == "FINANCIAL_SERVICES":
        components = [
            ("Thu nhập lãi thuần", ["Thu nhập lãi thuần", "Thu nhập lãi và các khoản thu nhập tương tự"]),
            ("Thu nhập phí & dịch vụ", ["Lãi thuần từ hoạt động dịch vụ", "Thu nhập từ hoạt động dịch vụ", "Doanh thu dịch vụ"]),
            ("Thu nhập bảo hiểm", ["Doanh thu phí bảo hiểm", "Lãi thuần từ hoạt động kinh doanh bảo hiểm"]),
            ("Thu hồi/xử lý nợ", ["Thu nhập từ xử lý nợ", "Thu nhập từ thu hồi nợ đã xử lý"]),
            ("Thu nhập hoạt động khác", ["Lãi thuần từ hoạt động khác", "Thu nhập hoạt động khác", "Thu nhập khác"]),
        ]
        title = "Cơ cấu thu nhập dịch vụ tài chính từ BCTC TTM"
    else:
        components = [
            ("Phí bảo hiểm gốc", ["Doanh thu phí bảo hiểm", "Phí bảo hiểm gốc"]),
            ("Phí bảo hiểm thuần", ["Doanh thu thuần hoạt động kinh doanh bảo hiểm", "Doanh thu phí bảo hiểm thuần"]),
            ("Thu nhập đầu tư tài chính", ["Doanh thu hoạt động tài chính", "Thu nhập từ hoạt động đầu tư"]),
            ("Thu nhập hoạt động khác", ["Thu nhập hoạt động khác", "Thu nhập khác"]),
        ]
        title = "Cơ cấu nguồn thu bảo hiểm từ BCTC TTM"

    positive, negative = [], []
    for name, aliases in components:
        value = _number(get_is_item(aliases))
        if value is None or value == 0:
            continue
        item = {"name": name, "amount_billion": round(value / 1e9, 1)}
        (positive if value > 0 else negative).append(item)

    total_positive = sum(item["amount_billion"] for item in positive)
    for index, item in enumerate(positive):
        item.update({
            "percentage": round(item["amount_billion"] / total_positive * 100, 1) if total_positive > 0 else 0,
            "color": COLORS[index % len(COLORS)],
            "children": [],
            "value_status": "reported_statement_line",
        })

    quality_assessment = None
    if get_bs_item and callable(get_bs_item):
        quality_assessment = _build_quality_assessment(
            archetype, positive, get_is_item, get_bs_item, get_cf_item
        )

    return {
        "symbol": symbol,
        "archetype": archetype,
        "status": "available" if positive else "unavailable",
        "classification": "accounting_income_breakdown",
        "title": title,
        "period": period,
        "total_revenue_ttm_billion": round(total_positive, 1),
        "segments": positive,
        "negative_components": negative,
        "source": {"publisher": statement_source, "document": "Báo cáo tài chính chuẩn hóa", "url": None},
        "limitations": ["Đây là cơ cấu dòng thu nhập kế toán, không phải cơ cấu sản phẩm hoặc khách hàng."],
        "industry_profile": _profile_payload(archetype),
        "quality_assessment": quality_assessment,
        "confidence": "medium" if positive else "unavailable",
    }


def _business_income_breakdown(
    symbol: str,
    archetype: str,
    get_is_period_item: Callable,
    reported_periods: list[str],
    latest_reported_period: Optional[str],
    statement_source: str,
    get_is_ttm_item: Callable,
    get_bs_item: Callable,
    get_cf_ttm_item: Optional[Callable],
) -> dict:
    """Build a reported accounting-income mix, with a clearly labelled period fallback."""
    periods = [str(item) for item in reported_periods if item]
    if latest_reported_period and latest_reported_period not in periods:
        periods.insert(0, latest_reported_period)

    components = [
        ("Doanh thu thuần", [
            "Doanh thu thuần",
            "Doanh thu bán hàng và cung cấp dịch vụ",
            "Doanh thu thuần về hoạt động kinh doanh",
        ]),
        ("Doanh thu hoạt động tài chính", [
            "Doanh thu hoạt động tài chính",
            "Thu nhập tài chính",
        ]),
        ("Lãi từ công ty liên doanh, liên kết", [
            "Phần lãi/(lỗ) trong công ty liên doanh, liên kết",
            "Lãi/(lỗ) từ công ty liên doanh, liên kết",
        ]),
        ("Thu nhập khác, ròng", [
            "Thu nhập khác, ròng",
            "Lợi nhuận khác",
        ]),
    ]

    selected_period = None
    positive: list[dict] = []
    negative: list[dict] = []
    for candidate_period in periods:
        period_positive: list[dict] = []
        period_negative: list[dict] = []
        for name, aliases in components:
            value = _number(get_is_period_item(aliases, candidate_period))
            if value is None or value == 0:
                continue
            item = {"name": name, "amount_billion": round(value / 1e9, 1)}
            (period_positive if value > 0 else period_negative).append(item)
        if period_positive:
            selected_period = candidate_period
            positive = period_positive
            negative = period_negative
            break

    if not selected_period:
        return {
            "symbol": symbol,
            "archetype": archetype,
            "status": "unavailable",
            "classification": "accounting_income_sources",
            "title": "Cơ cấu nguồn thu nhập kế toán",
            "period": None,
            "target_period": latest_reported_period,
            "segments": [],
            "source": None,
            "limitations": ["Không tìm thấy dòng doanh thu hoặc thu nhập dương trong các kỳ báo cáo gần nhất."],
            "message": "Chưa có dữ liệu nguồn thu nhập đủ điều kiện để dựng biểu đồ.",
            "industry_profile": _profile_payload(archetype),
            "confidence": "unavailable",
        }

    total_positive = sum(item["amount_billion"] for item in positive)
    for index, item in enumerate(positive):
        item.update({
            "percentage": round(item["amount_billion"] / total_positive * 100, 1) if total_positive > 0 else 0,
            "color": COLORS[index % len(COLORS)],
            "children": [],
            "value_status": "reported_statement_line",
        })

    fallback_used = bool(latest_reported_period and selected_period != latest_reported_period)
    return {
        "symbol": symbol,
        "archetype": archetype,
        "status": "available",
        "classification": "accounting_income_sources",
        "title": "Cơ cấu nguồn thu nhập kế toán theo BCTC",
        "period": selected_period,
        "target_period": latest_reported_period,
        "period_match": not fallback_used,
        "fallback_used": fallback_used,
        "total_income_billion": round(total_positive, 1),
        "segments": positive,
        "negative_components": negative,
        "source": {
            "publisher": statement_source,
            "document": "Báo cáo kết quả hoạt động kinh doanh chuẩn hóa",
            "url": None,
            "evidence": "Số liệu lấy trực tiếp từ các dòng doanh thu/thu nhập của kỳ báo cáo được hiển thị.",
        },
        "limitations": [
            "Đây là cơ cấu nguồn thu nhập kế toán, không phải cơ cấu theo dự án, sản phẩm hoặc khách hàng.",
            "Doanh thu tài chính và thu nhập khác có thể không lặp lại; cần đọc thuyết minh trước khi dự phóng.",
        ],
        "industry_profile": _profile_payload(archetype),
        "quality_assessment": _build_quality_assessment(
            archetype, positive, get_is_ttm_item, get_bs_item, get_cf_ttm_item
        ),
        "confidence": "high_reported_lines",
    }


def build_revenue_structure(
    symbol: str,
    archetype: str,
    get_is_item: Callable,
    get_bs_item: Callable,
    period: str = "TTM",
    statement_source: str = "Nguồn BCTC chuẩn hóa",
    latest_reported_period: Optional[str] = None,
    get_is_period_item: Optional[Callable] = None,
    reported_periods: Optional[list[str]] = None,
    get_cf_item: Optional[Callable] = None,
    get_cf_period_item: Optional[Callable] = None,
    get_bs_period_item: Optional[Callable] = None,
) -> dict:
    """Build revenue structure with historical trends and sector-specific insights."""
    symbol = symbol.upper().strip()
    disclosure = _reported_disclosure(symbol)

    # Build historical trends if we have multi-period data
    historical_trends = None
    segment_trends = None

    if get_is_period_item and reported_periods:
        historical_trends = _build_historical_trends(
            symbol, archetype, get_is_period_item, reported_periods, get_bs_period_item
        )
        segment_trends = _build_segment_trend_analysis(
            symbol, archetype, get_is_period_item, reported_periods
        )

    if disclosure:
        disclosure["archetype"] = archetype
        disclosure["target_period"] = latest_reported_period
        disclosure["period_match"] = periods_compatible(disclosure.get("period"), latest_reported_period)
        disclosure["fallback_used"] = not disclosure["period_match"]
        if disclosure["fallback_used"]:
            disclosure["limitations"] = [
                f"Đây là công bố gần nhất ({disclosure.get('period')}), không phải số liệu {latest_reported_period}.",
                *disclosure.get("limitations", []),
            ]
        disclosure["industry_profile"] = _profile_payload(archetype)
        disclosure["quality_assessment"] = _build_quality_assessment(
            archetype, disclosure["segments"], get_is_item, get_bs_item, get_cf_item
        )
        # Add trends if available
        if historical_trends:
            disclosure["historical_trends"] = historical_trends
        if segment_trends and segment_trends.get("status") == "available":
            disclosure["segment_trends"] = segment_trends
        return disclosure

    if archetype in {"SECURITIES", "BANKING", "BANK", "FINANCIAL_SERVICES", "INSURANCE"}:
        result = _accounting_breakdown(symbol, archetype, get_is_item, period, statement_source, get_bs_item=get_bs_item, get_cf_item=get_cf_item)
        if historical_trends:
            result["historical_trends"] = historical_trends
        if segment_trends and segment_trends.get("status") == "available":
            result["segment_trends"] = segment_trends
        return result

    if get_is_period_item and reported_periods:
        result = _business_income_breakdown(
            symbol,
            archetype,
            get_is_period_item,
            list(reported_periods),
            latest_reported_period,
            statement_source,
            get_is_item,
            get_bs_item,
            get_cf_item,
        )
        # Add trends
        if historical_trends:
            result["historical_trends"] = historical_trends
        if segment_trends and segment_trends.get("status") == "available":
            result["segment_trends"] = segment_trends
        return result

    result = {
        "symbol": symbol,
        "archetype": archetype,
        "status": "unavailable",
        "classification": "not_disclosed",
        "title": "Cơ cấu doanh thu theo sản phẩm/kênh",
        "period": None,
        "target_period": latest_reported_period,
        "segments": [],
        "source": None,
        "limitations": [
            "BCTC chuẩn hóa chỉ cung cấp tổng doanh thu, không chứng minh được cơ cấu sản phẩm.",
            "Ứng dụng không tự ước lượng tỷ trọng khi doanh nghiệp chưa công bố dữ liệu phù hợp.",
        ],
        "message": "Chưa có cơ cấu sản phẩm/kênh bán được doanh nghiệp công bố và kiểm chứng.",
        "industry_profile": _profile_payload(archetype),
        "confidence": "unavailable",
    }
    # Still add trends if available even for unavailable status
    if historical_trends:
        result["historical_trends"] = historical_trends
    if segment_trends and segment_trends.get("status") == "available":
        result["segment_trends"] = segment_trends
    return result
