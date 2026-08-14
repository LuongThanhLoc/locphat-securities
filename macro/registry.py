"""Declarative contracts for official macro series and presentation rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    series_id: str
    title_vi: str
    category: str
    unit: str
    frequency: str
    transform: str
    patterns: tuple[str, ...]
    overview_vi: str
    impact_analysis_vi: str
    vn_market_impact_vi: str
    source_publisher: str


INDICATORS: dict[str, IndicatorSpec] = {
    "cpi": IndicatorSpec(
        "cpi", "CPIAUCSL", "Chỉ số giá tiêu dùng Mỹ (CPI)", "inflation", "% m/m",
        "monthly", "percent_change", (r"consumer price index", r"\bcpi m/m\b", r"\bcpi y/y\b", r"\bcpi\b"),
        "CPI đo mức thay đổi giá của giỏ hàng hóa và dịch vụ tiêu dùng tại Mỹ.",
        "CPI tăng hoặc giảm cần được đọc cùng xu hướng nhiều kỳ; một điểm dữ liệu đơn lẻ không tự động quyết định chính sách của Fed.",
        "Kênh tác động chính tới Việt Nam là kỳ vọng lãi suất Mỹ, lợi suất trái phiếu, USD/VND và dòng vốn quốc tế.",
        "U.S. Bureau of Labor Statistics",
    ),
    "core_cpi": IndicatorSpec(
        "core_cpi", "CPILFESL", "CPI lõi Mỹ", "inflation", "% m/m", "monthly",
        "percent_change", (r"core cpi", r"core consumer price"),
        "CPI lõi loại trừ thực phẩm và năng lượng, giúp theo dõi áp lực giá có tính dai dẳng.",
        "Xu hướng nhiều tháng quan trọng hơn một lần công bố; mức tăng cao kéo dài có thể làm giảm dư địa nới lỏng tiền tệ.",
        "Lãi suất Mỹ cao lâu hơn có thể gây áp lực lên tỷ giá và định giá tài sản rủi ro tại Việt Nam.",
        "U.S. Bureau of Labor Statistics",
    ),
    "ppi": IndicatorSpec(
        "ppi", "PPIFIS", "Chỉ số giá sản xuất Mỹ (PPI)", "inflation", "% m/m", "monthly",
        "percent_change", (r"producer price index", r"\bppi m/m\b", r"\bppi y/y\b", r"\bppi\b"),
        "PPI đo mức thay đổi giá bán buôn của các nhà sản xuất tại Mỹ, là chỉ báo dẫn dắt cho CPI.",
        "PPI hạ nhiệt cho thấy áp lực chi phí đầu vào của doanh nghiệp giảm, giảm áp lực tăng giá thành sản phẩm tiêu dùng.",
        "Hỗ trợ ổn định chi phí nhập khẩu nguyên liệu và kỳ vọng hạ nhiệt tỷ giá USD/VND.",
        "U.S. Bureau of Labor Statistics",
    ),
    "core_ppi": IndicatorSpec(
        "core_ppi", "WPSFD4131", "PPI lõi Mỹ", "inflation", "% m/m", "monthly",
        "percent_change", (r"core ppi", r"core producer price"),
        "PPI lõi loại trừ thực phẩm và năng lượng biến động nhằm đo lường xu hướng giá sản xuất cốt lõi.",
        "Phản ánh độ dai dẳng của chi phí sản xuất trong nền kinh tế Mỹ.",
        "Tác động gián tiếp qua kỳ vọng lãi suất của Fed và tâm lý thị trường vốn quốc tế.",
        "U.S. Bureau of Labor Statistics",
    ),
    "nonfarm_payrolls": IndicatorSpec(
        "nonfarm_payrolls", "PAYEMS", "Bảng lương phi nông nghiệp Mỹ", "employment", "nghìn việc làm",
        "monthly", "difference", (r"non.?farm", r"employment situation", r"payroll"),
        "Bảng lương phi nông nghiệp đo thay đổi việc làm trong các ngành ngoài nông nghiệp.",
        "Cần đọc cùng thất nghiệp, tiền lương và các revision; số việc làm tăng không mặc nhiên là tích cực cho mọi tài sản.",
        "Thị trường lao động Mỹ ảnh hưởng kỳ vọng Fed, DXY, lợi suất và dòng vốn vào thị trường mới nổi.",
        "U.S. Bureau of Labor Statistics",
    ),
    "unemployment": IndicatorSpec(
        "unemployment", "UNRATE", "Tỷ lệ thất nghiệp Mỹ", "employment", "%", "monthly", "level",
        (r"unemployment rate",),
        "Tỷ lệ thất nghiệp phản ánh tỷ trọng lực lượng lao động đang tìm việc nhưng chưa có việc làm.",
        "Nên đánh giá cùng tỷ lệ tham gia lao động và tăng trưởng tiền lương thay vì diễn giải theo một chiều cố định.",
        "Sự thay đổi kỳ vọng tăng trưởng và chính sách Fed truyền dẫn tới tỷ giá và dòng vốn quốc tế.",
        "U.S. Bureau of Labor Statistics",
    ),
    "pce": IndicatorSpec(
        "pce", "PCEPI", "Chỉ số giá PCE Mỹ", "inflation", "% m/m", "monthly", "percent_change",
        (r"personal income and outlays", r"pce price index", r"\bpce\b"),
        "PCE là thước đo giá tiêu dùng trong hệ thống tài khoản quốc gia Mỹ.",
        "Fed theo dõi xu hướng PCE và PCE lõi, nhưng phản ứng chính sách còn phụ thuộc tăng trưởng và lao động.",
        "PCE tác động tới lợi suất, đồng USD và điều kiện tài chính đối với thị trường Việt Nam.",
        "U.S. Bureau of Economic Analysis",
    ),
    "core_pce": IndicatorSpec(
        "core_pce", "PCEPILFE", "PCE lõi Mỹ", "inflation", "% m/m", "monthly", "percent_change",
        (r"core pce",),
        "PCE lõi loại trừ thực phẩm và năng lượng để theo dõi áp lực giá nền.",
        "Đánh giá dựa trên chuỗi nhiều kỳ và revision, không gắn nhãn tốt/xấu từ một lần công bố.",
        "Tác động chính qua lãi suất Mỹ, tỷ giá và mức chiết khấu của tài sản rủi ro.",
        "U.S. Bureau of Economic Analysis",
    ),
    "gdp": IndicatorSpec(
        "gdp", "A191RL1Q225SBEA", "Tăng trưởng GDP thực Mỹ", "growth", "% q/q SAAR", "quarterly", "level",
        (r"gross domestic product", r"advance gdp", r"preliminary gdp", r"\bgdp\b"),
        "GDP thực đo tăng trưởng sản lượng sau khi loại trừ ảnh hưởng giá.",
        "Các ước tính advance, second và third có thể được sửa đổi; UI phải hiển thị đúng phiên bản công bố.",
        "Tăng trưởng Mỹ ảnh hưởng nhu cầu hàng hóa, xuất khẩu và khẩu vị rủi ro toàn cầu.",
        "U.S. Bureau of Economic Analysis",
    ),
    "retail_sales": IndicatorSpec(
        "retail_sales", "RSAFS", "Doanh số bán lẻ Mỹ", "trade_manufacturing", "% m/m", "monthly", "percent_change",
        (r"retail sales", r"core retail sales"),
        "Doanh số bán lẻ phản ánh chi tiêu hàng hóa của người tiêu dùng Mỹ.",
        "Dữ liệu có thể biến động và được sửa đổi; nên đọc cùng lạm phát và thu nhập thực.",
        "Nhu cầu tiêu dùng Mỹ liên quan trực tiếp tới triển vọng đơn hàng xuất khẩu của doanh nghiệp Việt Nam.",
        "U.S. Census Bureau",
    ),
    "jobless_claims": IndicatorSpec(
        "jobless_claims", "ICSA", "Đơn xin trợ cấp thất nghiệp lần đầu", "employment", "nghìn đơn", "weekly", "thousands",
        (r"initial jobless claims", r"unemployment insurance weekly", r"jobless claims"),
        "Số đơn trợ cấp lần đầu là chỉ báo tần suất cao về tình trạng sa thải.",
        "Chuỗi trung bình nhiều tuần có ý nghĩa hơn một quan sát đơn lẻ.",
        "Dữ liệu ảnh hưởng ngắn hạn tới kỳ vọng lãi suất Mỹ và tâm lý tài sản rủi ro.",
        "U.S. Department of Labor",
    ),
    "fed_funds": IndicatorSpec(
        "fed_funds", "DFEDTARU", "Cận trên lãi suất mục tiêu Fed Funds", "interest_rate", "%", "daily", "level",
        (r"federal funds rate", r"fomc statement", r"interest rate decision", r"fomc meeting"),
        "Mục tiêu Fed Funds là công cụ chính định hướng điều kiện tiền tệ ngắn hạn tại Mỹ.",
        "Quyết định cần đọc cùng tuyên bố, họp báo và dự báo kinh tế của FOMC.",
        "Đây là kênh tác động lớn tới USD/VND, chi phí vốn, khối ngoại và định giá thị trường Việt Nam.",
        "Board of Governors of the Federal Reserve System",
    ),
    "michigan_sentiment": IndicatorSpec(
        "michigan_sentiment", "UMCSENT", "Tâm lý Người tiêu dùng Michigan (UoM)", "growth", "điểm", "monthly", "level",
        (r"uom consumer sentiment", r"prelim uom consumer sentiment", r"consumer sentiment"),
        "Khảo sát tâm lý người tiêu dùng của Đại học Michigan về tình hình tài chính và triển vọng kinh tế Mỹ.",
        "Tâm lý lạc quan hỗ trợ chi tiêu tiêu dùng nhưng cũng có thể làm chậm quá trình hạ nhiệt lạm phát.",
        "Phản ánh sức khỏe kinh tế vĩ mô và sức mua của thị trường xuất khẩu hàng đầu của Việt Nam.",
        "University of Michigan / Surveys of Consumers",
    ),
    "michigan_inflation": IndicatorSpec(
        "michigan_inflation", "MICH", "Kỳ vọng Lạm phát Michigan (UoM)", "inflation", "%", "monthly", "level",
        (r"uom inflation expectations", r"prelim uom inflation expectations", r"inflation expectations"),
        "Đo lường mức lạm phát kỳ vọng của người tiêu dùng Mỹ trong 1 năm tới.",
        "Kỳ vọng lạm phát neo vững là mục tiêu then chốt của Fed trong việc quyết định lộ trình lãi suất.",
        "Tác động tới lợi suất trái phiếu kho bạc Mỹ và kỳ vọng tỷ giá USD/VND.",
        "University of Michigan / Surveys of Consumers",
    ),
    "building_permits": IndicatorSpec(
        "building_permits", "PERMIT", "Giấy phép Xây dựng Nhà ở Mỹ", "housing", "nghìn căn", "monthly", "thousands",
        (r"building permits", r"housing starts", r"new residential construction"),
        "Số lượng giấy phép xây dựng nhà ở mới được cấp, là chỉ báo sớm về sức khỏe ngành bất động sản Mỹ.",
        "Phản ánh độ nhạy cảm của ngành xây dựng với mặt bằng lãi suất vay thế chấp.",
        "Chỉ báo dẫn dắt cho nhu cầu vật liệu xây dựng và tâm lý thị trường vốn toàn cầu.",
        "U.S. Census Bureau",
    ),
    "trade_balance": IndicatorSpec(
        "trade_balance", "BOPGSTB", "Cán cân Thương mại Mỹ", "trade_manufacturing", "tỷ USD", "monthly", "level",
        (r"trade balance", r"international trade", r"goods trade balance"),
        "Chênh lệch giữa giá trị xuất khẩu và nhập khẩu hàng hóa dịch vụ của Mỹ.",
        "Thâm hụt thương mại phản ánh nhu cầu tiêu dùng nội địa Mỹ đối với hàng hóa toàn cầu.",
        "Việt Nam là đối tác thặng dư thương mại lớn với Mỹ, nên dữ liệu này rất quan trọng cho nhóm dệt may, thủy sản, gỗ.",
        "U.S. Bureau of Economic Analysis & U.S. Census Bureau",
    ),
}


TICKER_SERIES = {
    "DXY": {"series_id": "DTWEXBGS", "name": "Dollar Index (Fed Broad DXY)", "unit": "điểm", "decimals": 2},
    "US10Y": {"series_id": "DGS10", "name": "Lợi suất TPCP Mỹ 10 năm", "unit": "%", "decimals": 2},
    "USDVND": {"series_id": "DEXVUS", "name": "Tỷ giá USD/VND (Nguồn Fed)", "unit": "VND/USD", "decimals": 0},
    "WTI_OIL": {"series_id": "DCOILWTICO", "name": "Dầu thô WTI", "unit": "USD/thùng", "decimals": 2},
    "GOLD": {"series_id": "GOLDAMGBD228NLBM", "name": "Vàng London Fix", "unit": "USD/oz", "decimals": 2},
}


CATEGORY_NAMES = {
    "interest_rate": "Lãi suất & NHTW",
    "inflation": "Lạm phát & Giá cả",
    "employment": "Việc làm & Lao động",
    "growth": "Tăng trưởng & GDP",
    "trade_manufacturing": "Sản xuất & Thương mại",
    "housing": "Bất động sản & Xây dựng",
    "bonds": "Trái phiếu & Ngân sách",
    "energy": "Năng lượng & Hàng hóa",
    "general": "Chỉ số kinh tế",
}


def find_indicator(title: str) -> Optional[IndicatorSpec]:
    import re

    # Specific series must win over their broader parent series.
    order = (
        "core_cpi", "core_pce", "core_ppi", "ppi", "cpi", "pce",
        "michigan_inflation", "michigan_sentiment", "nonfarm_payrolls", "unemployment",
        "jobless_claims", "gdp", "retail_sales", "fed_funds", "building_permits",
        "trade_balance",
    )
    for key in order:
        spec = INDICATORS[key]
        if any(re.search(pattern, title or "", re.IGNORECASE) for pattern in spec.patterns):
            return spec
    return None
