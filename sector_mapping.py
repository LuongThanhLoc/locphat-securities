# Dynamic Real-Time API Industry Identification Engine for HOSE / HNX / UPCoM Stocks
#
# SECTOR STRUCTURE (post 2026-08-07):
# - SECTOR_DEFINITIONS: 25 archetypes (24 sieucophieu sectors + 1 fallback bucket).
#   Display names match sieucophieu.vn/bang-dien exactly.
# - SIEUCOPHIEU_MULTIMAP: 368 symbols -> list of sector display names.
#   Supports multi-membership (a stock can appear in 2+ sectors simultaneously).
# - SECTOR_MAP: backward-compat single-membership lookup; uses the first
#   listed sector in SIEUCOPHIEU_MULTIMAP as the "primary" sector.
# - get_sector_info(symbol): returns the primary membership (single dict).
# - get_sector_memberships(symbol): returns the full list of memberships.
#
# Source: danh_sach_co_phieu_theo_sector_sieucophieu.txt (snapshot 28/07/2026).

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
    sec_symbols = set(SIEUCOPHIEU_MULTIMAP.get(sym_upper, []) + ["CHỨNG KHOÁN"])
    is_target_securities = "CHỨNG KHOÁN" in SIEUCOPHIEU_MULTIMAP.get(sym_upper, [])

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


# 24 sector definitions + 1 fallback bucket. Display names match sieucophieu.vn/bang-dien exactly.
SECTOR_DEFINITIONS = {
    "STEEL":                 {"sector": "Thép",                       "archetype": "STEEL"},
    "BANKING":               {"sector": "Ngân hàng",                  "archetype": "BANKING"},
    "SECURITIES":            {"sector": "Chứng khoán",                "archetype": "SECURITIES"},
    "FINANCIAL_SERVICES":    {"sector": "Dịch vụ tài chính",          "archetype": "FINANCIAL_SERVICES"},
    "INSURANCE":             {"sector": "Bảo hiểm",                   "archetype": "INSURANCE"},
    "REAL_ESTATE":           {"sector": "Bất động sản",               "archetype": "REAL_ESTATE"},
    "INDUSTRIAL_PARK":       {"sector": "BĐS Khu công nghiệp",        "archetype": "INDUSTRIAL_PARK"},
    "CONSTRUCTION":          {"sector": "Đầu tư công",                "archetype": "CONSTRUCTION"},
    "BUILDING_MATERIALS":    {"sector": "VLXD",                       "archetype": "BUILDING_MATERIALS"},
    "CHEMICALS_FERTILIZERS": {"sector": "Hóa chất - Phân bón",       "archetype": "CHEMICALS_FERTILIZERS"},
    "RUBBER":                {"sector": "Cao su",                     "archetype": "RUBBER"},
    "OIL_GAS":               {"sector": "Dầu khí",                    "archetype": "OIL_GAS"},
    "POWER_ENERGY":          {"sector": "Điện - Năng lượng",          "archetype": "POWER_ENERGY"},
    "MINING":                {"sector": "Khoáng sản",                 "archetype": "MINING"},
    "AUTOMOTIVE":            {"sector": "Ô tô - Phụ tùng",            "archetype": "AUTOMOTIVE"},
    "TEXTILE":               {"sector": "Dệt may",                    "archetype": "TEXTILE"},
    "SEAFOOD":               {"sector": "Thủy sản",                   "archetype": "SEAFOOD"},
    "FOOD_BEVERAGE":         {"sector": "Thực phẩm",                  "archetype": "FOOD_BEVERAGE"},
    "RETAIL":                {"sector": "Bán lẻ",                     "archetype": "RETAIL"},
    "PHARMA_HEALTHCARE":     {"sector": "Dược - Y tế",                "archetype": "PHARMA_HEALTHCARE"},
    "TECH_TELECOM":          {"sector": "Công nghệ - Truyền thông",   "archetype": "TECH_TELECOM"},
    "AVIATION_TOURISM":      {"sector": "Hàng không - Du lịch",       "archetype": "AVIATION_TOURISM"},
    "PORTS_LOGISTICS":       {"sector": "Cảng biển - Vận tải",        "archetype": "PORTS_LOGISTICS"},
    "WATER_PLASTICS":        {"sector": "Nước - Nhựa",                "archetype": "WATER_PLASTICS"},
    "SUGAR_WOOD_PAPER":      {"sector": "Đường - Gỗ - Giấy",          "archetype": "SUGAR_WOOD_PAPER"},
    # Legacy bucket — sieucophieu.vn does not have a separate "Dịch vụ tài chính"
    # sector; consumer-finance companies (F88, EVF, etc.) don't appear in the
    # 28/07/2026 snapshot. Kept as a fallback target for the keyword matcher.
    "FINANCIAL_SERVICES":    {"sector": "Dịch vụ tài chính",          "archetype": "FINANCIAL_SERVICES"},
    # Fallback bucket for stocks not in the sieucophieu snapshot (UPCoM, halted, new IPOs).
    "MANUFACTURING_GENERAL": {"sector": "Sản xuất công nghiệp",       "archetype": "MANUFACTURING_GENERAL"},
}

