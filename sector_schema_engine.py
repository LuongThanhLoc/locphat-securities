# Sector Schema Engine: Computes dynamic Block 2 metrics for 8 Core Industry Archetypes

from sector_mapping import get_sector_info
import pandas as pd

def safe_div(num, den, multiplier=100.0, default=0.0):
    if den is None or den == 0 or num is None:
        return default
    return round((num / den) * multiplier, 1)

def fmt_pct(val, default_text="N/A"):
    if val is None or pd.isna(val) or val == 0:
        return default_text
    try:
        f = float(val)
        abs_f = abs(f)
        if abs_f <= 5.0:
            f = f * 100.0
        return f"{round(abs(f), 1)}%"
    except:
        return default_text

def build_sector_financial_health(symbol: str, get_bs_item, get_is_item, get_ratio_item, current_price: float, issue_share: float, comp_overview: dict = None, get_cf_item=None) -> dict:
    get_cf_item = get_cf_item or get_is_item
    info = get_sector_info(symbol, comp_overview=comp_overview, get_bs_item=get_bs_item)
    sector_name = info["sector"]
    archetype = info["archetype"]

    # Common Balance Sheet & Income Statement numbers
    equity = get_bs_item(['Vốn chủ sở hữu', 'VỐN CHỦ SỞ HỮU', 'Vốn góp của chủ sở hữu'])
    total_assets = get_bs_item(['TỔNG CỘNG TÀI SẢN', 'Tài sản'])
    total_liabilities = get_bs_item(['NỢ PHẢI TRẢ', 'Nợ phải trả'])
    total_borrowing = get_bs_item(['Vay ngắn hạn', 'Vay và nợ thuê tài sản tài chính ngắn hạn', 'Nợ vay ngắn hạn']) + get_bs_item(['Vay dài hạn', 'Vay và nợ thuê tài sản tài chính dài hạn', 'Nợ vay dài hạn'])
    cash = get_bs_item(['Tiền và tương đương tiền']) + get_bs_item(['Các khoản đầu tư tài chính ngắn hạn', 'Tiền gửi ngân hàng'])
    inventory = abs(get_bs_item(['Hàng tồn kho, ròng', 'Hàng tồn kho']))
    receivables = abs(get_bs_item(['Tổng các khoản phải thu', 'Các khoản phải thu (từ 2016)', 'Phải thu ngắn hạn', 'Phải thu khách hàng']))
    
    # Financial Income Statement metrics
    rev = abs(get_is_item(['Doanh thu thuần', 'Doanh thu bán hàng và cung cấp dịch vụ', 'Doanh thu thuần về hoạt động kinh doanh', 'DOANH THU HOẠT ĐỘNG']))
    cogs = abs(get_is_item(['Giá vốn hàng bán', 'CHI PHÍ HOẠT ĐỘNG']))
    gross_profit = abs(get_is_item(['LỢI NHUẬN GỘP', 'Lợi nhuận gộp'])) or (rev - cogs if rev > cogs else 0.0)
    gross_margin = safe_div(gross_profit, rev, 100.0)
    
    npat = get_is_item(['Lợi nhuận của Cổ đông của Công ty mẹ', 'Lãi/(lỗ) thuần sau thuế', 'LỢI NHUẬN KẾ TOÁN SAU THUẾ', 'LNST'])
    net_debt = total_borrowing - cash
    de_ratio = safe_div(net_debt, equity, 100.0)
    net_cash = (cash - total_borrowing) / 1e9

    metrics = []
    detail_metrics = []
    risk_warning = ""
    badge_label = f"Mô hình: {sector_name}"

    # 1. ARCHETYPE: BANKING / BANK
    if archetype in ["BANKING", "BANK"]:
        deposits = get_bs_item(['Tiền gửi của khách hàng', 'Tiền gửi khách hàng', 'Tiền gửi và vay các TCTD khác'])
        casa_item = get_bs_item(['Tiền gửi không kỳ hạn', 'Tiền gửi bằng tiền đồng không kỳ hạn', 'Tiền gửi ngoại tệ không kỳ hạn'])
        
        ratio_nim = get_ratio_item(['Biên lãi thuần', 'NIM (%)', 'NIM', 'netInterestMargin'])
        ratio_npl = get_ratio_item(['Nợ xấu (%)', 'Tỷ lệ nợ xấu', 'NPL (%)', 'npl'])
        ratio_llr = get_ratio_item(['DP rủi ro/Nợ xấu', 'Tỷ lệ bao phủ nợ xấu (%)', 'LLR (%)', 'loansLossReservesToNPLs', 'loansLossReserveToLoans'])
        ratio_car = get_ratio_item(['CAR', 'CAR (%)', 'Tỷ lệ an toàn vốn', 'car', 'totalEquityTotalAsset'])
        ratio_cred = get_ratio_item(['Tăng trưởng cho vay (%)', 'Tăng trưởng tín dụng (%)', 'loansGrowth', 'creditGrowth'])

        if (ratio_car == 0.0 or pd.isna(ratio_car)) and equity > 0 and total_assets > 0:
            ratio_car = equity / total_assets

        nim_val = fmt_pct(ratio_nim, "N/A")
        npl_val = fmt_pct(ratio_npl, "N/A")
        llr_val = fmt_pct(ratio_llr, "N/A")
        
        casa_pct = safe_div(casa_item, deposits, 100.0) if deposits > 0 and casa_item > 0 else get_ratio_item(['CASA (%)', 'Tỷ lệ CASA', 'casaRatio'])
        casa_val = fmt_pct(casa_pct, "N/A")
        cred_val = fmt_pct(ratio_cred, "N/A")
        car_val = fmt_pct(ratio_car, "N/A")

        metrics = [
            {"label": "Tăng trưởng Tín dụng TTM", "value": cred_val, "badge": "success", "subtext": "Tốc độ mở rộng dư nợ cho vay"},
            {"label": "Biên lãi thuần (NIM)", "value": nim_val, "badge": "primary", "subtext": "Tỷ suất sinh lời tài sản sinh lãi"},
            {"label": "Tỷ lệ Nợ xấu (NPL)", "value": npl_val, "badge": "primary", "subtext": "Nợ nhóm 3-5 / Cho vay"},
            {"label": "Bao phủ Nợ xấu (LLCR)", "value": llr_val, "badge": "success", "subtext": "Quỹ dự phòng rủi ro tín dụng"}
        ]
        detail_metrics = [
            {"label": "Tỷ lệ CASA", "value": casa_val, "desc": "Tiền gửi không kỳ hạn giá rẻ"},
            {"label": "Tỷ lệ An toàn vốn (CAR)", "value": car_val, "desc": "Quy định Basel II > 8.0%"}
        ]
        risk_warning = "⚠️ Cần theo dõi chất lượng tài sản, rủi ro nợ xấu từ nhóm BĐS & Trái phiếu doanh nghiệp."

    # 2. ARCHETYPE: SECURITIES
    elif archetype == "SECURITIES":
        margin_loans = get_bs_item(['Các khoản cho vay', 'Phải thu về cho vay ngắn hạn'])
        fvtpl = get_bs_item(['Các tài sản tài chính ghi nhận thông qua lãi lỗ (FVTPL)'])
        margin_to_equity = safe_div(margin_loans, equity, 100.0)
        fvtpl_to_assets = safe_div(fvtpl, total_assets, 100.0)
        roe = safe_div(npat, equity, 100.0)

        car_ratio = get_ratio_item(['CAR (%)', 'Tỷ lệ an toàn tài chính (CAR)', 'CAR', 'capitalAdequacyRatio'])
        if (car_ratio == 0.0 or pd.isna(car_ratio)) and equity > 0 and total_assets > 0:
            car_ratio = equity / total_assets
        car_val = fmt_pct(car_ratio, "N/A")

        metrics = [
            {"label": "Dư nợ Margin / VCSH", "value": f"{margin_to_equity}%", "badge": "success" if margin_to_equity < 120 else "warning", "subtext": "Trần pháp lý: Max 200%"},
            {"label": "Tỷ lệ CAR (An toàn)", "value": car_val, "badge": "success", "subtext": "Quy định tối thiểu > 180%"},
            {"label": "FVTPL / Tổng tài sản", "value": f"{fvtpl_to_assets}%", "badge": "primary", "subtext": "Quy mô Tự doanh cổ phiếu & trái phiếu"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 15 else "primary", "subtext": "Hiệu quả sử dụng vốn CSH"}
        ]
        detail_metrics = [
            {"label": "Dư địa Margin còn lại", "value": f"{((equity * 2.0 - margin_loans)/1e9):,.1f} tỷ" if equity > 0 else "N/A", "desc": "Hạn mức tối đa cho vay mở rộng"},
            {"label": "Cơ cấu Tự doanh FVTPL", "value": f"FVTPL: {(fvtpl/1e9):,.1f} tỷ", "desc": "Tỷ trọng danh mục ghi nhận lãi/lỗ"}
        ]
        risk_warning = "⚠️ Khẩu vị Tự doanh biến động mạnh theo xu hướng VN-Index và áp lực thanh khoản Trái phiếu."

    # 3. ARCHETYPE: REAL_ESTATE / REAL_ESTATE_RESIDENTIAL / REAL_ESTATE_INDUSTRIAL / INDUSTRIAL_PARK
    elif archetype in ["REAL_ESTATE", "REAL_ESTATE_RESIDENTIAL", "REAL_ESTATE_INDUSTRIAL", "INDUSTRIAL_PARK"]:
        unearned_short = get_bs_item(['Người mua trả tiền trước', 'Người mua trả tiền trước ngắn hạn', 'Doanh thu chưa thực hiện ngắn hạn'])
        unearned_long = get_bs_item(['Người mua trả tiền trước dài hạn', 'Doanh thu chưa thực hiện dài hạn'])
        prepayments = unearned_short + unearned_long
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
        net_debt = total_borrowing - cash

        metrics = [
            {"label": "Người mua Trả tiền trước", "value": f"{(prepayments/1e9):,.1f} tỷ", "badge": "success" if prepayments > 1e12 else "primary", "subtext": "Doanh thu chờ ghi nhận từ dự án"},
            {"label": "Hàng tồn kho Dự án", "value": f"{(inventory/1e9):,.1f} tỷ", "badge": "primary", "subtext": "Quỹ đất & Chi phí dở dang dự án"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{safe_div(net_debt, equity, 100.0)}%", "badge": "success" if safe_div(net_debt, equity, 100.0) < 100 else "warning", "subtext": "Nợ vay tài chính trừ tiền mặt / vốn chủ sở hữu"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "danger", "subtext": "Tiền thực thu từ mở bán dự án"}
        ]
        detail_metrics = [
            {"label": "Tổng Nợ Vay Tài Chính", "value": f"{(total_borrowing/1e9):,.1f} tỷ", "desc": "Nợ vay ngân hàng & trái phiếu doanh nghiệp"},
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "desc": "Hiệu quả biên lợi nhuận bàn giao nhà/đất"}
        ]
        risk_warning = "⚠️ Vướng mắc pháp lý phê duyệt dự án & áp lực đáo hạn trái phiếu doanh nghiệp."

    # 4. ARCHETYPE: STEEL (THÉP)
    elif archetype == "STEEL":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp Thép", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 12 else "primary", "subtext": "Spread giá bán HRC/Thép xây dựng - quặng sắt/than coke"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Dung lượng tồn kho thành phẩm & nguyên liệu quặng"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 70 else "warning", "subtext": "Tỷ lệ đòn bẩy tài chính tài tài trợ dự án"},
            {"label": "Dòng tiền CFO TTM", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "danger", "subtext": "Dòng tiền thuần thu về từ hoạt động kinh doanh thô"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho Thành phẩm/Quặng", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Tồn kho phôi thép, HRC & thép xây dựng sẵn sàng bán"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính đảm bảo tiến độ dự án mở rộng công suất"}
        ]
        risk_warning = "⚠️ Biến động giá quặng sắt thế giới, than củi/coke & chính sách thuế chống bán phá giá HRC."

    # 5. ARCHETYPE: INSURANCE (BẢO HIỂM)
    elif archetype == "INSURANCE":
        premium_rev = abs(get_is_item(['Doanh thu phí bảo hiểm', 'Doanh thu thuần hoạt động kinh doanh bảo hiểm', 'Doanh thu thuần']))
        claims = abs(get_is_item(['Chi phí bồi thường bảo hiểm', 'Chi bồi thường bảo hiểm']))
        invest_inc = abs(get_is_item(['Doanh thu hoạt động tài chính', 'Thu nhập từ hoạt động đầu tư']))
        reserve = abs(get_bs_item(['Dự phòng nghiệp vụ bảo hiểm', 'Quỹ dự phòng nghiệp vụ']))
        combined_ratio = safe_div(claims + cogs, premium_rev, 100.0) if premium_rev > 0 else 0.0
        invest_yield = safe_div(invest_inc, total_assets, 100.0)
        roe = safe_div(npat, equity, 100.0)

        metrics = [
            {"label": "Tỷ lệ kết hợp (Combined Ratio)", "value": f"{combined_ratio}%", "badge": "success" if combined_ratio < 95 else "warning", "subtext": "< 100% = Lãi từ hoạt động bảo hiểm"},
            {"label": "Hiệu suất đầu tư Tài sản", "value": f"{invest_yield}%", "badge": "primary", "subtext": "Thu nhập đầu tư / Tổng tài sản"},
            {"label": "Dự phòng nghiệp vụ BH", "value": f"{(reserve/1e9):,.1f} tỷ", "badge": "primary", "subtext": "Quỹ dự phòng bồi thường"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 12 else "primary", "subtext": "Hiệu quả sử dụng vốn CSH"}
        ]
        detail_metrics = [
            {"label": "Doanh thu phí Bảo hiểm", "value": f"{(premium_rev/1e9):,.1f} tỷ", "desc": "Phí bảo hiểm thu được trong kỳ"},
            {"label": "Tổng Nợ vay / VCSH", "value": f"{de_ratio}%", "desc": "Mức đòn bẩy tài chính"}
        ]
        risk_warning = "⚠️ Rủi ro thiên tai/dịch bệnh tăng chi phí bồi thường bất thường & biến động danh mục đầu tư."

    # 6. ARCHETYPE: INDUSTRIAL_PARK (BĐS KHU CÔNG NGHIỆP)
    elif archetype == "INDUSTRIAL_PARK":
        unearned = get_bs_item(['Người mua trả tiền trước', 'Người mua trả tiền trước ngắn hạn']) + get_bs_item(['Doanh thu chưa thực hiện dài hạn', 'Người mua trả tiền trước dài hạn'])
        rental_rev = abs(get_is_item(['Doanh thu cho thuê', 'Doanh thu thuần']))
        occupancy_proxy = safe_div(rental_rev, total_assets, 100.0) if total_assets > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Doanh thu cho thuê KCN", "value": f"{(rental_rev/1e9):,.1f} tỷ", "badge": "success" if rental_rev > 1e12 else "primary", "subtext": "Cho thuê đất & hạ tầng KCN"},
            {"label": "Hiệu suất Tài sản cho thuê", "value": f"{occupancy_proxy}%", "badge": "primary", "subtext": "Proxy tỷ lệ lấp đầy KCN"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 80 else "warning", "subtext": "Đòn bẩy đầu tư hạ tầng"},
            {"label": "Khách trả trước (Backlog)", "value": f"{(unearned/1e9):,.1f} tỷ", "badge": "success" if unearned > 500e9 else "primary", "subtext": "Doanh thu chờ ghi nhận tương lai"}
        ]
        detail_metrics = [
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "desc": "Tiền thực thu từ hoạt động kinh doanh"},
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "desc": "Biên lợi nhuận cho thuê hạ tầng"}
        ]
        risk_warning = "⚠️ Phụ thuộc vào dòng vốn FDI & chính sách thuế ưu đãi đầu tư."

    # 7. ARCHETYPE: OIL_GAS (DẦU KHÍ)
    elif archetype == "OIL_GAS":
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
        capex = abs(get_cf_item(['Tiền chi mua sắm, xây dựng TSCĐ', 'Tiền chi đầu tư mua sắm TSCĐ', 'Chi mua sắm TSCĐ']))
        if capex == 0:
            capex = abs(get_bs_item(['Tài sản cố định', 'Tài sản cố định hữu hình'])) * 0.08
        roe = safe_div(npat, equity, 100.0)

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 15 else "primary", "subtext": "Phản ánh biến động giá dầu/khí"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thực thu từ khai thác/dịch vụ"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 80 else "warning", "subtext": "Đòn bẩy tài tài trợ dự án thăm dò"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 15 else "primary", "subtext": "Hiệu quả vốn chủ sở hữu"}
        ]
        detail_metrics = [
            {"label": "CAPEX TTM", "value": f"{(capex/1e9):,.1f} tỷ" if capex > 0 else f"{(rev * 0.05 / 1e9):,.1f} tỷ", "desc": "Chi phí đầu tư TSCĐ & thăm dò khai thác"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính sau trừ nợ vay"}
        ]
        risk_warning = "⚠️ Biến động giá dầu thô thế giới & rủi ro địa chính trị ảnh hưởng trực tiếp biên lợi nhuận."

    # 8. ARCHETYPE: POWER_ENERGY (ĐIỆN - NĂNG LƯỢNG)
    elif archetype == "POWER_ENERGY":
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
        fixed_assets = abs(get_bs_item(['Tài sản cố định', 'Tài sản cố định hữu hình']))
        asset_util = safe_div(rev, fixed_assets, 100.0) if fixed_assets > 0 else 0.0
        roe = safe_div(npat, equity, 100.0)

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 20 else "primary", "subtext": "Hiệu suất giá bán điện - chi phí nhiên liệu"},
            {"label": "Hiệu suất Tài sản CĐ", "value": f"{asset_util}%", "badge": "primary", "subtext": "Doanh thu / TSCĐ (% hiệu suất nhà máy)"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ bán điện EVN/khách hàng"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 12 else "primary", "subtext": "Hiệu quả vốn chủ sở hữu"}
        ]
        detail_metrics = [
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy vốn vay đầu tư nhà máy điện"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính sau nợ vay"}
        ]
        risk_warning = "⚠️ Phụ thuộc vào giá bán điện EVN, thủy văn/gió/bức xạ & cơ chế DPPA."

    # 9. ARCHETYPE: MINING (KHOÁNG SẢN)
    elif archetype == "MINING":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 15 else "primary", "subtext": "Spread giá bán quặng/than - chi phí khai thác"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ giải phóng quặng/than tồn kho"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 60 else "warning", "subtext": "Đòn bẩy tài chính mỏ khai thác"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thực thu từ bán quặng/than"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Quặng/than thành phẩm & nguyên liệu chờ bán"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính đảm bảo hoạt động khai thác"}
        ]
        risk_warning = "⚠️ Biến động giá hàng hóa thế giới, chi phí khai thác tăng & rủi ro môi trường."

    # 10. ARCHETYPE: CHEMICALS_FERTILIZERS (HÓA CHẤT - PHÂN BÓN)
    elif archetype == "CHEMICALS_FERTILIZERS":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 20 else "primary", "subtext": "Spread giá bán phân bón/hóa chất - giá khí/nguyên liệu"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ tiêu thụ phân bón/hóa chất"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 50 else "warning", "subtext": "Đòn bẩy tài chính"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu thực từ bán hàng"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Phân bón/hóa chất tồn kho chờ tiêu thụ"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính ròng"}
        ]
        risk_warning = "⚠️ Biến động giá khí đầu vào, rủi ro thời vụ nông nghiệp & cạnh tranh giá nhập khẩu."

    # 11. ARCHETYPE: CONSTRUCTION (XÂY DỰNG - ĐẦU TƯ CÔNG)
    elif archetype == "CONSTRUCTION":
        backlog = get_bs_item(['Người mua trả tiền trước', 'Người mua trả tiền trước ngắn hạn', 'Doanh thu chưa thực hiện ngắn hạn']) + get_bs_item(['Doanh thu chưa thực hiện dài hạn', 'Người mua trả tiền trước dài hạn'])
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 10 else "warning", "subtext": "Biên thầu xây dựng - nhà thầu phụ"},
            {"label": "Backlog (Trả trước)", "value": f"{(backlog/1e9):,.1f} tỷ", "badge": "success" if backlog > 500e9 else "primary", "subtext": "Giá trị hợp đồng chờ thi công"},
            {"label": "Phải thu Khách hàng", "value": f"{(receivables/1e9):,.1f} tỷ", "badge": "warning" if receivables > equity * 0.5 else "primary", "subtext": "Rủi ro nợ xấu từ chủ đầu tư"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thực thu từ nghiệm thu khối lượng"}
        ]
        detail_metrics = [
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy tài chính nhà thầu"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính sau nợ vay"}
        ]
        risk_warning = "⚠️ Rủi ro chậm giải ngân đầu tư công, phải thu xấu từ chủ đầu tư & biến động giá VLXD."

    # 12. ARCHETYPE: BUILDING_MATERIALS (VẬT LIỆU XÂY DỰNG)
    elif archetype == "BUILDING_MATERIALS":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 20 else "primary", "subtext": "Biên lợi nhuận xi măng/đá/gạch"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ tiêu thụ vật liệu"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 70 else "warning", "subtext": "Đòn bẩy tài chính"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu thực từ bán VLXD"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Tồn kho xi măng/đá/gạch thành phẩm"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính ròng"}
        ]
        risk_warning = "⚠️ Cung vượt cầu ngành xi măng, chi phí vận chuyển & phụ thuộc chu kỳ BĐS."

    # 13. ARCHETYPE: AUTOMOTIVE (Ô TÔ - PHỤ TÙNG)
    elif archetype == "AUTOMOTIVE":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 15 else "primary", "subtext": "Biên lợi nhuận phân phối/sản xuất ô tô"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ tiêu thụ xe/phụ tùng"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 80 else "warning", "subtext": "Đòn bẩy tài chính"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thực thu từ bán hàng"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Xe & phụ tùng tồn kho"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính ròng"}
        ]
        risk_warning = "⚠️ Rủi ro thay đổi thuế tiêu thụ đặc biệt, cạnh tranh giá & xu hướng xe điện."

    # 14. ARCHETYPE: TEXTILE (DỆT MAY)
    elif archetype == "TEXTILE":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 12 else "primary", "subtext": "Biên gia công/FOB - chi phí nguyên phụ liệu"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ hoàn thành đơn hàng xuất khẩu"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 60 else "warning", "subtext": "Đòn bẩy tài chính"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ xuất khẩu dệt may"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Chủ yếu từ đơn hàng xuất khẩu FOB/CMT"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính ròng"}
        ]
        risk_warning = "⚠️ Biến động tỷ giá USD/VND, đơn hàng xuất khẩu suy giảm & cạnh tranh từ Bangladesh."

    # 15. ARCHETYPE: SEAFOOD (THỦY SẢN)
    elif archetype == "SEAFOOD":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 15 else "primary", "subtext": "Spread giá xuất khẩu cá/tôm - chi phí nuôi trồng"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ xuất khẩu hàng tồn kho thủy sản"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 60 else "warning", "subtext": "Đòn bẩy tài chính ngành thủy sản"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ xuất khẩu thủy sản"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Cá tra/tôm đông lạnh & nguyên liệu chế biến"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính ròng"}
        ]
        risk_warning = "⚠️ Rủi ro thuế chống bán phá giá Mỹ/EU, giá nguyên liệu thức ăn & thời tiết nuôi trồng."

    # 16. ARCHETYPE: FOOD_BEVERAGE (THỰC PHẨM & ĐỒ UỐNG)
    elif archetype == "FOOD_BEVERAGE":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        roe = safe_div(npat, equity, 100.0)

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 25 else "primary", "subtext": "Sức mạnh thương hiệu & sức mua tiêu dùng"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ tiêu thụ sản phẩm qua kênh phân phối"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 18 else "primary", "subtext": "Hiệu quả vốn chủ sở hữu"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "primary", "subtext": "Dư địa tiền mặt sau nợ vay"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Tổng doanh thu bán hàng & dịch vụ"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy tài chính"}
        ]
        risk_warning = "⚠️ Cạnh tranh thương hiệu, biến động giá nguyên liệu đầu vào & sức mua người tiêu dùng."

    # 17. ARCHETYPE: RETAIL (BÁN LẺ)
    elif archetype == "RETAIL":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        payables = abs(get_bs_item(['Phải trả người bán ngắn hạn', 'Phải trả người bán']))
        dpo = safe_div(payables, cogs, 365.0) if cogs > 0 else 0.0
        ccc = max(round(dsi - dpo, 1), 0.0)

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 18 else "primary", "subtext": "Biên bán lẻ & chiết khấu nhà cung cấp"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ bán hàng qua chuỗi cửa hàng"},
            {"label": "Chu kỳ Tiền mặt (CCC)", "value": f"{ccc:.0f} ngày" if ccc > 0 else "< 30 ngày", "badge": "success" if ccc < 30 else "warning", "subtext": "Thời gian chuyển hàng thành tiền"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "primary", "subtext": "Dư địa tài chính ròng"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Tổng doanh thu bán hàng chuỗi lẻ"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy tài chính mở rộng chuỗi"}
        ]
        risk_warning = "⚠️ Cạnh tranh kênh online, chi phí mặt bằng & sức mua tiêu dùng suy giảm."

    # 18. ARCHETYPE: PHARMA_HEALTHCARE (DƯỢC - Y TẾ)
    elif archetype == "PHARMA_HEALTHCARE":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        roe = safe_div(npat, equity, 100.0)

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 35 else "primary", "subtext": "Biên sản xuất dược phẩm/thiết bị y tế"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ phân phối thuốc qua kênh ETC/OTC"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 18 else "primary", "subtext": "Hiệu quả vốn chủ sở hữu"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "primary", "subtext": "Dư địa đầu tư R&D & mở rộng"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Tổng doanh thu dược phẩm & thiết bị y tế"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy tài chính"}
        ]
        risk_warning = "⚠️ Rủi ro thay đổi chính sách đấu thầu thuốc, cạnh tranh thuốc generic & quản lý chất lượng."

    # 19. ARCHETYPE: TECH_TELECOM (CÔNG NGHỆ - TRUYỀN THÔNG)
    elif archetype == "TECH_TELECOM":
        roe = safe_div(npat, equity, 100.0)
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 25 else "primary", "subtext": "Biên phần mềm/dịch vụ CNTT - chi phí nhân sự"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 20 else "primary", "subtext": "Hiệu quả vốn - tài sản nhẹ, ROE cao"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ dịch vụ CNTT/viễn thông"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "primary", "subtext": "Dư địa M&A & đầu tư công nghệ mới"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Doanh thu CNTT/viễn thông/chuyển đổi số"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy tài chính (thường thấp)"}
        ]
        risk_warning = "⚠️ Cạnh tranh nhân sự CNTT, rủi ro chậm thu tiền dự án & phụ thuộc vào ngân sách IT khách hàng."

    # 20. ARCHETYPE: AVIATION_TOURISM (HÀNG KHÔNG - DU LỊCH)
    elif archetype == "AVIATION_TOURISM":
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
        fixed_assets = abs(get_bs_item(['Tài sản cố định', 'Tài sản cố định hữu hình']))

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 12 else "warning", "subtext": "Biên vận tải HK - chi phí nhiên liệu/thuê máy bay"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "danger", "subtext": "Tiền thu từ bán vé & dịch vụ hàng không"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 200 else "danger", "subtext": "Đòn bẩy tài chính (thuê tài chính máy bay)"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "danger", "subtext": "Thanh khoản ngắn hạn"}
        ]
        detail_metrics = [
            {"label": "TSCĐ (Máy bay & hạ tầng)", "value": f"{(fixed_assets/1e9):,.1f} tỷ", "desc": "Giá trị tàu bay/sân bay/cơ sở hạ tầng"},
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Tổng doanh thu vận tải & dịch vụ hàng không"}
        ]
        risk_warning = "⚠️ Biến động giá nhiên liệu Jet-A1, tỷ giá thuê máy bay USD & rủi ro dịch bệnh/thiên tai."

    # 21. ARCHETYPE: PORTS_LOGISTICS (CẢNG BIỂN - VẬN TẢI)
    elif archetype == "PORTS_LOGISTICS":
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
        fixed_assets = abs(get_bs_item(['Tài sản cố định', 'Tài sản cố định hữu hình']))
        asset_util = safe_div(rev, fixed_assets, 100.0) if fixed_assets > 0 else 0.0

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 20 else "primary", "subtext": "Biên xếp dỡ/dịch vụ cảng"},
            {"label": "Hiệu suất Tài sản CĐ", "value": f"{asset_util}%", "badge": "primary", "subtext": "Doanh thu / TSCĐ (hiệu suất cầu cảng)"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ dịch vụ cảng/logistics"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "primary", "subtext": "Dư địa tài chính ròng"}
        ]
        detail_metrics = [
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy đầu tư mở rộng cầu cảng"},
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Tổng doanh thu dịch vụ cảng & vận tải biển"}
        ]
        risk_warning = "⚠️ Phụ thuộc vào sản lượng xuất nhập khẩu, cạnh tranh cảng & chi phí đầu tư hạ tầng."

    # 22. ARCHETYPE: RUBBER (CAO SU)
    elif archetype == "RUBBER":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 20 else "primary", "subtext": "Spread giá mủ cao su - chi phí khai thác"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ tiêu thụ mủ cao su"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 30 else "warning", "subtext": "Đòn bẩy tài chính (thường thấp)"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ bán mủ & gỗ cao su"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Mủ cao su & gỗ cao su tồn kho"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính (có quỹ đất lớn)"}
        ]
        risk_warning = "⚠️ Biến động giá cao su thế giới, thời tiết mùa vụ & chuyển đổi quỹ đất BĐS KCN."

    # 23. ARCHETYPE: WATER_PLASTICS (NƯỚC - NHỰA)
    elif archetype == "WATER_PLASTICS":
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])
        roe = safe_div(npat, equity, 100.0)

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 25 else "primary", "subtext": "Biên lợi nhuận cấp nước/sản xuất nhựa"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 15 else "primary", "subtext": "Hiệu quả vốn chủ sở hữu"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ bán nước/nhựa"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "primary", "subtext": "Dư địa tài chính ròng"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Doanh thu cấp nước/ống nhựa/bao bì nhựa"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "desc": "Đòn bẩy tài chính đầu tư hạ tầng"}
        ]
        risk_warning = "⚠️ Rủi ro giá nguyên liệu nhựa (giá dầu), chính sách giá nước & cạnh tranh ngành."

    # 24. ARCHETYPE: SUGAR_WOOD_PAPER (ĐƯỜNG - GỖ - GIẤY)
    elif archetype == "SUGAR_WOOD_PAPER":
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 else 0.0
        cfo = get_cf_item(['Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ hoạt động kinh doanh'])

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 15 else "primary", "subtext": "Biên sản xuất đường/gỗ/giấy"},
            {"label": "Vòng quay Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "< 30 ngày", "badge": "primary", "subtext": "Tốc độ tiêu thụ sản phẩm"},
            {"label": "Nợ vay ròng / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 60 else "warning", "subtext": "Đòn bẩy tài chính"},
            {"label": "Dòng tiền CFO", "value": f"{(cfo/1e9):,.1f} tỷ" if cfo != 0 else f"{(npat/1e9):,.1f} tỷ (LNST)", "badge": "success" if cfo > 0 else "primary", "subtext": "Tiền thu từ bán đường/gỗ/giấy"}
        ]
        detail_metrics = [
            {"label": "Hàng Tồn kho", "value": f"{(inventory/1e9):,.1f} tỷ", "desc": "Đường/gỗ/giấy thành phẩm & nguyên liệu"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "desc": "Dư địa tài chính ròng"}
        ]
        risk_warning = "⚠️ Cạnh tranh đường nhập khẩu, giá gỗ biến động & nhu cầu giấy giảm do số hóa."

    # 25. ARCHETYPE: FINANCIAL_SERVICES (DỊCH VỤ TÀI CHÍNH / TÀI CHÍNH TIÊU DÙNG / CẦM ĐỒ)
    elif archetype == "FINANCIAL_SERVICES":
        loans = get_bs_item(['Cho vay khách hàng', 'Các khoản cho vay', 'Phải thu về cho vay ngắn hạn', 'Phải thu về cho vay', 'Phải thu cho vay'])
        borrowings = total_borrowing or get_bs_item(['Vay ngắn hạn', 'Vay dài hạn', 'Trái phiếu phát hành', 'Vay và nợ thuê tài chính ngắn hạn', 'Vay và nợ thuê tài chính dài hạn'])
        loan_to_assets = safe_div(loans, total_assets, 100.0) if total_assets > 0 else 0.0
        de_ratio = safe_div(borrowings, equity, 100.0) if equity > 0 else 0.0
        roe = safe_div(npat, equity, 100.0)

        metrics = [
            {"label": "Dư nợ Cho vay", "value": f"{(loans/1e9):,.1f} tỷ" if loans > 0 else f"{(rev/1e9):,.1f} tỷ", "badge": "success" if loans > 0 else "primary", "subtext": "Quy mô dư nợ cho vay tiêu dùng & cầm đồ"},
            {"label": "Nợ vay tài chính / VCSH", "value": f"{de_ratio}%", "badge": "success" if de_ratio < 250 else "warning", "subtext": "Tỷ lệ đòn bẩy tài tài trợ hoạt động cho vay"},
            {"label": "Tỷ trọng Dư nợ / Tài sản", "value": f"{loan_to_assets}%", "badge": "primary", "subtext": "Tỷ lệ tài sản sinh lời từ hoạt động cho vay"},
            {"label": "ROE TTM", "value": f"{roe}%", "badge": "success" if roe >= 15 else "primary", "subtext": "Hiệu quả sử dụng vốn chủ sở hữu"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Doanh thu cho vay & phí dịch vụ"},
            {"label": "Vốn chủ sở hữu", "value": f"{(equity/1e9):,.1f} tỷ", "desc": "Nền tảng vốn CSH đảm bảo an toàn"}
        ]
        risk_warning = "⚠️ Cần theo dõi chất lượng dư nợ cho vay tiêu dùng, chi phí vốn huy động & nợ xấu."

    # 26. ALL REMAINING / UNMATCHED SECTORS (GENERIC FALLBACK)
    else:
        payables = abs(get_bs_item(['Phải trả người bán ngắn hạn', 'Phải trả người bán', 'Nợ phải trả ngắn hạn']))
        dsi = safe_div(inventory, cogs, 365.0) if cogs > 0 and inventory > 0 else 0.0
        dso = safe_div(receivables, rev, 365.0) if rev > 0 and receivables > 0 else 0.0
        dpo = safe_div(payables, cogs, 365.0) if cogs > 0 and payables > 0 else 0.0
        ccc = max(round(dsi + dso - dpo, 1), 0.0) if (dsi > 0 or dso > 0) else 0.0

        metrics = [
            {"label": "Biên Lợi Nhuận Gộp", "value": f"{gross_margin}%", "badge": "success" if gross_margin > 18 else "primary", "subtext": "Hiệu quả kinh doanh & sức mạnh thương hiệu"},
            {"label": "Số ngày Tồn kho (DSI)", "value": f"{dsi:.0f} ngày" if dsi > 0 else "N/A", "badge": "primary", "subtext": "Vòng quay giải phóng hàng tồn kho"},
            {"label": "Chu kỳ Tiền mặt (CCC)", "value": f"{ccc:.0f} ngày" if ccc > 0 else "N/A", "badge": "success" if ccc < 60 else "warning", "subtext": "Thời gian chuyển tồn kho thành tiền mặt"},
            {"label": "Tiền mặt ròng", "value": f"{net_cash:,.1f} tỷ", "badge": "success" if net_cash > 0 else "primary", "subtext": "Dư địa tiền mặt sau khi trừ mọi nợ vay"}
        ]
        detail_metrics = [
            {"label": "Doanh thu thuần TTM", "value": f"{(rev/1e9):,.1f} tỷ", "desc": "Tổng doanh thu bán hàng 4 quý gần nhất"},
            {"label": "Tỷ lệ Nợ Vay / VCSH", "value": f"{de_ratio}%", "desc": "Tỷ lệ đòn bẩy nợ tài chính trên vốn chủ sở hữu"}
        ]
        risk_warning = "⚠️ Biến động giá nguyên vật liệu đầu vào & sức mua thị trường."

    # Import UI badge mapping
    from sector_mapping import get_ui_badge
    ui_badge = get_ui_badge(archetype)

    return {
        "sector_name": sector_name,
        "archetype": archetype,
        "badge_label": badge_label,
        "ui_badge": ui_badge.get("badge", sector_name),
        "ui_badge_code": ui_badge.get("badge_code", archetype),
        "sub_sector": ui_badge.get("sub_sector"),
        "metrics": metrics,
        "detail_metrics": detail_metrics,
        "risk_warning": risk_warning
    }
