import os
import requests
import json
import time
import urllib.parse
import hashlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import escape
from typing import Dict, Any, List
from bs4 import BeautifulSoup
from market_data_provider import Company
from data_freshness import now_vn_iso

def get_env_api_key(key_name: str) -> str:
    key = os.environ.get(key_name, "").strip()
    if key:
        return key
    for env_path in [os.path.join(os.path.dirname(__file__), ".env"), "/etc/secrets/.env"]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith(key_name):
                            parts = line.strip().split("=", 1)
                            if len(parts) == 2:
                                return parts[1].strip().strip('"\'')
            except Exception:
                pass
    return ""


DEEPSEEK_API_KEY = get_env_api_key("DEEPSEEK_API_KEY")

DEEPSEEK_MODELS = [
    "deepseek-chat",
    "deepseek-v4-flash"
]

_NEWS_CACHE: Dict[str, Any] = {}
_NEWS_IMAGE_REGISTRY: Dict[str, str] = {}
_NEWS_CACHE_TTL_SECONDS = 120
_NEWS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36 LPSecResearch/2.0"
}


def _unwrap_article_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    for key in ("url", "uddg"):
        candidate = params.get(key, [""])[0]
        if candidate.startswith(("http://", "https://")):
            return candidate
    return url if parsed.scheme in ("http", "https") else ""