# Reverse lookup: display_name -> archetype code.
DISPLAY_NAME_TO_ARCHETYPE = {v["sector"]: k for k, v in SECTOR_DEFINITIONS.items()}

# Backward-compat sector symbols list (single-membership view) derived from the multimap
# for callers that still iterate SECTOR_DEFINITIONS[*]["symbols"]. Order in each list is the
# order of the first appearance in SIEUCOPHIEU_MULTIMAP iteration.
SECTOR_SYMBOLS = {arch: [] for arch in SECTOR_DEFINITIONS.keys()}


# Sieucophieu symbol -> list of sector display names (multi-membership).
# Authoritative snapshot from danh_sach_co_phieu_theo_sector_sieucophieu.txt (28/07/2026).
SIEUCOPHIEU_MULTIMAP = {
    'AAA': ['Nước - Nhựa'],
    'AAM': ['Thủy sản'],
    'AAS': ['Chứng khoán'],
    'ABB': ['Ngân hàng'],
    'ABI': ['Bảo hiểm'],
    'ACB': ['Ngân hàng'],
    'ACC': ['VLXD'],
    'ACL': ['Thủy sản'],
    'ACV': ['Hàng không - Du lịch'],
    'ADS': ['Dệt may'],
    'AFX': ['Thực phẩm'],
    'AGG': ['Bất động sản'],
    'AGR': ['Chứng khoán'],
    'AIG': ['Thực phẩm'],
    'AMS': ['VLXD'],
    'ANV': ['Thủy sản'],
    'APF': ['Thực phẩm'],
    'APG': ['Chứng khoán'],
    'APH': ['Nước - Nhựa'],
    'APS': ['Chứng khoán'],
    'ASM': ['Thủy sản'],
    'AST': ['Hàng không - Du lịch'],
    'BAB': ['Ngân hàng'],
    'BAF': ['Thực phẩm'],
    'BCC': ['VLXD', 'Đầu tư công'],
    'BCE': ['VLXD'],
    'BCM': ['Bất động sản', 'BĐS Khu công nghiệp'],
    'BDT': ['VLXD'],
    'BFC': ['Hóa chất - Phân bón'],
    'BIC': ['Bảo hiểm'],
    'BID': ['Ngân hàng'],
    'BKC': ['Khoáng sản'],
    'BMC': ['Khoáng sản'],
    'BMI': ['Bảo hiểm'],
    'BMP': ['Nước - Nhựa', 'VLXD'],
    'BMS': ['Chứng khoán'],
    'BNA': ['Thực phẩm'],
    'BOT': ['VLXD'],
    'BSI': ['Chứng khoán'],
    'BSR': ['Dầu khí'],
    'BTS': ['VLXD', 'Đầu tư công'],
    'BVB': ['Ngân hàng'],
    'BVH': ['Bảo hiểm'],
    'BVS': ['Chứng khoán'],
    'BWE': ['Nước - Nhựa'],
    'C32': ['Đầu tư công'],
    'C47': ['VLXD', 'Đầu tư công'],
    'C4G': ['VLXD', 'Đầu tư công'],
    'C69': ['VLXD'],
    'CBS': ['Đường - Gỗ - Giấy'],
    'CEO': ['Bất động sản'],
    'CIG': ['VLXD'],
    'CII': ['Bất động sản', 'Đầu tư công'],
    'CMG': ['Công nghệ - Truyền thông'],
    'CMX': ['Thủy sản'],
    'CNG': ['Dầu khí'],
    'CRC': ['VLXD'],
    'CRE': ['Bất động sản'],
    'CRV': ['Bất động sản'],
    'CSM': ['Ô tô - Phụ tùng'],
    'CSV': ['Hóa chất - Phân bón'],
    'CTD': ['Đầu tư công'],
    'CTF': ['Ô tô - Phụ tùng'],
    'CTG': ['Ngân hàng'],
    'CTI': ['VLXD', 'Đầu tư công'],
    'CTR': ['Công nghệ - Truyền thông', 'VLXD'],
    'CTS': ['Chứng khoán'],
    'D2D': ['BĐS Khu công nghiệp'],
    'DAG': ['Nước - Nhựa'],
    'DAH': ['Hàng không - Du lịch'],
    'DBC': ['Thực phẩm'],
    'DBD': ['Dược - Y tế'],
    'DC4': ['Bất động sản', 'VLXD'],
    'DCL': ['Dược - Y tế'],
    'DCM': ['Hóa chất - Phân bón'],
    'DDV': ['Hóa chất - Phân bón'],
    'DGC': ['Hóa chất - Phân bón'],
    'DGW': ['Bán lẻ'],
    'DHA': ['Đầu tư công'],
    'DHC': ['Đường - Gỗ - Giấy'],
    'DHD': ['Dược - Y tế'],
    'DHG': ['Dược - Y tế'],
    'DHT': ['Dược - Y tế'],
    'DIG': ['Bất động sản'],
    'DMC': ['Dược - Y tế'],
    'DPG': ['Đầu tư công'],
    'DPM': ['Hóa chất - Phân bón'],
    'DPR': ['Cao su'],
    'DRC': ['Ô tô - Phụ tùng'],
    'DRG': ['Cao su'],
    'DRI': ['Cao su'],
    'DSC': ['Chứng khoán'],
    'DSE': ['Chứng khoán'],
    'DTD': ['BĐS Khu công nghiệp', 'VLXD'],
    'DVG': ['VLXD'],
    'DVP': ['Cảng biển - Vận tải'],
    'DXG': ['Bất động sản'],
    'DXP': ['Cảng biển - Vận tải'],
    'DXS': ['Bất động sản'],
    'EIB': ['Ngân hàng'],
    'ELC': ['Công nghệ - Truyền thông'],
    'EVE': ['Dệt may'],
    'EVF': ['Ngân hàng'],
    'EVG': ['VLXD'],
    'EVS': ['Chứng khoán'],
    'FCM': ['VLXD', 'Khoáng sản'],
    'FCN': ['VLXD', 'Đầu tư công'],
    'FMC': ['Thủy sản'],
    'FOC': ['Công nghệ - Truyền thông'],
    'FOX': ['Công nghệ - Truyền thông'],
    'FPT': ['Công nghệ - Truyền thông'],
    'FRT': ['Bán lẻ'],
    'FTS': ['Chứng khoán'],
    'G36': ['VLXD', 'Đầu tư công'],
    'GAS': ['Dầu khí'],
    'GDA': ['Thép'],
    'GDT': ['Đường - Gỗ - Giấy'],
    'GEE': ['Điện - Năng lượng'],
    'GEG': ['Điện - Năng lượng'],
    'GEL': ['BĐS Khu công nghiệp'],
    'GEX': ['Điện - Năng lượng', 'VLXD'],
    'GIL': ['Dệt may'],
    'GMD': ['Cảng biển - Vận tải'],
    'GSP': ['Cảng biển - Vận tải'],
    'GVR': ['BĐS Khu công nghiệp', 'Cao su'],
    'HAG': ['Thực phẩm'],
    'HAH': ['Cảng biển - Vận tải'],
    'HAX': ['Ô tô - Phụ tùng'],
    'HBC': ['VLXD'],
    'HCM': ['Chứng khoán'],
    'HDB': ['Ngân hàng'],
    'HDC': ['Bất động sản'],
    'HDG': ['Bất động sản', 'Điện - Năng lượng'],
    'HGM': ['Khoáng sản'],
    'HHG': ['Cảng biển - Vận tải'],
    'HHP': ['Đường - Gỗ - Giấy'],
    'HHS': ['Bất động sản'],
    'HHV': ['VLXD', 'Đầu tư công'],
    'HII': ['Nước - Nhựa'],
    'HLD': ['Bất động sản'],
    'HND': ['Điện - Năng lượng'],
    'HNG': ['Thực phẩm'],
    'HOM': ['VLXD', 'Đầu tư công'],
    'HPG': ['Thép'],
    'HQC': ['Bất động sản'],
    'HRC': ['Cao su'],
    'HSG': ['Thép'],
    'HT1': ['VLXD', 'Đầu tư công'],
    'HTN': ['Bất động sản'],
    'HUB': ['VLXD'],
    'HUT': ['Bất động sản', 'VLXD'],
    'HVH': ['VLXD'],
    'HVN': ['Hàng không - Du lịch'],
    'IDC': ['BĐS Khu công nghiệp'],
    'IDI': ['Thủy sản'],
    'IDV': ['BĐS Khu công nghiệp'],
    'IJC': ['Bất động sản'],
    'ILB': ['Cảng biển - Vận tải'],
    'IMP': ['Dược - Y tế'],
    'ITD': ['Công nghệ - Truyền thông'],
    'JVC': ['Dược - Y tế'],
    'KBC': ['Bất động sản', 'BĐS Khu công nghiệp'],
    'KCB': ['Khoáng sản'],
    'KDC': ['Thực phẩm'],
    'KDH': ['Bất động sản'],
    'KHG': ['Bất động sản'],
    'KHP': ['Điện - Năng lượng'],
    'KLB': ['Ngân hàng'],
    'KMR': ['Dệt may'],
    'KSB': ['Đầu tư công'],
    'KSV': ['Khoáng sản'],
    'KTS': ['Đường - Gỗ - Giấy'],
    'L14': ['VLXD'],
    'L18': ['Bất động sản', 'VLXD'],
    'LAS': ['Hóa chất - Phân bón'],
    'LCG': ['VLXD', 'Đầu tư công'],
    'LDP': ['Dược - Y tế'],
    'LHG': ['BĐS Khu công nghiệp'],
    'LIG': ['VLXD'],
    'LIX': ['Hóa chất - Phân bón'],
    'LPB': ['Ngân hàng'],
    'LSS': ['Thực phẩm', 'Đường - Gỗ - Giấy'],
    'LTG': ['Thực phẩm'],
    'MAC': ['Cảng biển - Vận tải'],
    'MBB': ['Ngân hàng'],
    'MBS': ['Chứng khoán'],
    'MCH': ['Bán lẻ'],
    'MCM': ['Thực phẩm'],
    'MIG': ['Bảo hiểm'],
    'MLS': ['Thực phẩm'],
    'MSB': ['Ngân hàng'],
    'MSH': ['Dệt may'],
    'MSN': ['Bán lẻ', 'Thực phẩm'],
    'MSR': ['Khoáng sản'],
    'MVC': ['VLXD'],
    'MWG': ['Bán lẻ'],
    'NAB': ['Ngân hàng'],
    'NAF': ['Thực phẩm'],
    'NCT': ['Hàng không - Du lịch'],
    'NDN': ['Bất động sản'],
    'NDX': ['VLXD'],
    'NED': ['VLXD'],
    'NET': ['Hóa chất - Phân bón'],
    'NHA': ['VLXD'],
    'NHH': ['Nước - Nhựa'],
    'NKG': ['Thép'],
    'NLG': ['Bất động sản'],
    'NNC': ['VLXD'],
    'NSH': ['Thép'],
    'NT2': ['Điện - Năng lượng'],
    'NTC': ['BĐS Khu công nghiệp'],
    'NTL': ['Bất động sản'],
    'NTP': ['Nước - Nhựa'],
    'NVB': ['Ngân hàng'],
    'NVL': ['Bất động sản'],
    'OCB': ['Ngân hàng'],
    'OIL': ['Dầu khí'],
    'ORS': ['Chứng khoán'],
    'PAC': ['Hóa chất - Phân bón', 'Ô tô - Phụ tùng'],
    'PAN': ['Thực phẩm'],
    'PC1': ['Điện - Năng lượng', 'VLXD'],
    'PDR': ['Bất động sản'],
    'PET': ['Bán lẻ'],
    'PGI': ['Bảo hiểm'],
    'PGN': ['Nước - Nhựa'],
    'PHC': ['VLXD'],
    'PHP': ['Cảng biển - Vận tải'],
    'PHR': ['BĐS Khu công nghiệp', 'Cao su'],
    'PLC': ['Dầu khí'],
    'PLP': ['Nước - Nhựa'],
    'PLX': ['Dầu khí'],
    'PMB': ['Hóa chất - Phân bón'],
    'PNJ': ['Bán lẻ'],
    'POM': ['Thép'],
    'POW': ['Điện - Năng lượng'],
    'PPC': ['Điện - Năng lượng'],
    'PRE': ['Bảo hiểm'],
    'PSD': ['Bán lẻ'],
    'PSI': ['Chứng khoán'],
    'PTB': ['VLXD'],
    'PTI': ['Bảo hiểm'],
    'PVB': ['Dầu khí'],
    'PVC': ['Dầu khí'],
    'PVD': ['Dầu khí'],
    'PVG': ['Điện - Năng lượng'],
    'PVI': ['Bảo hiểm'],
    'PVP': ['Dầu khí', 'Cảng biển - Vận tải'],
    'PVS': ['Dầu khí'],
    'PVT': ['Dầu khí', 'Cảng biển - Vận tải'],
    'PXT': ['VLXD'],
    'QCG': ['Bất động sản'],
    'QNS': ['Thực phẩm', 'Đường - Gỗ - Giấy'],
    'QTP': ['Điện - Năng lượng'],
    'REE': ['Điện - Năng lượng'],
    'RGG': ['Bất động sản'],
    'RIC': ['Hàng không - Du lịch'],
    'S99': ['VLXD'],
    'SAS': ['Hàng không - Du lịch'],
    'SBS': ['Chứng khoán'],
    'SBT': ['Đường - Gỗ - Giấy'],
    'SCI': ['VLXD'],
    'SCR': ['Bất động sản'],
    'SCS': ['Hàng không - Du lịch'],
    'SD5': ['VLXD', 'Đầu tư công'],
    'SD6': ['VLXD', 'Đầu tư công'],
    'SD9': ['VLXD'],
    'SDD': ['VLXD'],
    'SGN': ['Hàng không - Du lịch'],
    'SGP': ['Cảng biển - Vận tải'],
    'SGR': ['Bất động sản'],
    'SHA': ['Thép'],
    'SHB': ['Ngân hàng'],
    'SHI': ['Thép'],
    'SHS': ['Chứng khoán'],
    'SIP': ['BĐS Khu công nghiệp'],
    'SJD': ['Điện - Năng lượng'],
    'SJE': ['Điện - Năng lượng'],
    'SJS': ['Bất động sản'],
    'SKG': ['Hàng không - Du lịch', 'Cảng biển - Vận tải'],
    'SLS': ['Thực phẩm', 'Đường - Gỗ - Giấy'],
    'SMC': ['Thép'],
    'SPM': ['Dược - Y tế'],
    'SSB': ['Ngân hàng'],
    'SSI': ['Chứng khoán'],
    'STB': ['Ngân hàng'],
    'STG': ['Cảng biển - Vận tải'],
    'STK': ['Dệt may'],
    'SWC': ['Cảng biển - Vận tải'],
    'SZC': ['BĐS Khu công nghiệp'],
    'SZL': ['BĐS Khu công nghiệp'],
    'TAR': ['Thực phẩm'],
    'TCB': ['Ngân hàng'],
    'TCH': ['Bất động sản'],
    'TCI': ['Chứng khoán'],
    'TCL': ['Cảng biển - Vận tải'],
    'TCM': ['Dệt may'],
    'TCO': ['Cảng biển - Vận tải'],
    'TCW': ['Cảng biển - Vận tải'],
    'TCX': ['Chứng khoán'],
    'TDC': ['Bất động sản'],
    'TDM': ['Nước - Nhựa'],
    'THG': ['Đầu tư công'],
    'TIP': ['BĐS Khu công nghiệp'],
    'TLH': ['Thép'],
    'TMS': ['Cảng biển - Vận tải'],
    'TNA': ['VLXD'],
    'TNC': ['Cao su'],
    'TNG': ['Dệt may'],
    'TNH': ['Dược - Y tế'],
    'TPB': ['Ngân hàng'],
    'TRC': ['Cao su'],
    'TTA': ['Điện - Năng lượng'],
    'TTF': ['VLXD'],
    'TV1': ['Điện - Năng lượng'],
    'TV2': ['Điện - Năng lượng', 'Đầu tư công'],
    'TVB': ['Chứng khoán'],
    'TVC': ['Chứng khoán'],
    'TVN': ['Thép'],
    'TVS': ['Chứng khoán'],
    'UDC': ['VLXD'],
    'VAB': ['Ngân hàng'],
    'VC2': ['VLXD'],
    'VC7': ['VLXD'],
    'VC9': ['VLXD'],
    'VCA': ['Thép'],
    'VCB': ['Ngân hàng'],
    'VCG': ['VLXD', 'Đầu tư công'],
    'VCI': ['Chứng khoán'],
    'VCK': ['Chứng khoán'],
    'VCS': ['VLXD'],
    'VDS': ['Chứng khoán'],
    'VGC': ['BĐS Khu công nghiệp', 'VLXD'],
    'VGI': ['Công nghệ - Truyền thông'],
    'VGS': ['Thép'],
    'VGT': ['Dệt may'],
    'VHC': ['Thủy sản'],
    'VHE': ['Thực phẩm'],
    'VHM': ['Bất động sản'],
    'VIB': ['Ngân hàng'],
    'VIC': ['Bất động sản'],
    'VIG': ['Chứng khoán'],
    'VIP': ['Cảng biển - Vận tải'],
    'VIX': ['Chứng khoán'],
    'VJC': ['Hàng không - Du lịch'],
    'VLB': ['VLXD'],
    'VLC': ['Thực phẩm'],
    'VNA': ['Cảng biển - Vận tải'],
    'VND': ['Chứng khoán'],
    'VNE': ['Điện - Năng lượng', 'VLXD'],
    'VNM': ['Thực phẩm'],
    'VNP': ['Nước - Nhựa'],
    'VNR': ['Bảo hiểm'],
    'VOC': ['Thực phẩm'],
    'VOS': ['Cảng biển - Vận tải'],
    'VPB': ['Ngân hàng'],
    'VPI': ['Bất động sản'],
    'VPL': ['Bất động sản'],
    'VPX': ['Chứng khoán'],
    'VRE': ['Bất động sản'],
    'VSC': ['Cảng biển - Vận tải'],
    'VSH': ['Điện - Năng lượng'],
    'VTD': ['Hàng không - Du lịch'],
    'VTO': ['Cảng biển - Vận tải'],
    'VTP': ['Công nghệ - Truyền thông'],
    'VTV': ['VLXD'],
    'WSS': ['Chứng khoán'],
    'YBM': ['Khoáng sản'],
    'YEG': ['Công nghệ - Truyền thông'],
}


