# forensic_analysis_engine.py
# Forensic Red-Flag Engine for Vietnam Stock Market
# Deterministic Python logic for detecting 7 financial report red flags.

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional

def safe_float(val, default: float = 0.0) -> float:
    if val is None or pd.isna(val):
        return default
    try:
        f = float(val)
        return f if not np.isnan(f) else default
    except (ValueError, TypeError):
        return default

def get_row_value(df: pd.DataFrame, item_names: List[str], col: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    for name in item_names:
        row = df[df['item'].astype(str).str.strip().str.lower() == name.strip().lower()]
        if not row.empty:
            val = safe_float(row[col].values[0])
            if val != 0.0:
                return val
    return 0.0

def is_financial_sector(badge: str) -> bool:
    b = str(badge).upper()
    financial_keywords = ["NGÂN HÀNG", "CHỨNG KHOÁN", "BẢO HIỂM", "BANK", "SECURITIES", "INSURANCE", "FINANCIAL"]
    return any(k in b for k in financial_keywords)

def extract_financial_series(bs_df: pd.DataFrame, is_df: pd.DataFrame, cf_df: pd.DataFrame, r_df: pd.DataFrame):
    """
    Extracts ordered quarterly time-series (up to 8 quarters, sorted from oldest to newest).
    Returns list of dicts with key metrics per quarter.
    """
    data_cols_bs = [c for c in bs_df.columns if c not in ['item', 'item_en', 'item_id']] if not bs_df.empty else []
    data_cols_is = [c for c in is_df.columns if c not in ['item', 'item_en', 'item_id']] if not is_df.empty else []
    data_cols_cf = [c for c in cf_df.columns if c not in ['item', 'item_en', 'item_id']] if not cf_df.empty else []

    all_cols = []
    for c in data_cols_bs:
        if c in data_cols_is or not data_cols_is:
            all_cols.append(c)

    if not all_cols and data_cols_is:
        all_cols = data_cols_is
    if not all_cols and data_cols_bs:
        all_cols = data_cols_bs

    recent_cols = all_cols[:8]
    recent_cols_chronological = list(reversed(recent_cols))

    quarters_data = []

    rev_items = ['Doanh thu thuần về bán hàng và cung cấp dịch vụ', 'Doanh thu thuần', 'Doanh thu hoạt động', 'Thu nhập lãi thuần']
    cogs_items = ['Giá vốn hàng bán', 'Giá vốn']
    gp_items = ['Lợi nhuận gộp về bán hàng và cung cấp dịch vụ', 'Lợi nhuận gộp']
    npat_items = ['Lợi nhuận sau thuế công ty mẹ', 'Lợi nhuận sau thuế của cổ đông công ty mẹ', 'Lợi nhuận sau thuế thu nhập doanh nghiệp', 'LNST', 'Lợi nhuận sau thuế']
    oth_inc_items = ['Thu nhập khác']
    oth_exp_items = ['Chi phí khác']
    oth_profit_items = ['Lợi nhuận khác']

    rec_items = ['Tài sản ngắn hạn - Phải thu', 'Các khoản phải thu ngắn hạn', 'Phải thu ngắn hạn của khách hàng', 'Các khoản phải thu']
    inv_items = ['Hàng tồn kho', 'Hàng tồn kho, ròng']
    debt_items = ['Nợ phải trả', 'TỔNG CỘNG NỢ PHẢI TRẢ', 'Vay và nợ thuê tài chính ngắn hạn', 'Các khoản nợ vay']
    st_debt_items = ['Vay và nợ thuê tài chính ngắn hạn', 'Vay ngắn hạn']
    lt_debt_items = ['Vay và nợ thuê tài chính dài hạn', 'Vay dài hạn']
    equity_items = ['VỐN CHỦ SỞ HỮU', 'Vốn chủ sở hữu', 'Vốn góp của chủ sở hữu']
    asset_items = ['TỔNG CỘNG TÀI SẢN', 'Tổng tài sản']

    cfo_items = ['Lưu chuyển tiền thuần từ hoạt động kinh doanh', 'Dòng tiền thuần từ hoạt động kinh doanh', 'Lưu chuyển tiền thuần từ (dùng vào) hoạt động kinh doanh']

    for q in recent_cols_chronological:
        rev = get_row_value(is_df, rev_items, q)
        cogs = get_row_value(is_df, cogs_items, q)
        gp = get_row_value(is_df, gp_items, q)
        npat = get_row_value(is_df, npat_items, q)
        oth_inc = get_row_value(is_df, oth_inc_items, q)
        oth_exp = get_row_value(is_df, oth_exp_items, q)
        oth_profit = get_row_value(is_df, oth_profit_items, q)

        rec = get_row_value(bs_df, rec_items, q)
        inv = get_row_value(bs_df, inv_items, q)
        equity = get_row_value(bs_df, equity_items, q)
        assets = get_row_value(bs_df, asset_items, q)

        debt = get_row_value(bs_df, debt_items, q)
        if debt == 0.0:
            debt = get_row_value(bs_df, st_debt_items, q) + get_row_value(bs_df, lt_debt_items, q)

        cfo = get_row_value(cf_df, cfo_items, q) if not cf_df.empty else 0.0

        gross_margin = (gp / rev * 100.0) if rev > 0 else 0.0
        net_margin = (npat / rev * 100.0) if rev > 0 else 0.0
        roe = (npat / equity * 100.0) if equity > 0 else 0.0
        debt_to_equity = (debt / equity) if equity > 0 else 0.0

        quarters_data.append({
            "period": q,
            "revenue": rev,
            "cogs": cogs,
            "gross_profit": gp,
            "npat": npat,
            "other_income": oth_inc,
            "other_expenses": oth_exp,
            "other_profit": oth_profit,
            "receivables": rec,
            "inventory": inv,
            "equity": equity,
            "assets": assets,
            "debt": debt,
            "cfo": cfo,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "roe": roe,
            "debt_to_equity": debt_to_equity
        })

    return quarters_data


def run_forensic_analysis(
    symbol: str,
    badge: str,
    bs_df: pd.DataFrame,
    is_df: pd.DataFrame,
    cf_df: pd.DataFrame,
    r_df: Optional[pd.DataFrame] = None
) -> Dict[str, Any]:
    """
    Executes the 7 Forensic Red-Flag checks in Python.
    Returns JSON dictionary structure for forensic_analysis.
    """
    symbol = symbol.upper().strip()
    is_financial = is_financial_sector(badge)

    r_df_clean = r_df if (r_df is not None and not r_df.empty) else pd.DataFrame()
    quarters = extract_financial_series(bs_df, is_df, cf_df, r_df_clean)

    flags = []

    if len(quarters) >= 4:
        recent_4 = quarters[-4:]  # Most recent 4 quarters

        # ==========================================
        # CỜ 1 — Lệch pha Lợi nhuận & Dòng tiền
        # ==========================================
        cfo_violation_count = 0
        cfo_lnst_ratios = []

        for q in recent_4:
            npat = q["npat"]
            cfo = q["cfo"]
            if npat > 0:
                ratio = cfo / npat
                cfo_lnst_ratios.append(ratio)
                if ratio < 0.5 or cfo < 0:
                    cfo_violation_count += 1

        if cfo_violation_count >= 2 and cfo_lnst_ratios:
            avg_ratio = float(np.mean(cfo_lnst_ratios))
            flags.append({
                "flag": "loi_nhuan_khong_co_tien_that",
                "ten_hien_thi": "Lợi nhuận không có tiền mặt thật",
                "severity": "cao",
                "so_quy_vi_pham": cfo_violation_count,
                "cfo_lnst_trung_binh": round(avg_ratio, 2),
                "so_lieu_cu_the": f"CFO/LNST < 0.5 hoặc âm ở {cfo_violation_count}/4 quý gần nhất (trung bình {avg_ratio:.2f}x)",
                "giai_thich": "Lợi nhuận ghi nhận trên sổ sách nhưng dòng tiền thuần từ hoạt động kinh doanh (CFO) ở mức thấp hoặc âm, cho thấy chất lượng lợi nhuận chưa đi kèm tiền mặt thực tế.",
                "kha_nang_nguyen_nhan": "Đáng lo ngại",
                "ly_do_nhan_dinh": "Tiền mặt bị đọng ở khoản phải thu hoặc tồn kho, có nguy cơ gia tăng rủi ro thanh khoản."
            })

        # ==========================================
        # CỜ 2 — Phải thu tăng nhanh hơn Doanh thu
        # ==========================================
        if len(quarters) >= 8:
            rec_yoy_violations = 0
            rev_neg_rec_pos_count = 0
            diff_ratios = []

            for i in range(4):
                q_curr = quarters[i + 4]
                q_prev = quarters[i]

                rev_curr, rev_prev = q_curr["revenue"], q_prev["revenue"]
                rec_curr, rec_prev = q_curr["receivables"], q_prev["receivables"]

                if rev_prev > 0 and rec_prev > 0:
                    g_rev = (rev_curr - rev_prev) / rev_prev
                    g_rec = (rec_curr - rec_prev) / rec_prev

                    if g_rev > 0 and g_rec > 1.5 * g_rev:
                        rec_yoy_violations += 1
                        diff_ratios.append(g_rec / g_rev if g_rev > 0 else 1.5)
                    elif g_rev < 0 and g_rec > 0:
                        rec_yoy_violations += 1
                        rev_neg_rec_pos_count += 1
                        diff_ratios.append(2.0)

            if rec_yoy_violations >= 2:
                max_diff = round(float(np.max(diff_ratios)), 1) if diff_ratios else 1.5
                severity = "cao" if rev_neg_rec_pos_count > 0 else "trung_binh_cao"
                flags.append({
                    "flag": "phai_thu_bat_thuong",
                    "ten_hien_thi": "Khoản phải thu tăng nhanh hơn doanh thu",
                    "severity": severity,
                    "chenh_lech_lan": max_diff,
                    "so_lieu_cu_the": f"Phải thu tăng trưởng nhanh gấp {max_diff}x tốc độ tăng doanh thu ở {rec_yoy_violations}/4 quý gần nhất",
                    "giai_thich": "Khoản phải thu ngắn hạn tăng trưởng quá nhanh so với doanh thu, phản ánh doanh nghiệp có thể đang nới lỏng chính sách tín dụng bán hàng.",
                    "kha_nang_nguyen_nhan": "Đáng lo ngại",
                    "ly_do_nhan_dinh": "Rủi ro nợ xấu và bị chiếm dụng vốn gia tăng từ phía khách hàng và đối tác."
                })

        # ==========================================
        # CỜ 3 — Hàng tồn kho phình bất thường (Bỏ qua cho Tài chính)
        # ==========================================
        if not is_financial and len(quarters) >= 8:
            inv_violations = 0
            inv_diff_ratios = []

            for i in range(4):
                q_curr = quarters[i + 4]
                q_prev = quarters[i]

                cogs_curr, cogs_prev = q_curr["cogs"], q_prev["cogs"]
                inv_curr, inv_prev = q_curr["inventory"], q_prev["inventory"]

                if cogs_prev > 0 and inv_prev > 0:
                    g_cogs = (cogs_curr - cogs_prev) / cogs_prev
                    g_inv = (inv_curr - inv_prev) / inv_prev

                    if g_cogs > 0 and g_inv > 1.5 * g_cogs:
                        inv_violations += 1
                        inv_diff_ratios.append(g_inv / g_cogs if g_cogs > 0 else 1.5)
                    elif g_cogs < 0 and g_inv > 0:
                        inv_violations += 1
                        inv_diff_ratios.append(2.0)

            if inv_violations >= 2:
                max_inv_diff = round(float(np.max(inv_diff_ratios)), 1) if inv_diff_ratios else 1.5
                flags.append({
                    "flag": "hang_ton_kho_phinh_bat_thuong",
                    "ten_hien_thi": "Hàng tồn kho phình to bất thường",
                    "severity": "trung_binh",
                    "chenh_lech_lan": max_inv_diff,
                    "so_lieu_cu_the": f"Tồn kho tăng trưởng vượt {max_inv_diff}x tốc độ tăng giá vốn ở {inv_violations}/4 quý gần nhất",
                    "giai_thich": "Tốc độ phình to của hàng tồn kho vượt xa tốc độ tăng giá vốn hàng bán, dấu hiệu hàng hóa tiêu thụ chậm hoặc đọng vốn khâu sản xuất.",
                    "kha_nang_nguyen_nhan": "Có thể chính đáng",
                    "ly_do_nhan_dinh": "Doanh nghiệp có thể đang chủ động tích trữ nguyên vật liệu giá rẻ hoặc chuẩn bị hàng cho mùa cao điểm tiêu thụ."
                })

        # ==========================================
        # CỜ 4 — Đòn bẩy tăng nhưng hiệu quả không cải thiện (Bỏ qua cho Tài chính)
        # ==========================================
        if not is_financial:
            de_increasing_quarters = 0
            de_values = [q["debt_to_equity"] for q in recent_4]
            roe_values = [q["roe"] for q in recent_4]
            net_margin_values = [q["net_margin"] for q in recent_4]

            for i in range(len(de_values) - 1):
                if de_values[i + 1] > de_values[i] and de_values[i] > 0:
                    de_increasing_quarters += 1

            roe_delta = roe_values[-1] - roe_values[0] if roe_values else 0.0
            margin_delta = net_margin_values[-1] - net_margin_values[0] if net_margin_values else 0.0

            if de_increasing_quarters >= 2 and (roe_delta <= 2.0 or margin_delta <= 2.0) and de_values[-1] > 0.8:
                flags.append({
                    "flag": "don_bay_tang_hieu_qua_kem",
                    "ten_hien_thi": "Đòn bẩy tài chính tăng nhưng hiệu quả không cải thiện",
                    "severity": "trung_binh_cao",
                    "so_lieu_cu_the": f"Nợ/VCSH tăng từ {de_values[0]:.2f}x lên {de_values[-1]:.2f}x trong 4 quý nhưng ROE biến động {roe_delta:+.1f}%",
                    "giai_thich": "Doanh nghiệp gia tăng nợ vay làm phình đòn bẩy tài chính nhưng hiệu quả sinh lời (ROE, Biên ròng) không cải thiện tương ứng.",
                    "kha_nang_nguyen_nhan": "Đáng lo ngại",
                    "ly_do_nhan_dinh": "Gánh nặng chi phí lãi vay tăng lên có thể bào mòn lợi nhuận nếu dự án từ vốn vay chưa phát huy hiệu quả."
                })

        # ==========================================
        # CỜ 5 — Biên lợi nhuận biến động đột ngột
        # ==========================================
        curr_q = recent_4[-1]
        curr_gm = curr_q["gross_margin"]

        prev_gms = [q["gross_margin"] for q in quarters[:-1] if q["gross_margin"] > 0]
        if prev_gms and curr_gm > 0:
            avg_gm = float(np.mean(prev_gms))
            if avg_gm > 0:
                gm_deviation = abs(curr_gm - avg_gm) / avg_gm
                if gm_deviation > 0.30:
                    flags.append({
                        "flag": "bien_loi_nhuan_bien_dong_dot_ngot",
                        "ten_hien_thi": "Biên lợi nhuận biến động đột ngột",
                        "severity": "theo_doi",
                        "so_lieu_cu_the": f"Biên LN gộp quý gần nhất ({curr_gm:.1f}%) lệch {gm_deviation*100:.1f}% so với trung bình các quý trước ({avg_gm:.1f}%)",
                        "giai_thich": "Biên lợi nhuận gộp quý hiện tại chênh lệch mạnh >30% so với trung bình các quý trước, phản ánh biến động lớn trong cấu trúc giá vốn hoặc giá bán.",
                        "kha_nang_nguyen_nhan": "Có thể chính đáng",
                        "ly_do_nhan_dinh": "Cần theo dõi thêm tính thời vụ ngành hoặc sự thay đổi của giá nguyên vật liệu đầu vào."
                    })

        # ==========================================
        # CỜ 6 — Thu nhập/Chi phí khác bất thường lớn
        # ==========================================
        npat_curr = curr_q["npat"]
        oth_inc = curr_q["other_income"]
        oth_exp = curr_q["other_expenses"]
        oth_prof = curr_q["other_profit"]

        net_oth = abs(oth_prof) if oth_prof != 0.0 else abs(oth_inc - oth_exp)

        if npat_curr > 0 and net_oth > 0:
            oth_ratio = net_oth / npat_curr
            if oth_ratio > 0.20:
                flags.append({
                    "flag": "thu_nhap_chi_phi_khac_bat_thuong",
                    "ten_hien_thi": "Thu nhập/Chi phí khác chiếm tỷ trọng lớn bất thường",
                    "severity": "cao",
                    "so_lieu_cu_the": f"Thu nhập/Chi phí khác ròng chiếm {oth_ratio*100:.1f}% tổng LNST quý gần nhất",
                    "giai_thich": "Thu nhập hoặc chi phí khác chiếm tỷ trọng >20% trong LNST quý, dấu hiệu lợi nhuận phụ thuộc nhiều vào các khoản đột biến ngoài hoạt động cốt lõi.",
                    "kha_nang_nguyen_nhan": "Đáng lo ngại",
                    "ly_do_nhan_dinh": "Lợi nhuận từ thanh lý tài sản hoặc khoản thu đột biến mang tính nhất thời, khó duy trì bền vững."
                })

        # ==========================================
        # CỜ 7 — Pha loãng cổ phiếu bất thường
        # ==========================================
        if not r_df.empty:
            try:
                shares_col = [c for c in r_df.columns if c not in ['item', 'item_en', 'item_id']]
                if len(shares_col) >= 4:
                    shares_row = r_df[r_df['item'].astype(str).str.contains('Số CP|cổ phiếu|Shares', case=False, na=False)]
                    eps_row = r_df[r_df['item'].astype(str).str.contains('EPS|Thu nhập trên mỗi cổ phiếu', case=False, na=False)]

                    if not shares_row.empty and not eps_row.empty:
                        s_curr = safe_float(shares_row[shares_col[0]].values[0])
                        s_prev = safe_float(shares_row[shares_col[-1]].values[0])
                        eps_curr = safe_float(eps_row[shares_col[0]].values[0])
                        eps_prev = safe_float(eps_row[shares_col[-1]].values[0])

                        if s_prev > 0 and s_curr > 1.10 * s_prev:
                            s_growth = (s_curr - s_prev) / s_prev * 100.0
                            eps_growth = ((eps_curr - eps_prev) / eps_prev * 100.0) if eps_prev > 0 else -1.0
                            if eps_growth <= 2.0:
                                flags.append({
                                    "flag": "pha_loang_co_phieu_bat_thuong",
                                    "ten_hien_thi": "Pha loãng cổ phiếu làm giảm hiệu quả trên mỗi cổ phần",
                                    "severity": "trung_binh",
                                    "so_lieu_cu_the": f"Số cổ phiếu lưu hành tăng {s_growth:.1f}% trong 4 quý qua nhưng EPS biến động {eps_growth:+.1f}%",
                                    "giai_thich": "Số lượng cổ phiếu lưu hành tăng nhanh >10% do phát hành thêm nhưng EPS tăng chậm hoặc giảm, làm suy giảm lợi ích cổ đông.",
                                    "kha_nang_nguyen_nhan": "Đáng lo ngại",
                                    "ly_do_nhan_dinh": "Tăng vốn chưa mang lại hiệu quả kinh doanh tương ứng."
                                })
            except Exception as e:
                print(f"Warning checking flag 7: {e}")

    # Aggregated Risk Level determination
    if not flags:
        overall_risk = "Sạch"
    elif all(f["severity"] == "theo_doi" for f in flags):
        overall_risk = "Cần theo dõi"
    elif any(f["severity"] == "cao" for f in flags) or len(flags) >= 3:
        overall_risk = "Nghiêm trọng"
    else:
        overall_risk = "Cảnh báo"

    return {
        "title": "Soi Báo Cáo Tài Chính AI",
        "muc_do_rui_ro_tong_the": overall_risk,
        "so_co_do_kich_hoat": len(flags),
        "chi_tiet_co_do": flags
    }