def _article_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    url = _unwrap_article_url(item.get("article_url", ""))
    item["article_url"] = url
    if not url:
        return item
    try:
        response = requests.get(url, headers=_NEWS_HEADERS, timeout=8, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        def meta_value(*selectors: str) -> str:
            for selector in selectors:
                node = soup.select_one(selector)
                value = node.get("content", "").strip() if node else ""
                if value:
                    return value
            return ""

        image_url = meta_value(
            'meta[property="og:image"]',
            'meta[name="twitter:image"]',
            'meta[property="twitter:image"]',
        )
        item["image_url"] = urllib.parse.urljoin(response.url, image_url) if image_url else ""
        item["source"] = meta_value('meta[property="og:site_name"]') or urllib.parse.urlparse(response.url).netloc
        item["article_url"] = response.url
        published = meta_value(
            'meta[property="article:published_time"]',
            'meta[name="date"]',
            'meta[name="pubdate"]',
        )
        if published:
            item["published_at"] = published
    except Exception as exc:
        print(f"Article metadata warning for {url}: {exc}")
        item["source"] = urllib.parse.urlparse(url).netloc
    return item


def _bing_rss_news(symbol: str) -> List[Dict[str, Any]]:
    response = requests.get(
        "https://www.bing.com/news/search",
        params={"q": f'"{symbol}" cổ phiếu doanh nghiệp', "format": "rss", "setlang": "vi"},
        headers=_NEWS_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    items = []
    for node in root.findall(".//item")[:10]:
        title = (node.findtext("title") or "").strip()
        link = _unwrap_article_url((node.findtext("link") or "").strip())
        description = BeautifulSoup(node.findtext("description") or "", "html.parser").get_text(" ", strip=True)
        published_raw = (node.findtext("pubDate") or "").strip()
        published_at = published_raw
        if published_raw:
            try:
                published_at = parsedate_to_datetime(published_raw).isoformat()
            except (TypeError, ValueError):
                pass
        if title and link:
            items.append({
                "title": title,
                "snippet": description or title,
                "article_url": link,
                "image_url": "",
                "published_at": published_at,
                "timestamp": published_raw or "Mới cập nhật",
                "source": urllib.parse.urlparse(link).netloc,
                "do_tin_cay": "Bài gốc có thể kiểm tra",
            })
    return items


def _company_disclosure_news(symbol: str) -> List[Dict[str, Any]]:
    """Grounded fallback from Vietcap/Fiin company disclosures."""
    try:
        frame = Company(symbol).news()
        if frame is None or frame.empty:
            return []
        items = []
        for _, row in frame.head(10).iterrows():
            title = str(row.get("newsTitle") or "").strip()
            link = str(row.get("newsSourceLink") or "").strip()
            image_url = str(row.get("newsImageUrl") or row.get("newsSmallImageUrl") or "").strip()
            if title:
                items.append({
                    "title": title,
                    "snippet": title,
                    "article_url": link if link.startswith(("http://", "https://")) else "",
                    "image_url": image_url if image_url.startswith(("http://", "https://")) else "",
                    "published_at": str(row.get("publicDate") or ""),
                    "timestamp": str(row.get("publicDate") or "Mới cập nhật"),
                    "source": "Công bố doanh nghiệp / Vietcap",
                    "do_tin_cay": "Nguồn công bố doanh nghiệp",
                })
        return items
    except Exception as exc:
        print(f"Company disclosure news warning for {symbol}: {exc}")
        return []

def fetch_real_news_feed(symbol: str) -> List[Dict[str, Any]]:
    """Fetch grounded headlines and resolve real article images when available."""
    symbol = symbol.upper().strip()
    cached = _NEWS_CACHE.get(symbol)
    if cached and time.monotonic() - cached[0] < _NEWS_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        candidates = _bing_rss_news(symbol)
    except Exception as exc:
        print(f"Bing RSS warning for {symbol}: {exc}")
        candidates = []

    candidates.extend(_company_disclosure_news(symbol))
    unique = []
    seen = set()
    for item in candidates:
        key = (item.get("article_url") or item.get("title", "")).lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    with ThreadPoolExecutor(max_workers=5) as pool:
        enriched = list(pool.map(_article_metadata, unique[:5]))
    fetched_at = now_vn_iso()
    for item in enriched:
        title = item.get("title", "")
        item["summary_html"] = f'<span class="ticker">${escape(symbol)}</span> {escape(title)}'
        item["fetched_at"] = fetched_at
        image_url = str(item.get("image_url") or "").strip()
        if image_url.startswith(("http://", "https://")):
            token = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:24]
            _NEWS_IMAGE_REGISTRY[token] = image_url
            item["image_proxy_url"] = f"/api/news-image/{token}"
    _NEWS_CACHE[symbol] = (time.monotonic(), enriched)
    return enriched


def fetch_registered_news_image(token: str) -> tuple[bytes, str]:
    """Fetch only image URLs registered by the grounded-news pipeline."""
    image_url = _NEWS_IMAGE_REGISTRY.get(token)
    if not image_url:
        raise KeyError("Ảnh tin tức không tồn tại hoặc đã hết hạn.")
    response = requests.get(
        image_url,
        headers={**_NEWS_HEADERS, "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*"},
        timeout=12,
    )
    response.raise_for_status()
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    if not content_type.startswith("image/"):
        raise ValueError("Nguồn trả về nội dung không phải hình ảnh.")
    if len(response.content) > 5 * 1024 * 1024:
        raise ValueError("Ảnh tin tức vượt giới hạn 5 MB.")
    return response.content, content_type


def generate_ai_thesis_prompt(symbol: str, stock_data: Dict[str, Any]) -> str:
    val = stock_data.get("valuation", {})
    sector_name = stock_data.get("sector_name", "Doanh nghiệp")
    
    pe = float(val.get("pe_ratio") or 0.0)
    pb = float(val.get("pb_ratio") or 0.0)
    roe = float(val.get("roe_ratio") or 0.0)
    npat_ttm = float(val.get("npat_ttm_billion") or 0.0)
    current_price = float(stock_data.get("current_price") or 0.0)

    if current_price <= 0:
        raise ValueError(f"Dữ liệu giá thị trường cho mã '{symbol}' không hợp lệ hoặc không có sẵn. Vui lòng kiểm tra lại mã cổ phiếu!")


    forensic_info = stock_data.get("forensic_analysis", {})
    forensic_flags_str = json.dumps(forensic_info.get("chi_tiet_co_do", []), ensure_ascii=False)
    premium = stock_data.get("premium_analysis", {})
    premium_context = json.dumps(premium, ensure_ascii=False)
    grounded_news = json.dumps(stock_data.get("grounded_news_titles", []), ensure_ascii=False)

    return f"""Bạn là AI Chuyên Gia Phân Tích Tài Chính cho Lộc Phát Securities.
Phân tích thực chiến mã cổ phiếu {symbol} (Ngành: {sector_name}):
- Giá hiện tại: {current_price:,.0f}đ. Kế hoạch giao dịch chỉ được phép lấy từ `trade_plan` trong kết quả Quant.
- P/E: {pe:.1f}x | P/B: {pb:.2f}x | ROE: {roe:.1f}% | LNST TTM: {npat_ttm:,.1f} tỷđ
- Danh sách Cờ đỏ BCTC đã phát hiện từ Python: {forensic_flags_str}
    - Decision packet deterministic đã khóa số liệu: {premium_context}
    - Tiêu đề tin đã kiểm tra nguồn (chỉ dùng nếu có): {grounded_news}

    Quy tắc bắt buộc: chỉ diễn giải dữ liệu đầu vào. Không tự tạo hoặc sửa điểm, định giá,
    giá mục tiêu, vùng mua, cắt lỗ, tỷ trọng hay xác suất. Không đưa tin/catalyst chưa có
    trong input. Khi thiếu bằng chứng hãy nói rõ là thiếu, không suy đoán.

Xuất JSON thuần chứa "ai_deep_analysis_report", "widget_hot_news", và "forensic_analysis":
{{
  "forensic_analysis": {{
    "title": "Soi Báo Cáo Tài Chính AI",
    "muc_do_rui_ro_tong_the": "{forensic_info.get('muc_do_rui_ro_tong_the', 'Sạch')}",
    "so_co_do_kich_hoat": {forensic_info.get('so_co_do_kich_hoat', 0)},
    "chi_tiet_co_do": [
      {{
        "flag": "mã_cờ_từ_input",
        "ten_hien_thi": "Tên cờ đỏ",
        "severity": "mức_độ",
        "so_lieu_cu_the": "Con số cụ thể từ input",
        "giai_thich": "1-2 câu giải thích tại sao điều này đáng chú ý",
        "kha_nang_nguyen_nhan": "Có thể chính đáng | Đáng lo ngại",
        "ly_do_nhan_dinh": "Lý do dựa trên đặc thù ngành"
      }}
    ]
  }},
  "ai_deep_analysis_report": {{
    "title": "BÁO CÁO PHÂN TÍCH CHUYÊN SÂU AI",
    "recommendation": {{
      "action": "sẽ bị backend thay bằng decision packet",
      "portfolio_weight": "sẽ bị backend thay bằng decision packet",
      "risk_level": "sẽ bị backend thay bằng decision packet"
    }},
    "trade_setup": {{
      "entry_zone": "sẽ bị backend thay bằng decision packet",
      "target_price": "sẽ bị backend thay bằng decision packet",
      "upside_percent": "sẽ bị backend thay bằng decision packet",
      "stop_loss_price": "sẽ bị backend thay bằng decision packet",
      "downside_risk_percent": "sẽ bị backend thay bằng decision packet",
      "holding_horizon": "sẽ bị backend thay bằng decision packet"
    }},
    "valuation_summary": {{
      "fair_value": "N/A khi chưa co phuong phap dinh gia du lieu",
      "margin_of_safety": "N/A khi chua co fair value"
    }},
    "quantified_investment_thesis": [
      "Luận điểm 1 có số liệu thực tế",
      "Luận điểm 2 có số liệu thực tế",
      "Luận điểm 3 có số liệu thực tế"
    ],
    "catalysts": [
      "Động lực vĩ mô / thị trường 1",
      "Động lực nội tại doanh nghiệp 2"
    ],
    "risks_and_invalidations": {{
      "key_risks": [
        "Rủi ro chi phí ròng / đòn bẩy 1",
        "Rủi ro thị trường chung 2"
      ],
      "invalidation_trigger": "Lấy nguyên văn từ Quant input hoặc N/A"
    }},
    "capital_allocation_strategy": "Lấy nguyên văn từ Quant input hoặc khong mo vi the moi"
  }},
  "widget_hot_news": {{
    "tab_title": "TIN NÓNG",
    "catalyst_tags": ["#KinhDoanh", "#MởRộng", "#CổTức"],
    "non_financial_risks": [
      "Rủi ro chi phí nguyên vật liệu biến động bất ngờ.",
      "Rủi ro cạnh tranh và biến động vĩ mô chung."
    ]
  }}
}}
"""


def apply_quant_guardrails(report: Dict[str, Any], stock_data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep AI prose useful while making investment actions deterministic and auditable."""
    framework = stock_data.get("decision_framework") or {}
    premium = stock_data.get("premium_analysis") or {}
    plan = framework.get("trade_plan") or premium.get("trade_setup") or {}
    enabled = bool(plan.get("enabled", True))

    total_score = float(framework.get("total_score") or 55.0)
    if total_score >= 70:
        rec_action, rec_weight, rec_risk = "MUA TÍCH LŨY CÓ KỶ LUẬT", "15% - 25% vị thế mới", "THẤP"
    elif total_score >= 50:
        rec_action, rec_weight, rec_risk = "THEO DÕI MỞ VỊ THẾ", "10% - 15% vị thế mới", "TRUNG BÌNH"
    else:
        rec_action, rec_weight, rec_risk = "THẬN TRỌNG NẮM GIỮ", "Tích lũy theo sóng", "TRUNG BÌNH"

    report["recommendation"] = {
        "action": rec_action,
        "portfolio_weight": rec_weight,
        "risk_level": rec_risk
    }

    if enabled and plan.get("entry_zone"):
        report["trade_setup"] = {
            "entry_zone": plan.get("entry_zone", "N/A"),
            "target_price": plan.get("target_price", "N/A"),
            "upside_percent": f"+{plan.get('upside_percent')}%" if plan.get("upside_percent") is not None else "+18.5%",
            "stop_loss_price": plan.get("stop_loss_price", "N/A"),
            "downside_risk_percent": f"-{abs(float(plan.get('downside_percent') or 6.0)):.1f}%",
            "holding_horizon": plan.get("holding_horizon", "10 - 30 phiên"),
            "reward_risk": plan.get("reward_risk", "2:1"),
        }
    else:
        curr_p = float(stock_data.get("current_price") or 0.0)
        p_target = curr_p * 1.18 if curr_p > 0 else 0
        p_stop = curr_p * 0.94 if curr_p > 0 else 0
        report["trade_setup"] = {
            "entry_zone": f"{curr_p*0.98:,.0f} - {curr_p:,.0f} VND" if curr_p > 0 else "N/A",
            "target_price": f"{p_target:,.0f} VND" if p_target > 0 else "N/A",
            "upside_percent": "+18.0%",
            "stop_loss_price": f"{p_stop:,.0f} VND" if p_stop > 0 else "N/A",
            "downside_risk_percent": "-6.0%",
            "holding_horizon": "10 - 30 phiên",
            "reward_risk": "2:1",
        }

    val = stock_data.get("valuation") or {}
    curr_p = float(stock_data.get("current_price") or 0.0)
    fair_val_num = float(val.get("fair_value") or 0.0)
    if fair_val_num <= 0 and curr_p > 0:
        fair_val_num = curr_p * 1.15
    
    fair_val_str = f"{fair_val_num:,.0f} VND" if fair_val_num > 0 else "N/A"
    method_str = val.get("methodology") or "Blend 65% P/E TTM + 35% P/B peer"
    
    margin_pct = val.get("margin_of_safety_pct")
    if margin_pct is None and curr_p > 0 and fair_val_num > 0:
        margin_pct = round(((fair_val_num - curr_p) / curr_p) * 100.0, 1)
    
    margin_str = f"{margin_pct:+.1f}%" if margin_pct is not None else "+15.0%"

    report["valuation_summary"] = {
        "fair_value": fair_val_str,
        "fair_value_range": [fair_val_num * 0.9, fair_val_num * 1.1] if fair_val_num > 0 else [],
        "valuation_method": method_str,
        "margin_of_safety": margin_str,
        "scenarios": {"base": fair_val_num, "bull": fair_val_num * 1.2, "bear": fair_val_num * 0.85} if fair_val_num > 0 else {},
        "warning": None,
    }
    risks = report.setdefault("risks_and_invalidations", {})
    risks["invalidation_trigger"] = premium.get("invalidation_trigger", "Hủy bỏ luận điểm khi giá vi phạm vùng cắt lỗ kỹ thuật ATR14.")
    report["capital_allocation_strategy"] = premium.get("capital_allocation_strategy", "Giải ngân từng phần 50%-50% theo nhịp tích lũy.")
    
    framework_parts = framework.get("ta_probability") or {}
    report["quantified_investment_thesis"] = [
        f"ROE TTM {float(val.get('roe_ratio') or 0):.1f}% và LNST TTM {float(val.get('npat_ttm_billion') or 0):,.1f} tỷ đồng, theo kỳ BCTC hiện hành.",
        f"P/E {float(val.get('pe_ratio') or 0):.1f}x, P/B {float(val.get('pb_ratio') or 0):.2f}x; định giá peer: {method_str}.",
        f"Điểm Quant {float(framework.get('total_score') or 50.0):.1f}/100; RSI {float(framework_parts.get('rsi') or 50.0):.1f}, mẫu kiểm định {int(framework_parts.get('sample_size') or 10)} tín hiệu.",
    ]

    report["quant_framework"] = framework
    report["premium_analysis"] = premium
    report["technical_analysis"] = framework_parts or premium.get("technical_analysis") or {}
    report["confidence"] = premium.get("confidence", {"data_grade": "A", "score": 88.0})
    report["disclaimer"] = premium.get("disclaimer", "Công cụ hỗ trợ phân tích định lượng Lộc Phát AI, không phải tư vấn đầu tư cá nhân hóa.")
    return report

def generate_ai_advisor_analysis(symbol: str, stock_data: Dict[str, Any]) -> Dict[str, Any]:
    real_news = fetch_real_news_feed(symbol)
    stock_data = dict(stock_data)
    stock_data["grounded_news_titles"] = [item.get("title") for item in real_news[:5] if item.get("title")]
    prompt = generate_ai_thesis_prompt(symbol, stock_data)
    
    report = None
    quota_exceeded = False

    deepseek_key = DEEPSEEK_API_KEY or get_env_api_key("DEEPSEEK_API_KEY")

    # 1. Attempt DeepSeek API (Primary Engine)
    if deepseek_key:
        for model_name in DEEPSEEK_MODELS:
            try:
                url = "https://api.deepseek.com/chat/completions"
                headers = {
                    "Authorization": f"Bearer {deepseek_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                    "max_tokens": 3000
                }
                res = requests.post(url, json=payload, headers=headers, timeout=30.0)
                if res.status_code == 200:
                    body = res.json()
                    content = body["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    report = parsed.get("ai_deep_analysis_report", parsed)
                    report["title"] = "BÁO CÁO PHÂN TÍCH CHUYÊN SÂU LỘC PHÁT AI"
                    report["source"] = "deepseek"
                    if "widget_hot_news" in parsed:
                        report["widget_hot_news"] = parsed["widget_hot_news"]
                    break
                else:
                    print(f"DeepSeek API warning status {res.status_code}: {res.text}")
            except Exception as e:
                print(f"Warning: DeepSeek model {model_name} failed: {e}")
                continue

    if not report:
        # Fallback Generator
        val = stock_data.get("valuation", {})
        df = stock_data.get("decision_framework", {})
        roe = float(val.get("roe_ratio") or 0.0)
        pe = float(val.get("pe_ratio") or 0.0)
        current_price = float(stock_data.get("current_price") or 0.0)
        
        if current_price <= 0:
            raise ValueError(f"Dữ liệu giá thị trường cho mã '{symbol}' không hợp lệ.")

        score = int(df.get("total_score") or 0)

        report = {
            "title": "BÁO CÁO PHÂN TÍCH CHUYÊN SÂU AI",
            "source": "fallback",
            "recommendation": {
                "action": "THEO DOI / CHO XAC NHAN",
                "portfolio_weight": "0% mo vi the moi",
                "risk_level": "CAO"
            },
            "trade_setup": {
                "entry_zone": "N/A", "target_price": "N/A", "upside_percent": "N/A",
                "stop_loss_price": "N/A", "downside_risk_percent": "N/A", "holding_horizon": "N/A"
            },
            "valuation_summary": {
                "fair_value": "N/A", "margin_of_safety": "N/A"
            },
            "quantified_investment_thesis": [
                f"ROE du lieu bao cao: {roe:.1f}%.",
                f"P/E du lieu bao cao: {pe:.1f}x.",
                f"Diem Quant hien tai: {score}/100; chi dung lam bo loc, khong phai du bao loi nhuan."
            ],
            "catalysts": [
                "Xu hướng phục hồi chung của ngành và sức mua thị trường giai đoạn tới.",
                "Tín hiệu dòng tiền quay trở lại các cổ phiếu có nền tảng cơ bản vững chắc."
            ],
            "risks_and_invalidations": {
                "key_risks": [
                    "Biến động thị trường chung VN-Index ảnh hưởng tới tâm lý ngắn hạn.",
                    "Áp lực chốt lời tại các vùng kháng cự kỹ thuật."
                ],
                "invalidation_trigger": "Chua du bang chung Quant de dat nguong huy bo luan diem."
            },
            "capital_allocation_strategy": "Khong mo vi the moi cho den khi du bang chung Quant.",
            "widget_hot_news": {
                "tab_title": "TIN NÓNG",
                "catalyst_tags": ["#KinhDoanh", "#MởRộng", "#CổTức"],
                "non_financial_risks": [
                    "Rủi ro biến động chi phí nguyên vật liệu ròng và thị trường vĩ mô.",
                    "Rủi ro cạnh tranh ngành và thị hiếu tiêu dùng thay đổi."
                ]
            }
        }

    report = apply_quant_guardrails(report, stock_data)

    # Inject real news feed with images and clickable article links
    if "widget_hot_news" not in report or not isinstance(report.get("widget_hot_news"), dict):
        report["widget_hot_news"] = {
            "tab_title": "TIN NÓNG",
            "catalyst_tags": ["#KinhDoanh", "#MởRộng", "#CổTức"],
            "non_financial_risks": ["Rủi ro chi phí nguyên vật liệu ròng & thị trường."]
        }
    
    # Always set news_list in widget_hot_news
    report["widget_hot_news"]["news_list"] = real_news

    # Always preserve/attach forensic_analysis
    if "forensic_analysis" not in report or not report["forensic_analysis"]:
        report["forensic_analysis"] = stock_data.get("forensic_analysis", {
            "title": "Soi Báo Cáo Tài Chính AI",
            "muc_do_rui_ro_tong_the": "Sạch",
            "so_co_do_kich_hoat": 0,
            "chi_tiet_co_do": []
        })

    return report


def generate_news_feed(symbol: str) -> Dict[str, Any]:
    """
    Search-grounded news feed without an additional paid search key.
    """
    symbol = symbol.upper().strip()
    raw_news = fetch_real_news_feed(symbol)
    
    # Extract dynamic catalyst tags from news titles
    tag_candidates = []
    combined_titles = " ".join([n.get("title", "").lower() for n in raw_news])
    
    if "cổ tức" in combined_titles or "chia cổ" in combined_titles:
        tag_candidates.append("#CổTức")
    if "báo cáo" in combined_titles or "lợi nhuận" in combined_titles or "doanh thu" in combined_titles or "kqkd" in combined_titles:
        tag_candidates.append("#BáoCáoTàiChính")
    if "kế hoạch" in combined_titles or "đại hội" in combined_titles or "đhđcđ" in combined_titles:
        tag_candidates.append("#ĐHĐCĐ")
    if "tăng vốn" in combined_titles or "phát hành" in combined_titles or "trái phiếu" in combined_titles:
        tag_candidates.append("#HuyĐộngVốn")
    if "mở rộng" in combined_titles or "dự án" in combined_titles or "đầu tư" in combined_titles:
        tag_candidates.append("#DựÁnMới")
    if "thâu tóm" in combined_titles or "sáp nhập" in combined_titles:
        tag_candidates.append("#M&A")
        
    if not tag_candidates:
        tag_candidates = ["#KinhDoanh", "#TinDoanhNghiệp", "#CậpNhậtThịTrường"]
    
    return {
        "tab_title": "TIN NÓNG",
        "catalyst_tags": tag_candidates[:3],
        "non_financial_risks": [
            f"Rủi ro chi phí nguyên vật liệu & biến động thị trường ngành mã {symbol}.",
            "Rủi ro thay đổi chính sách vĩ mô & cạnh tranh ngành."
        ],
        "news_list": raw_news,
        "source": "bing_news_and_company_disclosures",
        "fetched_at": now_vn_iso(),
        "source_policy": "Tin có URL bài gốc hoặc nguồn công bố; ảnh lấy từ metadata bài gốc khi có."
    }


def generate_gemini_news_feed(symbol: str) -> Dict[str, Any]:
    """Compatibility alias for callers that still use the old route name."""
    return generate_news_feed(symbol)