# Build SECTOR_SYMBOLS (per-archetype symbol list) from SIEUCOPHIEU_MULTIMAP.
# A symbol is listed in each archetype it belongs to. Order in each list follows
# the iteration order of the multimap (alphabetical by symbol).
for _sym, _secs in SIEUCOPHIEU_MULTIMAP.items():
    for _display_name in _secs:
        _arch = DISPLAY_NAME_TO_ARCHETYPE.get(_display_name)
        if _arch and _sym not in SECTOR_SYMBOLS[_arch]:
            SECTOR_SYMBOLS[_arch].append(_sym)

# Inject SECTOR_SYMBOLS into each SECTOR_DEFINITIONS entry so that any caller iterating
# SECTOR_DEFINITIONS[*]["symbols"] still gets a valid list (single-membership view = primary).
for _arch, _data in SECTOR_DEFINITIONS.items():
    _data["symbols"] = list(SECTOR_SYMBOLS.get(_arch, []))


# Legacy single-membership SECTOR_MAP, derived from SIEUCOPHIEU_MULTIMAP (uses first sector
# as the "primary" membership). Backward-compat for callers that expect a 1:1 map.
SECTOR_MAP = {}
for _sym, _secs in SIEUCOPHIEU_MULTIMAP.items():
    if not _secs:
        continue
    _arch = DISPLAY_NAME_TO_ARCHETYPE.get(_secs[0])
    if _arch:
        SECTOR_MAP[_sym] = {"sector": _secs[0], "archetype": _arch}


