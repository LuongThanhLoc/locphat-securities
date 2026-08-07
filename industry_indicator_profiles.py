"""Canonical industry analytics profiles shared by heatmap and stock analysis.

The profile keys are the same granular archetypes used by the heatmap.  This
module deliberately keeps expected business disclosures separate from values
available in standardized financial statements: an expected source is context,
never permission to synthesize a number.
"""

from __future__ import annotations


def metric(key: str, label: str, statement: str, nature: str = "flow", aliases: tuple[str, ...] = ()) -> dict:
    return {
        "key": key,
        "label": label,
        "statement": statement,
        "nature": nature,
        "unit": "ty_vnd",
        "aliases": list(aliases),
    }


PERIOD = {"key": "period", "label": "Kỳ Báo Cáo", "statement": "metadata", "nature": "period"}

METRICS = {
    "npat": metric("npat", "LNST", "income_statement", aliases=(
        "Lợi nhuận sau thuế", "Lợi nhuận của Cổ đông của Công ty mẹ", "Cổ đông của Công ty mẹ",
        "Lãi/(lỗ) thuần sau thuế", "LỢI NHUẬN KẾ TOÁN SAU THUẾ", "LỢI NHUẬN SAU THUẾ TNDN", "LNST",
    )),
    "rev": metric("rev", "Doanh Thu Thuần", "income_statement", aliases=(
        "Doanh thu thuần", "Doanh thu bán hàng và cung cấp dịch vụ", "Doanh thu thuần về hoạt động kinh doanh",
        "DOANH THU HOẠT ĐỘNG",
    )),
    "gross_profit": metric("gross_profit", "Lợi Nhuận Gộp", "income_statement", aliases=(
        "LỢI NHUẬN GỘP", "Lợi nhuận gộp", "LỢI NHUẬN GỘP VỀ BÁN HÀNG VÀ CUNG CẤP DỊCH VỤ",
    )),
    "financial_income": metric("financial_income", "Doanh Thu Tài Chính", "income_statement", aliases=(
        "Doanh thu hoạt động tài chính", "Thu nhập tài chính", "Thu nhập từ hoạt động đầu tư",
    )),
    "net_interest_inc": metric("net_interest_inc", "Thu Nhập Lãi Thuần", "income_statement", aliases=(
        "Thu nhập lãi thuần", "Thu nhập lãi và các khoản thu nhập tương tự",
    )),
    "brokerage_income": metric("brokerage_income", "Doanh Thu Môi Giới", "income_statement", aliases=(
        "Doanh thu nghiệp vụ môi giới chứng khoán", "Doanh thu môi giới",
    )),
    "premium_revenue": metric("premium_revenue", "Doanh Thu Phí Bảo Hiểm", "income_statement", aliases=(
        "Doanh thu phí bảo hiểm", "Doanh thu thuần hoạt động kinh doanh bảo hiểm", "Phí bảo hiểm gốc",
    )),
    "equity": metric("equity", "Vốn CSH", "balance_sheet", "stock", aliases=(
        "Vốn chủ sở hữu", "VỐN CHỦ SỞ HỮU", "Vốn góp của chủ sở hữu",
    )),
    "inventory": metric("inventory", "Hàng Tồn Kho", "balance_sheet", "stock", aliases=(
        "Hàng tồn kho, ròng", "Hàng tồn kho",
    )),
    "project_inventory": metric("project_inventory", "Hàng Tồn Kho Dự Án", "balance_sheet", "stock", aliases=(
        "Hàng tồn kho, ròng", "Hàng tồn kho", "Bất động sản dở dang",
    )),
    "prepayments": metric("prepayments", "Người Mua Trả Tiền Trước", "balance_sheet", "stock", aliases=(
        "Người mua trả tiền trước", "Người mua trả tiền trước ngắn hạn", "Người mua trả tiền trước dài hạn",
    )),
    "deferred_revenue": metric("deferred_revenue", "Doanh Thu Chưa Thực Hiện", "balance_sheet", "stock", aliases=(
        "Doanh thu chưa thực hiện", "Doanh thu chưa thực hiện ngắn hạn", "Doanh thu chưa thực hiện dài hạn",
    )),
    "receivables": metric("receivables", "Khoản Phải Thu", "balance_sheet", "stock", aliases=(
        "Tổng các khoản phải thu", "Các khoản phải thu (từ 2016)", "Phải thu ngắn hạn", "Phải thu khách hàng",
    )),
    "fixed_assets": metric("fixed_assets", "Tài Sản Cố Định", "balance_sheet", "stock", aliases=(
        "Tài sản cố định", "Tài sản cố định hữu hình", "Giá trị còn lại của TSCĐ hữu hình",
    )),
    "loans": metric("loans", "Dư Nợ Tín Dụng", "balance_sheet", "stock", aliases=(
        "Cho vay khách hàng", "Các khoản cho vay",
    )),
    "margin_loans": metric("margin_loans", "Dư Nợ Margin", "balance_sheet", "stock", aliases=(
        "Các khoản cho vay", "Dư nợ cho vay", "Phải thu về cho vay ngắn hạn",
    )),
    "fvtpl_assets": metric("fvtpl_assets", "Tài Sản FVTPL", "balance_sheet", "stock", aliases=(
        "Các tài sản tài chính ghi nhận thông qua lãi lỗ (FVTPL)",
        "Các tài sản tài chính ghi nhận thông qua lãi/lỗ (FVTPL)",
    )),
    "insurance_reserve": metric("insurance_reserve", "Dự Phòng Nghiệp Vụ", "balance_sheet", "stock", aliases=(
        "Dự phòng nghiệp vụ bảo hiểm", "Dự phòng toán học", "Dự phòng phí chưa được hưởng",
    )),
    "cfo": metric("cfo", "Dòng Tiền CFO", "cash_flow", aliases=(
        "Dòng tiền thuần từ hoạt động kinh doanh", "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
    )),
}


