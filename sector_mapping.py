# Dynamic Real-Time API Industry Identification Engine for HOSE / HNX / UPCoM Stocks
from market_data_provider import Listing

_API_ICB_DF = None
_API_ICB_MAP = None

def get_api_icb_df():
    global _API_ICB_DF
    if _API_ICB_DF is None:
        try:
            df = Listing(source='VCI').symbols_by_industries()
            if not df.empty and 'symbol' in df.columns:
                _API_ICB_DF = df
            else:
                _API_ICB_DF = None
        except Exception as e:
            print(f"Warning: Could not fetch API ICB DataFrame: {e}")
            _API_ICB_DF = None
    return _API_ICB_DF

def get_api_icb_map():
    global _API_ICB_MAP
    if _API_ICB_MAP is None:
        df = get_api_icb_df()
        if df is not None and not df.empty:
            icb_dict = {}
            for sym, group in df.groupby('symbol'):
                names = []
                for col in ['icb_name1', 'icb_name2', 'icb_name3', 'icb_name4', 'icb_name', 'organ_name']:
                    if col in group.columns:
                        names.extend([str(n) for n in group[col].dropna().tolist() if str(n).strip()])
                icb_dict[str(sym).upper()] = " ".join(set(names))
            _API_ICB_MAP = icb_dict
        else:
            _API_ICB_MAP = {}
    return _API_ICB_MAP

def get_dynamic_icb_peers(symbol: str, limit: int = 4) -> list:
    """
    Dynamically finds peer stocks sharing the exact ICB industry classification (Step 0)
    from Listing(source='VCI').symbols_by_industries().
    """
    df = get_api_icb_df()
    if df is None or df.empty or 'symbol' not in df.columns:
        return []
    
    sym_upper = symbol.upper().strip()
    target_row = df[df['symbol'].astype(str).str.upper() == sym_upper]
    if target_row.empty:
        return []
    
    row = target_row.iloc[0]
    peers = []
    sec_symbols = set(SECTOR_DEFINITIONS.get("SECURITIES", {}).get("symbols", []))
    is_target_securities = sym_upper in sec_symbols
    
    for col in ['icb_name4', 'icb_name3', 'icb_name2', 'icb_name1']:
        val = row.get(col)
        if val and str(val).strip():
            matched = df[(df[col] == val) & (df['symbol'].astype(str).str.upper() != sym_upper)]['symbol'].dropna().unique().tolist()
            matched_clean = []
            for m in matched:
                m_sym = str(m).upper()
                if m_sym == sym_upper:
                    continue
                if not is_target_securities and m_sym in sec_symbols:
                    continue
                matched_clean.append(m_sym)
            
            if len(matched_clean) >= 2:
                peers = matched_clean
                break
    
    return peers[:limit]