def _fallback_sector_info(comp_overview: dict = None, get_bs_item=None) -> dict:
    """Keyword-based fallback for stocks not in SIEUCOPHIEU_MULTIMAP."""
    icb_api_map = get_api_icb_map()
    icb_text = icb_api_map.get("", "")

    overview_text = ""
    if comp_overview and isinstance(comp_overview, dict):
        organ_name = str(comp_overview.get("organ_name") or "")
        icb_name = str(comp_overview.get("industry") or comp_overview.get("icb_name") or comp_overview.get("sector") or "")
        profile = str(comp_overview.get("company_profile") or "")
        overview_text = f"{organ_name} {icb_name} {profile}"

    combined_text = f"{icb_text} {overview_text}".lower()

    # Keyed by archetype to avoid string drift.
    KEYWORD_MAP = [
        ("STEEL",              ["thép và sản phẩm thép", "thép", "steel", "tôn phôi", "phôi thép", "xản xuất thép"]),
        ("SECURITIES",         ["môi giới chứng khoán", "công ty chứng khoán", "hoạt động chứng khoán"]),
        ("FINANCIAL_SERVICES", ["dịch vụ tài chính", "tài chính tiêu dùng", "cầm đồ", "cho vay tiêu dùng", "đầu tư tài chính", "financial services", "consumer finance"]),
        ("INDUSTRIAL_PARK",    ["khu công nghiệp", "kcn", "bất động sản khu công nghiệp", "hạ tầng khu công nghiệp"]),
        ("REAL_ESTATE",        ["bất động sản", "real estate", "phát triển nhà", "kinh doanh nhà"]),
        ("BANKING",            ["ngân hàng", "banking"]),
        ("INSURANCE",          ["bảo hiểm", "insurance"]),
        ("CHEMICALS_FERTILIZERS", ["hóa chất", "phân bón", "nông dược", "chemicals", "phốt pho"]),
        ("SEAFOOD",            ["thủy sản", "tôm", "cá tra", "seafood"]),
        ("TEXTILE",            ["dệt may", "may mặc", "sợi", "garment", "textile"]),
        ("RETAIL",             ["bán lẻ", "retail", "siêu thị", "chuỗi cửa hàng", "vàng bạc", "trang sức", "đá quý", "kim hoàn", "vàng miếng", "cửa hàng trang sức", "personal & household goods", "hàng cá nhân"]),
        ("WATER_PLASTICS",     ["cấp nước", "thoát nước", "phân phối nước", "sản xuất nhựa", "plastics"]),
        ("POWER_ENERGY",       ["điện lực", "phát điện", "thủy điện", "nhiệt điện", "năng lượng tái tạo"]),
        ("OIL_GAS",            ["dầu khí", "khoan dầu khí", "thiết bị dầu khí", "dịch vụ dầu khí", "petro"]),
        ("MINING",             ["khoáng sản", "quặng", "mining", "than đá"]),
        ("AUTOMOTIVE",         ["ô tô", "phụ tùng ô tô", "lốp", "auto"]),
        ("PHARMA_HEALTHCARE",  ["dược", "y tế", "bệnh viện", "pharma"]),
        ("TECH_TELECOM",       ["công nghệ thông tin", "phần mềm", "viễn thông", "technology"]),
        ("AVIATION_TOURISM",   ["hàng không", "du lịch", "khách sạn", "aviation"]),
        ("PORTS_LOGISTICS",    ["cảng biển", "vận tải biển", "logistics", "kho bãi"]),
        ("BUILDING_MATERIALS", ["vật liệu xây dựng", "xi măng", "đá xây dựng", "gạch"]),
        ("CONSTRUCTION",       ["xây dựng", "xây lắp", "đầu tư công", "construction"]),
        ("SUGAR_WOOD_PAPER",   ["mía đường", "chế biến gỗ", "sản xuất giấy", "sugar", "paper", "wood"]),
        ("FOOD_BEVERAGE",      ["thực phẩm", "đồ uống", "sữa", "bánh kẹo", "nước giải khát"]),
        ("RUBBER",             ["cao su", "rubber"]),
    ]
    for arch, keywords in KEYWORD_MAP:
        if any(k in combined_text for k in keywords):
            return {"sector": SECTOR_DEFINITIONS[arch]["sector"], "archetype": arch}

    # Balance-sheet financial signature fallback (banking vs securities).
    if get_bs_item and callable(get_bs_item):
        margin_loans = get_bs_item(['Các khoản cho vay', 'Phải thu về cho vay ngắn hạn'])
        fvtpl_assets = get_bs_item(['Các tài sản tài chính ghi nhận thông qua lãi lỗ (FVTPL)'])
        deposits = get_bs_item(['Tiền gửi của khách hàng', 'Tiền gửi khách hàng'])
        if margin_loans > 0 or fvtpl_assets > 0:
            return {"sector": "Chứng khoán", "archetype": "SECURITIES"}
        if deposits > 0:
            return {"sector": "Ngân hàng", "archetype": "BANKING"}

    return {"sector": "Sản xuất công nghiệp", "archetype": "MANUFACTURING_GENERAL"}