def _profile(name: str, trend: tuple[str, ...], sources: tuple[str, ...], indicators: tuple[str, ...],
             cautions: tuple[str, ...] = (), *, cyclical: bool = False, project_based: bool = False) -> dict:
    return {
        "name": name,
        "trend_metrics": list(trend),
        "expected_revenue_sources": list(sources),
        "key_indicators": list(indicators),
        "cautions": list(cautions),
        "cyclical": cyclical,
        "project_based": project_based,
    }


INDUSTRY_PROFILES = {
    "STEEL": _profile("Thép", ("rev", "gross_profit", "inventory", "npat"),
        ("Thép xây dựng", "HRC", "Tôn mạ/ống thép", "Xuất khẩu"),
        ("Biên lợi nhuận gộp", "Tồn kho/doanh thu", "Chi phí tài chính/doanh thu", "CFO/LNST"),
        ("Doanh thu tăng nhưng biên gộp giảm", "Tồn kho tăng khi giá thép đảo chiều"), cyclical=True),
    "BANKING": _profile("Ngân hàng", ("equity", "loans", "net_interest_inc", "npat"),
        ("Thu nhập lãi thuần", "Phí dịch vụ", "Ngoại hối", "Chứng khoán", "Thu nhập khác"),
        ("NIM", "CASA", "NPL", "Bao phủ nợ xấu", "CIR", "Chi phí dự phòng"),
        ("NIM giảm liên tục", "Lợi nhuận tăng chủ yếu do giảm dự phòng")),
    "SECURITIES": _profile("Chứng khoán", ("equity", "margin_loans", "fvtpl_assets", "npat"),
        ("Môi giới", "Cho vay margin", "FVTPL/tự doanh", "Tư vấn & bảo lãnh", "Lưu ký"),
        ("Dư nợ margin", "Tự doanh/tổng thu nhập", "Margin/tổng thu nhập", "ROE"),
        ("Phụ thuộc quá nhiều vào tự doanh", "Môi giới giảm nhưng lợi nhuận tăng nhờ tự doanh"), cyclical=True),
    "FINANCIAL_SERVICES": _profile("Dịch vụ tài chính", ("equity", "loans", "net_interest_inc", "npat"),
        ("Lãi cho vay", "Phí dịch vụ", "Bảo hiểm liên kết", "Xử lý nợ", "Thu nhập khác"),
        ("Tăng trưởng dư nợ", "Biên lãi", "Chi phí vốn", "Nợ xấu", "Chi phí dự phòng"),
        ("Dư nợ tăng cùng nợ xấu", "Lợi nhuận phụ thuộc thu hồi nợ")),
    "INSURANCE": _profile("Bảo hiểm", ("premium_revenue", "financial_income", "insurance_reserve", "npat"),
        ("Phí bảo hiểm gốc", "Tái bảo hiểm", "Phí bảo hiểm thuần", "Đầu tư tài chính"),
        ("Tăng trưởng phí", "Tỷ lệ bồi thường", "Combined ratio", "Dự phòng nghiệp vụ"),
        ("Combined ratio trên 100%", "Lợi nhuận chủ yếu từ đầu tư tài chính")),
    "REAL_ESTATE": _profile("Bất động sản", ("rev", "project_inventory", "prepayments", "npat"),
        ("Bàn giao bất động sản", "Chuyển nhượng đất/dự án", "Cho thuê", "Dịch vụ quản lý"),
        ("Biên lợi nhuận gộp", "Hàng tồn kho", "Người mua trả tiền trước", "CFO", "Nợ vay", "Tỷ trọng doanh thu tài chính"),
        ("Không annualize một quý", "Tồn kho cao nhưng người mua trả tiền trước thấp", "LNST tăng nhưng CFO âm"), project_based=True),
    "INDUSTRIAL_PARK": _profile("BĐS Khu công nghiệp", ("rev", "deferred_revenue", "project_inventory", "npat"),
        ("Cho thuê đất KCN", "Cho thuê nhà xưởng", "Dịch vụ hạ tầng", "Điện/nước/xử lý nước thải"),
        ("Doanh thu chưa thực hiện", "Tỷ lệ lấp đầy", "Quỹ đất còn lại", "CFO", "XDCB dở dang"),
        ("Ghi nhận một lần tiền thuê đất", "Doanh thu chưa thực hiện giảm liên tục"), project_based=True),
    "CONSTRUCTION": _profile("Đầu tư công", ("rev", "receivables", "cfo", "npat"),
        ("Xây lắp", "Hạ tầng", "BOT/PPP", "Bất động sản"),
        ("Biên lợi nhuận gộp", "Backlog", "Phải thu/doanh thu", "CFO", "Chi phí lãi vay"),
        ("Doanh thu tăng nhưng phải thu tăng nhanh", "Lợi nhuận dương nhưng CFO âm"), project_based=True),
    "BUILDING_MATERIALS": _profile("VLXD", ("rev", "gross_profit", "inventory", "npat"),
        ("Vật liệu xây dựng", "Xuất khẩu", "Dịch vụ vận chuyển"),
        ("Biên lợi nhuận gộp", "Giá vốn/doanh thu", "Vòng quay tồn kho", "Chi phí tài chính/doanh thu"),
        ("Tồn kho tăng khi doanh thu giảm", "Thu nhập thanh lý tài sản không phải cốt lõi"), cyclical=True),
    "CHEMICALS_FERTILIZERS": _profile("Hóa chất - Phân bón", ("rev", "gross_profit", "inventory", "npat"),
        ("Phân bón", "Hóa chất công nghiệp", "Xuất khẩu"),
        ("Biên lợi nhuận gộp", "Giá vốn/doanh thu", "Tồn kho", "Tỷ trọng xuất khẩu"),
        ("Lợi nhuận đột biến theo giá hàng hóa", "Tồn kho cao khi chu kỳ đảo chiều"), cyclical=True),
    "RUBBER": _profile("Cao su", ("rev", "gross_profit", "inventory", "npat"),
        ("Mủ cao su", "Gỗ cao su", "Thanh lý cây", "Đất/KCN"),
        ("Biên lợi nhuận gộp", "Sản lượng", "Giá bán", "CFO", "Thu nhập chuyển đổi đất"),
        ("Lợi nhuận từ thanh lý cây/đất có thể không lặp lại",), cyclical=True),
    "OIL_GAS": _profile("Dầu khí", ("rev", "gross_profit", "fixed_assets", "npat"),
        ("Thăm dò/khai thác", "Khoan & kỹ thuật", "Vận chuyển", "Phân phối/lọc hóa dầu"),
        ("Giá dầu", "Sản lượng", "Backlog", "Biên lợi nhuận gộp", "CFO"),
        ("Không annualize quý thuận lợi", "Lãi tỷ giá có thể không lặp lại"), cyclical=True),
    "POWER_ENERGY": _profile("Điện - Năng lượng", ("rev", "gross_profit", "fixed_assets", "npat"),
        ("Bán điện", "Dịch vụ điện", "Xây lắp năng lượng"),
        ("Sản lượng điện", "Giá bán bình quân", "Chi phí nhiên liệu", "Khấu hao", "Nợ vay"),
        ("Lợi nhuận phụ thuộc thủy văn/giá nhiên liệu",), cyclical=True),
    "MINING": _profile("Khoáng sản", ("rev", "gross_profit", "inventory", "npat"),
        ("Khai thác khoáng sản", "Chế biến", "Xuất khẩu"),
        ("Sản lượng", "Giá bán", "Trữ lượng", "Thuế tài nguyên", "CFO"),
        ("Lợi nhuận theo chu kỳ giá hàng hóa", "Trữ lượng suy giảm"), cyclical=True),
    "AUTOMOTIVE": _profile("Ô tô - Phụ tùng", ("rev", "gross_profit", "inventory", "npat"),
        ("Phân phối ô tô", "Sản xuất phụ tùng/lốp", "Dịch vụ hậu mãi"),
        ("Biên lợi nhuận gộp", "Tồn kho/doanh thu", "Phải thu", "CFO/LNST"),
        ("Tồn kho tăng khi sức mua giảm",), cyclical=True),
    "TEXTILE": _profile("Dệt may", ("rev", "gross_profit", "inventory", "npat"),
        ("CMT", "FOB", "ODM", "Sợi/vải", "Xuất khẩu"),
        ("Biên lợi nhuận gộp", "Order book", "Tồn kho", "Tỷ giá", "CFO"),
        ("Đơn hàng tăng nhưng biên giảm", "Tồn kho nguyên liệu tăng"), cyclical=True),
    "SEAFOOD": _profile("Thủy sản", ("rev", "gross_profit", "inventory", "npat"),
        ("Cá tra", "Tôm", "Sản phẩm chế biến", "Xuất khẩu"),
        ("Giá bán xuất khẩu", "Sản lượng", "Tự chủ nguyên liệu", "Tồn kho", "CFO"),
        ("Rủi ro thuế chống bán phá giá", "Tồn kho tăng khi nhu cầu xuất khẩu yếu"), cyclical=True),
    "FOOD_BEVERAGE": _profile("Thực phẩm", ("rev", "gross_profit", "inventory", "npat"),
        ("Thực phẩm", "Đồ uống", "Sữa", "Kênh nội địa/xuất khẩu"),
        ("Biên lợi nhuận gộp", "Tồn kho", "Chi phí bán hàng/doanh thu", "CFO/LNST"),
        ("Doanh thu tăng nhưng chi phí bán hàng tăng nhanh",)),
    "RETAIL": _profile("Bán lẻ", ("rev", "gross_profit", "inventory", "npat"),
        ("Bán lẻ tại cửa hàng", "Bán sỉ", "Online", "Dịch vụ"),
        ("SSSG", "Biên lợi nhuận gộp", "DSI", "CCC", "Chi phí bán hàng/doanh thu"),
        ("Tồn kho tăng nhanh hơn doanh thu", "Mở rộng cửa hàng nhưng SSSG âm")),
    "PHARMA_HEALTHCARE": _profile("Dược - Y tế", ("rev", "gross_profit", "inventory", "npat"),
        ("Kênh ETC", "Kênh OTC", "Sản xuất", "Phân phối"),
        ("Tỷ trọng ETC/OTC", "Biên lợi nhuận gộp", "R&D", "Tồn kho", "CFO"),
        ("Doanh thu tăng nhờ đấu thầu nhưng biên giảm",)),
    "TECH_TELECOM": _profile("Công nghệ - Truyền thông", ("rev", "gross_profit", "cfo", "npat"),
        ("CNTT/chuyển đổi số", "Viễn thông", "Xuất khẩu phần mềm", "Dịch vụ số"),
        ("Tăng trưởng doanh thu", "Biên lợi nhuận gộp", "CFO/LNST", "Phải thu/doanh thu"),
        ("Doanh thu tăng nhưng phải thu tăng nhanh",)),
    "AVIATION_TOURISM": _profile("Hàng không - Du lịch", ("rev", "gross_profit", "fixed_assets", "npat"),
        ("Vận tải hành khách", "Hàng hóa", "Dịch vụ phụ trợ", "Du lịch/khách sạn"),
        ("Load factor", "Giá nhiên liệu", "Doanh thu/ASK", "Nợ vay", "CFO"),
        ("Lợi nhuận nhạy với nhiên liệu và tỷ giá",), cyclical=True),
    "PORTS_LOGISTICS": _profile("Cảng biển - Vận tải", ("rev", "gross_profit", "fixed_assets", "npat"),
        ("Xếp dỡ cảng", "Kho bãi/logistics", "Vận tải biển", "Cho thuê tàu"),
        ("Sản lượng", "Giá cước", "Biên lợi nhuận gộp", "Hiệu suất TSCĐ", "CFO"),
        ("Giá cước và sản lượng mang tính chu kỳ",), cyclical=True),
    "WATER_PLASTICS": _profile("Nước - Nhựa", ("rev", "gross_profit", "fixed_assets", "npat"),
        ("Cấp nước", "Ống/bao bì nhựa", "Dịch vụ liên quan"),
        ("Biên lợi nhuận gộp", "Sản lượng", "Thất thoát nước", "Giá hạt nhựa", "CFO"),
        ("Cần tách cấp nước phòng thủ và nhựa theo chu kỳ",)),
    "SUGAR_WOOD_PAPER": _profile("Đường - Gỗ - Giấy", ("rev", "gross_profit", "inventory", "npat"),
        ("Đường", "Gỗ", "Giấy/bao bì", "Điện sinh khối"),
        ("Biên lợi nhuận gộp", "Tồn kho", "Giá nguyên liệu", "CFO/LNST"),
        ("Lợi nhuận nhạy với giá hàng hóa và mùa vụ",), cyclical=True),
    "MANUFACTURING_GENERAL": _profile("Sản xuất công nghiệp", ("rev", "gross_profit", "inventory", "npat"),
        ("Bán hàng hóa/sản phẩm", "Dịch vụ", "Xuất khẩu"),
        ("Biên lợi nhuận gộp", "Tồn kho/doanh thu", "Phải thu/doanh thu", "CFO/LNST"),
        ("Cần xác minh ngành chi tiết trước khi dùng chuẩn so sánh",)),
}