SECTOR_DEFINITIONS = {
    "STEEL": {
        "sector": "THÉP",
        "archetype": "STEEL",
        "symbols": ["HPG", "HSG", "NKG", "TLH", "SMC", "POM", "VCA", "VIS", "TNS"]
    },
    "BANKING": {
        "sector": "NGÂN HÀNG",
        "archetype": "BANKING",
        "symbols": ["VCB", "TCB", "MBB", "ACB", "VPB", "CTG", "BID", "STB", "LPB", "HDB", "VIB", "TPB", "MSB", "EIB", "OCB", "SSB", "BAB", "NAB", "SHB"]
    },
    "SECURITIES": {
        "sector": "CHỨNG KHOÁN",
        "archetype": "SECURITIES",
        "symbols": ["SSI", "VND", "VCI", "HCM", "MBS", "SHS", "FTS", "BSI", "CTS", "AGR", "VDS", "ORS", "VIX", "TCBS", "BVS", "TCI", "APG", "IVS", "PSI", "VFS", "SBS", "TVB", "HBS", "WSS", "ABW", "VIG", "CSI"]
    },
    "FINANCIAL_SERVICES": {
        "sector": "DỊCH VỤ TÀI CHÍNH",
        "archetype": "FINANCIAL_SERVICES",
        "symbols": ["F88", "EVF", "IPA", "FIT", "TVC", "TCI", "FTM"]
    },
    "INSURANCE": {
        "sector": "BẢO HIỂM",
        "archetype": "INSURANCE",
        "symbols": ["BVH", "BMI", "PVI", "MIG", "BIC", "VNR", "PGI"]
    },
    "REAL_ESTATE": {
        "sector": "BẤT ĐỘNG SẢN",
        "archetype": "REAL_ESTATE",
        "symbols": ["VHM", "NVL", "PDR", "DIG", "DXG", "KDH", "NLG", "CEO", "VRE", "TCH", "IJC", "HDC", "HDG", "NTH"]
    },
    "INDUSTRIAL_PARK": {
        "sector": "BĐS KHU CÔNG NGHIỆP",
        "archetype": "INDUSTRIAL_PARK",
        "symbols": ["BCM", "KBC", "IDC", "SZC", "PHR", "TIP", "D2D", "LHX", "SIP"]
    },
    "CONSTRUCTION": {
        "sector": "XÂY DỰNG - ĐẦU TƯ CÔNG",
        "archetype": "CONSTRUCTION",
        "symbols": ["CTD", "HBC", "HHV", "VCG", "C4G", "LCG", "DPG", "CII", "FCN"]
    },
    "BUILDING_MATERIALS": {
        "sector": "VẬT LIỆU XÂY DỰNG",
        "archetype": "BUILDING_MATERIALS",
        "symbols": ["KSB", "HT1", "BCC", "VGC", "VLB"]
    },
    "CHEMICALS_FERTILIZERS": {
        "sector": "HÓA CHẤT - PHÂN BÓN",
        "archetype": "CHEMICALS_FERTILIZERS",
        "symbols": ["DGC", "DCM", "DPM", "CSV", "BFC", "DDV", "BTE"]
    },
    "RUBBER": {
        "sector": "CAO SU",
        "archetype": "RUBBER",
        "symbols": ["GVR", "DPR", "DRI", "TRC"]
    },
    "OIL_GAS": {
        "sector": "DẦU KHÍ",
        "archetype": "OIL_GAS",
        "symbols": ["PVD", "PVS", "BSR", "PLX", "OIL", "PVC", "PVT"]
    },
    "POWER_ENERGY": {
        "sector": "ĐIỆN - NĂNG LƯỢNG",
        "archetype": "POWER_ENERGY",
        "symbols": ["POW", "GAS", "NT2", "QTP", "HND", "REE", "GEG", "PC1", "BWE", "TDM"]
    },
    "MINING": {
        "sector": "KHOÁNG SẢN",
        "archetype": "MINING",
        "symbols": ["MSR", "NBC", "THT", "TVD"]
    },
    "AUTOMOTIVE": {
        "sector": "Ô TÔ - PHỤ TÙNG",
        "archetype": "AUTOMOTIVE",
        "symbols": ["HAX", "DRC", "CSM", "HHS"]
    },
    "TEXTILE": {
        "sector": "DỆT MAY",
        "archetype": "TEXTILE",
        "symbols": ["MSH", "TNG", "VGT", "STK", "GIL", "MPT"]
    },
    "SEAFOOD": {
        "sector": "THỦY SẢN",
        "archetype": "SEAFOOD",
        "symbols": ["VHC", "ANV", "IDI", "FMC", "CMX"]
    },
    "FOOD_BEVERAGE": {
        "sector": "THỰC PHẨM & ĐỒ UỐNG",
        "archetype": "FOOD_BEVERAGE",
        "symbols": ["MSN", "VNM", "SAB", "BHN", "KDC", "DBC", "MCM"]
    },
    "RETAIL": {
        "sector": "BÁN LẺ",
        "archetype": "RETAIL",
        "symbols": ["MWG", "FRT", "DGW", "PET", "PNJ", "MCH", "AST", "SVN", "COM", "CMC", "SAB"]
    },
    "PHARMA_HEALTHCARE": {
        "sector": "DƯỢC - Y TẾ",
        "archetype": "PHARMA_HEALTHCARE",
        "symbols": ["DHG", "IMP", "DBD", "TRA", "DVN"]
    },
    "TECH_TELECOM": {
        "sector": "CÔNG NGHỆ - TRUYỀN THÔNG",
        "archetype": "TECH_TELECOM",
        "symbols": ["FPT", "CMG", "ELC", "ITD", "FOX", "VGI", "CTR"]
    },
    "AVIATION_TOURISM": {
        "sector": "HÀNG KHÔNG - DU LỊCH",
        "archetype": "AVIATION_TOURISM",
        "symbols": ["HVN", "VJC", "ACV", "SKG", "VTD"]
    },
    "PORTS_LOGISTICS": {
        "sector": "CẢNG BIỂN - VẬN TẢI",
        "archetype": "PORTS_LOGISTICS",
        "symbols": ["GMD", "HAH", "VSC", "VTO", "VIP", "SGP"]
    },
    "WATER_PLASTICS": {
        "sector": "NƯỚC - NHỰA",
        "archetype": "WATER_PLASTICS",
        "symbols": ["NTP", "BMP", "AAA", "APH"]
    },
    "SUGAR_WOOD_PAPER": {
        "sector": "ĐƯỜNG - GỖ - GIẤY",
        "archetype": "SUGAR_WOOD_PAPER",
        "symbols": ["SBT", "QNS", "LSS", "PTB", "TTF", "DHC"]
    }
}