def get_sector_memberships(symbol: str, comp_overview: dict = None, get_bs_item=None) -> list:
    """
    Return the full list of sector memberships for a symbol.

    Each entry is `{"sector": <display_name>, "archetype": <code>}`. For symbols
    in SIEUCOPHIEU_MULTIMAP, returns all listed memberships. For unknown symbols,
    returns a single-entry list from the keyword/balance-sheet fallback.
    """
    sym_upper = symbol.upper().strip()
    secs = SIEUCOPHIEU_MULTIMAP.get(sym_upper)
    if secs:
        out = []
        for display_name in secs:
            arch = DISPLAY_NAME_TO_ARCHETYPE.get(display_name, "MANUFACTURING_GENERAL")
            out.append({"sector": display_name, "archetype": arch})
        return out
    # Fallback: single membership via keyword matcher / balance-sheet signature.
    return [_fallback_sector_info(comp_overview, get_bs_item)]


def get_sector_info(symbol: str, comp_overview: dict = None, get_bs_item = None) -> dict:
    """Return the primary sector membership for a symbol (single dict).

    The "primary" sector is the first one in SIEUCOPHIEU_MULTIMAP for that symbol.
    For unknown symbols, falls back to the keyword matcher / balance-sheet signature.
    """
    sym_upper = symbol.upper().strip()
    if sym_upper in SECTOR_MAP:
        return SECTOR_MAP[sym_upper]
    return _fallback_sector_info(comp_overview, get_bs_item)


def get_ui_badge(archetype: str) -> dict:
    """Return the canonical heatmap sector without a second UI grouping layer."""
    from industry_indicator_profiles import get_industry_profile

    profile = get_industry_profile(archetype)
    return {
        "badge": profile["name"],
        "badge_code": profile["archetype"],
        "sub_sector": None,
    }