# Full indicator checklists transcribed from stock_revenue_industry_indicators.txt.
# The trend chart intentionally shows only four statement-grounded series; this
# checklist remains available to the UI and analysis engines for deeper review.
FULL_KEY_INDICATORS = {
    "STEEL": ("Tăng trưởng doanh thu thuần", "Biên lợi nhuận gộp", "Giá vốn hàng bán / doanh thu", "Hàng tồn kho / doanh thu", "Vòng quay hàng tồn kho", "Chi phí tài chính / doanh thu", "Lãi vay / lợi nhuận gộp", "Lợi nhuận sau thuế / doanh thu"),
    "BANKING": ("Thu nhập lãi thuần", "Tỷ trọng thu nhập ngoài lãi", "NIM", "CASA", "Tăng trưởng tín dụng", "CIR", "Chi phí dự phòng rủi ro tín dụng", "Nợ xấu", "Tỷ lệ bao phủ nợ xấu", "Lợi nhuận trước thuế"),
    "SECURITIES": ("Doanh thu môi giới", "Lãi cho vay margin", "Dư nợ cho vay margin", "Lãi/lỗ tự doanh", "Tỷ trọng tự doanh / tổng thu nhập", "Tỷ trọng margin / tổng thu nhập", "Chi phí hoạt động / doanh thu hoạt động", "Lợi nhuận trước thuế", "ROE"),
    "FINANCIAL_SERVICES": ("Tăng trưởng dư nợ cho vay", "Thu nhập lãi", "Biên lãi ròng", "Chi phí vốn", "Nợ xấu", "Chi phí dự phòng", "Thu nhập phí / tổng thu nhập", "Lợi nhuận sau thuế", "Dòng tiền hoạt động"),
    "INSURANCE": ("Tăng trưởng phí bảo hiểm gốc", "Phí bảo hiểm giữ lại", "Tỷ lệ bồi thường", "Tỷ lệ chi phí khai thác", "Combined ratio", "Lợi nhuận từ hoạt động bảo hiểm", "Lợi nhuận từ đầu tư tài chính", "Dự phòng nghiệp vụ bảo hiểm"),
    "REAL_ESTATE": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Hàng tồn kho", "Người mua trả tiền trước", "Doanh thu chưa thực hiện", "Dòng tiền từ hoạt động kinh doanh", "Nợ vay", "Chi phí lãi vay", "Tỷ trọng doanh thu tài chính", "Tỷ trọng thu nhập khác"),
    "INDUSTRIAL_PARK": ("Doanh thu cho thuê đất", "Doanh thu chưa thực hiện", "Người thuê trả tiền trước", "Biên lợi nhuận gộp", "Tỷ lệ lấp đầy nếu có", "Diện tích đất thương phẩm còn lại nếu có", "Dòng tiền kinh doanh", "Nợ vay", "Chi phí xây dựng cơ bản dở dang"),
    "CONSTRUCTION": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Giá trị hợp đồng/backlog nếu có", "Phải thu khách hàng", "Phải thu / doanh thu", "Dòng tiền kinh doanh", "Hàng tồn kho dở dang", "Nợ vay", "Chi phí lãi vay"),
    "BUILDING_MATERIALS": ("Tăng trưởng doanh thu", "Biên lợi nhuận gộp", "Giá vốn / doanh thu", "Hàng tồn kho", "Vòng quay hàng tồn kho", "Chi phí bán hàng / doanh thu", "Chi phí tài chính / doanh thu", "Lợi nhuận sau thuế"),
    "CHEMICALS_FERTILIZERS": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Giá vốn / doanh thu", "Hàng tồn kho", "Doanh thu xuất khẩu / tổng doanh thu", "Chi phí bán hàng", "Lợi nhuận sau thuế", "Biên lợi nhuận ròng"),
    "RUBBER": ("Doanh thu cao su cốt lõi", "Biên lợi nhuận gộp", "Giá vốn / doanh thu", "Hàng tồn kho", "Tài sản sinh học", "Thu nhập khác / tổng thu nhập", "Dòng tiền kinh doanh", "Lợi nhuận sau thuế"),
    "OIL_GAS": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Hàng tồn kho", "Lãi/lỗ từ công ty liên doanh liên kết", "Chi phí tài chính", "Lãi/lỗ tỷ giá", "Dòng tiền kinh doanh", "Lợi nhuận sau thuế"),
    "POWER_ENERGY": ("Doanh thu bán điện", "Biên lợi nhuận gộp", "EBITDA margin", "Sản lượng điện nếu có", "Chi phí lãi vay", "Nợ vay", "Dòng tiền kinh doanh", "Lợi nhuận sau thuế"),
    "MINING": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Giá vốn / doanh thu", "Hàng tồn kho", "Tài sản cố định", "Chi phí khai thác", "Lợi nhuận sau thuế", "Dòng tiền kinh doanh"),
    "AUTOMOTIVE": ("Tăng trưởng doanh thu", "Biên lợi nhuận gộp", "Hàng tồn kho", "Vòng quay hàng tồn kho", "Chi phí bán hàng / doanh thu", "Phải thu khách hàng", "Lợi nhuận sau thuế", "Biên lợi nhuận ròng"),
    "TEXTILE": ("Doanh thu thuần", "Doanh thu xuất khẩu / tổng doanh thu", "Biên lợi nhuận gộp", "Hàng tồn kho", "Phải thu khách hàng", "Chi phí bán hàng", "Chi phí quản lý", "Dòng tiền kinh doanh", "Lợi nhuận sau thuế"),
    "SEAFOOD": ("Doanh thu thuần", "Doanh thu xuất khẩu / tổng doanh thu", "Biên lợi nhuận gộp", "Hàng tồn kho / doanh thu", "Phải thu khách hàng", "Chi phí bán hàng / doanh thu", "Lợi nhuận sau thuế", "Dòng tiền kinh doanh"),
    "FOOD_BEVERAGE": ("Tăng trưởng doanh thu", "Biên lợi nhuận gộp", "Chi phí bán hàng / doanh thu", "Biên lợi nhuận hoạt động", "Biên lợi nhuận ròng", "Hàng tồn kho", "Dòng tiền kinh doanh / lợi nhuận sau thuế", "ROE"),
    "RETAIL": ("Tăng trưởng doanh thu", "Biên lợi nhuận gộp", "Chi phí bán hàng / doanh thu", "Biên lợi nhuận hoạt động", "Hàng tồn kho", "Vòng quay hàng tồn kho", "Phải trả nhà cung cấp", "Dòng tiền kinh doanh", "Lợi nhuận sau thuế"),
    "PHARMA_HEALTHCARE": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Chi phí bán hàng / doanh thu", "Chi phí quản lý / doanh thu", "Phải thu khách hàng", "Dòng tiền kinh doanh", "Biên lợi nhuận ròng", "ROE"),
    "TECH_TELECOM": ("Tăng trưởng doanh thu", "Tỷ trọng doanh thu dịch vụ lặp lại", "Biên lợi nhuận gộp", "Biên EBIT", "Phải thu khách hàng", "Chi phí nhân sự nếu có", "Dòng tiền kinh doanh / lợi nhuận sau thuế", "ROE"),
    "AVIATION_TOURISM": ("Tăng trưởng doanh thu", "Biên lợi nhuận gộp", "Biên lợi nhuận hoạt động", "Chi phí nhiên liệu nếu có", "Chi phí bán hàng / doanh thu", "Chi phí tài chính / doanh thu", "Nợ vay", "Dòng tiền kinh doanh", "Lợi nhuận sau thuế"),
    "PORTS_LOGISTICS": ("Doanh thu thuần", "Biên lợi nhuận gộp", "EBITDA margin", "Tài sản cố định", "Hiệu suất sử dụng tài sản", "Dòng tiền kinh doanh", "Nợ vay", "Lợi nhuận sau thuế"),
    "WATER_PLASTICS": ("Tăng trưởng doanh thu", "Biên lợi nhuận gộp", "Biên lợi nhuận ròng", "Hàng tồn kho", "Vòng quay hàng tồn kho", "Giá vốn / doanh thu", "Dòng tiền kinh doanh / lợi nhuận sau thuế", "Nợ vay"),
    "SUGAR_WOOD_PAPER": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Giá vốn / doanh thu", "Hàng tồn kho", "Doanh thu xuất khẩu / tổng doanh thu", "Chi phí bán hàng", "Chi phí tài chính", "Dòng tiền kinh doanh", "Lợi nhuận sau thuế"),
    "MANUFACTURING_GENERAL": ("Doanh thu thuần", "Biên lợi nhuận gộp", "Hàng tồn kho / doanh thu", "Phải thu / doanh thu", "Chi phí tài chính / doanh thu", "Dòng tiền kinh doanh / lợi nhuận sau thuế"),
}


ALIASES = {"BANK": "BANKING", "REAL_ESTATE_RESIDENTIAL": "REAL_ESTATE", "REAL_ESTATE_INDUSTRIAL": "INDUSTRIAL_PARK"}


def canonical_archetype(archetype: str) -> str:
    key = str(archetype or "MANUFACTURING_GENERAL").upper()
    return ALIASES.get(key, key if key in INDUSTRY_PROFILES else "MANUFACTURING_GENERAL")


def get_industry_profile(archetype: str) -> dict:
    key = canonical_archetype(archetype)
    profile = {"archetype": key, **INDUSTRY_PROFILES[key]}
    profile["key_indicators"] = list(FULL_KEY_INDICATORS[key])
    return profile


def get_trend_schema(archetype: str) -> dict:
    profile = get_industry_profile(archetype)
    return {
        "archetype": profile["archetype"],
        "sector_name": profile["name"],
        "columns": [PERIOD, *[METRICS[key] for key in profile["trend_metrics"]]],
    }