SECTOR_MAP = {}
for code, data in SECTOR_DEFINITIONS.items():
    for s in data["symbols"]:
        SECTOR_MAP[s.upper()] = {
            "sector": data["sector"],
            "archetype": data["archetype"]
        }


def get_sector_info(symbol: str, comp_overview: dict = None, get_bs_item = None) -> dict:
    symbol = symbol.upper().strip()

    # 1. Primary check: Exact mapping in 24-Sector Dictionary (High accuracy for main listed stocks)
    if symbol in SECTOR_MAP:
        return SECTOR_MAP[symbol]

    # 2. Dynamic ICB classification from the direct public listing adapter.
    icb_api_map = get_api_icb_map()
    icb_text = icb_api_map.get(symbol, "")

    overview_text = ""
    if comp_overview and isinstance(comp_overview, dict):
        organ_name = str(comp_overview.get("organ_name") or "")
        icb_name = str(comp_overview.get("industry") or comp_overview.get("icb_name") or comp_overview.get("sector") or "")
        profile = str(comp_overview.get("company_profile") or "")
        overview_text = f"{organ_name} {icb_name} {profile}"

    combined_text = f"{icb_text} {overview_text}".lower()

    # 3. Dynamic API Text Keyword Matcher
    if any(k in combined_text for k in ["thép và sản phẩm thép", "thép", "steel", "tôn phôi", "phôi thép", "xản xuất thép"]):
        return {"sector": "THÉP", "archetype": "STEEL"}

    if any(k in combined_text for k in ["môi giới chứng khoán", "công ty chứng khoán", "hoạt động chứng khoán"]):
        return {"sector": "CHỨNG KHOÁN", "archetype": "SECURITIES"}

    if any(k in combined_text for k in ["dịch vụ tài chính", "tài chính tiêu dùng", "cầm đồ", "cho vay tiêu dùng", "đầu tư tài chính", "financial services", "consumer finance"]):
        return {"sector": "DỊCH VỤ TÀI CHÍNH", "archetype": "FINANCIAL_SERVICES"}

    if any(k in combined_text for k in ["khu công nghiệp", "kcn", "bất động sản khu công nghiệp", "hạ tầng khu công nghiệp"]):
        return {"sector": "BĐS KHU CÔNG NGHIỆP", "archetype": "INDUSTRIAL_PARK"}

    if any(k in combined_text for k in ["bất động sản", "real estate", "phát triển nhà", "kinh doanh nhà"]):
        return {"sector": "BẤT ĐỘNG SẢN", "archetype": "REAL_ESTATE"}

    if any(k in combined_text for k in ["ngân hàng", "banking"]):
        return {"sector": "NGÂN HÀNG", "archetype": "BANKING"}

    if any(k in combined_text for k in ["bảo hiểm", "insurance"]):
        return {"sector": "BẢO HIỂM", "archetype": "INSURANCE"}

    if any(k in combined_text for k in ["hóa chất", "phân bón", "nông dược", "chemicals", "phốt pho"]):
        return {"sector": "HÓA CHẤT - PHÂN BÓN", "archetype": "CHEMICALS_FERTILIZERS"}

    if any(k in combined_text for k in ["thủy sản", "tôm", "cá tra", "seafood"]):
        return {"sector": "THỦY SẢN", "archetype": "SEAFOOD"}

    if any(k in combined_text for k in ["dệt may", "may mặc", "sợi", "garment", "textile"]):
        return {"sector": "DỆT MAY", "archetype": "TEXTILE"}

    if any(k in combined_text for k in ["bán lẻ", "retail", "siêu thị", "chuỗi cửa hàng", "vàng bạc", "trang sức", "đá quý", "kim hoàn", "vàng miếng", "cửa hàng trang sức", "personal & household goods", "hàng cá nhân"]):
        return {"sector": "BÁN LẺ", "archetype": "RETAIL"}

    if any(k in combined_text for k in ["cấp nước", "thoát nước", "phân phối nước", "sản xuất nhựa", "plastics"]):
        return {"sector": "NƯỚC - NHỰA", "archetype": "WATER_PLASTICS"}

    if any(k in combined_text for k in ["điện lực", "phát điện", "thủy điện", "nhiệt điện", "năng lượng tái tạo"]):
        return {"sector": "ĐIỆN - NĂNG LƯỢNG", "archetype": "POWER_ENERGY"}

    if any(k in combined_text for k in ["dầu khí", "khoan dầu khí", "thiết bị dầu khí", "dịch vụ dầu khí", "petro"]):
        return {"sector": "DẦU KHÍ", "archetype": "OIL_GAS"}

    if any(k in combined_text for k in ["khoáng sản", "quặng", "mining", "than đá"]):
        return {"sector": "KHOÁNG SẢN", "archetype": "MINING"}

    if any(k in combined_text for k in ["ô tô", "phụ tùng ô tô", "lốp", "auto"]):
        return {"sector": "Ô TÔ - PHỤ TÙNG", "archetype": "AUTOMOTIVE"}

    if any(k in combined_text for k in ["dược", "y tế", "bệnh viện", "pharma"]):
        return {"sector": "DƯỢC - Y TẾ", "archetype": "PHARMA_HEALTHCARE"}

    if any(k in combined_text for k in ["công nghệ thông tin", "phần mềm", "viễn thông", "technology"]):
        return {"sector": "CÔNG NGHỆ - TRUYỀN THÔNG", "archetype": "TECH_TELECOM"}

    if any(k in combined_text for k in ["hàng không", "du lịch", "khách sạn", "aviation"]):
        return {"sector": "HÀNG KHÔNG - DU LỊCH", "archetype": "AVIATION_TOURISM"}

    if any(k in combined_text for k in ["cảng biển", "vận tải biển", "logistics", "kho bãi"]):
        return {"sector": "CẢNG BIỂN - VẬN TẢI", "archetype": "PORTS_LOGISTICS"}

    if any(k in combined_text for k in ["vật liệu xây dựng", "xi măng", "đá xây dựng", "gạch"]):
        return {"sector": "VẬT LIỆU XÂY DỰNG", "archetype": "BUILDING_MATERIALS"}

    if any(k in combined_text for k in ["xây dựng", "xây lắp", "đầu tư công", "construction"]):
        return {"sector": "XÂY DỰNG - ĐẦU TƯ CÔNG", "archetype": "CONSTRUCTION"}

    if any(k in combined_text for k in ["mía đường", "chế biến gỗ", "sản xuất giấy", "sugar", "paper", "wood"]):
        return {"sector": "ĐƯỜNG - GỖ - GIẤY", "archetype": "SUGAR_WOOD_PAPER"}

    if any(k in combined_text for k in ["thực phẩm", "đồ uống", "sữa", "bánh kẹo", "nước giải khát"]):
        return {"sector": "THỰC PHẨM & ĐỒ UỐNG", "archetype": "FOOD_BEVERAGE"}

    if any(k in combined_text for k in ["cao su", "rubber"]):
        return {"sector": "CAO SU", "archetype": "RUBBER"}

    # 4. BALANCE SHEET FINANCIAL SIGNATURE FALLBACK
    if get_bs_item and callable(get_bs_item):
        margin_loans = get_bs_item(['Các khoản cho vay', 'Phải thu về cho vay ngắn hạn'])
        fvtpl_assets = get_bs_item(['Các tài sản tài chính ghi nhận thông qua lãi lỗ (FVTPL)'])
        deposits = get_bs_item(['Tiền gửi của khách hàng', 'Tiền gửi khách hàng'])

        if margin_loans > 0 or fvtpl_assets > 0:
            return {"sector": "CHỨNG KHOÁN", "archetype": "SECURITIES"}
        if deposits > 0:
            return {"sector": "NGÂN HÀNG", "archetype": "BANKING"}

    # 5. Default fallback
    return {"sector": "SẢN XUẤT CÔNG NGHIỆP", "archetype": "MANUFACTURING_GENERAL"}


def get_ui_badge(archetype: str) -> dict:
    """Return the canonical heatmap sector without a second UI grouping layer."""
    from industry_indicator_profiles import get_industry_profile

    profile = get_industry_profile(archetype)
    return {
        "badge": profile["name"],
        "badge_code": profile["archetype"],
        "sub_sector": None,
    }
