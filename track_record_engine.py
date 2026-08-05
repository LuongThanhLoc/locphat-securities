# Track Record Engine: Ghi lại & Tự động chấm điểm mọi khuyến nghị AI Advisor
import os
import sqlite3
import json
import re
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sector_mapping import get_sector_info

DB_PATH = os.path.join(os.path.dirname(__file__), "track_record.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS track_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            timestamp_created TEXT NOT NULL,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            entry_zone TEXT,
            target_price TEXT,
            stop_loss_price TEXT,
            holding_horizon TEXT,
            price_at_creation REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'DANG_THEO_DOI',
            current_price REAL,
            actual_return_percent REAL,
            timestamp_updated TEXT,
            sector_name TEXT
        )
    """)
    conn.commit()
    conn.close()

# Helpers for parsing strings into numbers
def parse_price_value(val_str) -> float:
    if val_str is None:
        return 0.0
    if isinstance(val_str, (int, float)):
        return float(val_str)
    
    s = str(val_str).replace("VNĐ", "").replace("đ", "").replace("VND", "").strip()
    # Find all numbers with dots or commas
    # e.g., "120.000 - 125.000" -> pick first or target
    matches = re.findall(r'[\d\.\,]+', s)
    if not matches:
        return 0.0
    
    num_s = matches[0]
    # Standardize format: if contains dot as thousand separator e.g. 135.000 or 135,000
    if '.' in num_s and ',' in num_s:
        num_s = num_s.replace('.', '').replace(',', '.')
    elif '.' in num_s:
        # Check if dot is thousand separator (e.g. 135.000) or decimal (135.5)
        parts = num_s.split('.')
        if len(parts[-1]) == 3 and len(parts) > 1:
            num_s = num_s.replace('.', '')
    elif ',' in num_s:
        parts = num_s.split(',')
        if len(parts[-1]) == 3 and len(parts) > 1:
            num_s = num_s.replace(',', '')
        else:
            num_s = num_s.replace(',', '.')

    try:
        val = float(num_s)
        # If price is in thousands (e.g. 135 -> 135000), adjust if price scale requires
        return val
    except Exception:
        return 0.0

def parse_holding_horizon_days(horizon_str: str) -> int:
    if not horizon_str:
        return 180  # Default 6 months
    s = str(horizon_str).lower()
    
    numbers = [int(n) for n in re.findall(r'\d+', s)]
    max_num = max(numbers) if numbers else 6
    
    if 'ngày' in s or 'day' in s:
        return max_num
    elif 'tháng' in s or 'month' in s:
        return max_num * 30
    elif 'năm' in s or 'year' in s:
        return max_num * 365
    return max_num * 30

def save_recommendation(ticker: str, report_data: Dict[str, Any], stock_data: Dict[str, Any]):
    """
    Saves a recommendation record into SQLite DB.
    Guarantees no modifications to original trade setup values.
    """
    try:
        init_db()
        ticker_sym = ticker.upper().strip()
        timestamp_created = datetime.now().isoformat()
        
        # Source must come from ai_advisor_engine report ("deepseek" or "fallback")
        source = report_data.get("source", "deepseek")
        
        rec = report_data.get("recommendation", {})
        action = rec.get("action", "MUA TÍCH LŨY")
        
        setup = report_data.get("trade_setup", {})
        entry_zone = str(setup.get("entry_zone") or "")
        target_price = str(setup.get("target_price") or "")
        stop_loss_price = str(setup.get("stop_loss_price") or "")
        holding_horizon = str(setup.get("holding_horizon") or "3 - 6 tháng")
        
        # Price at creation from real-time stock data
        price_at_creation = float(stock_data.get("current_price") or 0.0)
        
        sector_info = get_sector_info(ticker_sym)
        sector_name = stock_data.get("sector_name") or sector_info.get("sector", "KHÁC")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO track_records (
                ticker, timestamp_created, source, action, entry_zone, target_price,
                stop_loss_price, holding_horizon, price_at_creation, status,
                current_price, actual_return_percent, timestamp_updated, sector_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DANG_THEO_DOI', ?, 0.0, ?, ?)
        """, (
            ticker_sym, timestamp_created, source, action, entry_zone, target_price,
            stop_loss_price, holding_horizon, price_at_creation, price_at_creation,
            timestamp_created, sector_name
        ))
        conn.commit()
        conn.close()
        print(f"✅ Track Record saved successfully for {ticker_sym} (Source: {source})")
    except Exception as e:
        print(f"Error saving track record for {ticker}: {e}")

def save_recommendation_async(ticker: str, report_data: Dict[str, Any], stock_data: Dict[str, Any]):
    """
    Fire-and-forget background thread execution so HTTP response time is never delayed.
    """
    thread = threading.Thread(target=save_recommendation, args=(ticker, report_data, stock_data), daemon=True)
    thread.start()

_last_update_time = None

def update_all_open_records_async(force: bool = False):
    """
    Triggers update_all_open_records in a background daemon thread if not updated recently.
    Does not block the HTTP caller.
    """
    global _last_update_time
    now = datetime.now()
    if not force and _last_update_time and (now - _last_update_time).total_seconds() < 300:
        return
    
    _last_update_time = now
    thread = threading.Thread(target=update_all_open_records, daemon=True)
    thread.start()

def update_all_open_records():
    """
    Updates all open records using the direct market-data adapter.
    Evaluates:
    1. current_price >= target_price -> DAT_MUC_TIEU
    2. current_price <= stop_loss_price -> CHAM_CAT_LO
    3. elapsed days >= holding_horizon_days -> HET_HAN_KHONG_DAT
    """
    init_db()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM track_records WHERE status = 'DANG_THEO_DOI'")
        open_rows = cursor.fetchall()
        
        if not open_rows:
            return

        now = datetime.now()
        
        # Prefer DNSE latest trade; VCI OHLC remains an explicit fallback.
        try:
            from market_data_provider import Quote
        except Exception:
            Quote = None
        try:
            from dnse_realtime import get_dnse_latest_price_snapshot
        except Exception:
            get_dnse_latest_price_snapshot = None

        for row in open_rows:
            record_id = row["id"]
            ticker = row["ticker"]
            created_dt = datetime.fromisoformat(row["timestamp_created"])
            price_at_creation = float(row["price_at_creation"] or 0.0)
            
            target_val = parse_price_value(row["target_price"])
            stop_loss_val = parse_price_value(row["stop_loss_price"])
            horizon_days = parse_holding_horizon_days(row["holding_horizon"])
            
            # Fetch current price from DNSE, then fall back to latest daily close.
            curr_price = price_at_creation
            if get_dnse_latest_price_snapshot:
                try:
                    snapshot = get_dnse_latest_price_snapshot(ticker)
                    latest_dnse = float(snapshot.get("price_vnd") or 0.0)
                    if latest_dnse > 0:
                        curr_price = latest_dnse
                except Exception as e:
                    print(f"Warning updating DNSE price for {ticker}: {e}")
            if curr_price == price_at_creation and Quote:
                try:
                    today_str = now.strftime('%Y-%m-%d')
                    start_str = (now - timedelta(days=7)).strftime('%Y-%m-%d')
                    q_df = Quote(symbol=ticker, source='VCI').history(start=start_str, end=today_str)
                    if not q_df.empty and 'close' in q_df.columns:
                        latest_c = float(q_df.iloc[-1]['close'])
                        if latest_c > 0:
                            curr_price = latest_c * 1000.0 if latest_c < 1000.0 else latest_c
                except Exception as e:
                    print(f"Warning updating price for {ticker}: {e}")

            # Compute actual return %
            if price_at_creation > 0:
                actual_return = round(((curr_price - price_at_creation) / price_at_creation) * 100.0, 2)
            else:
                actual_return = 0.0
                
            new_status = "DANG_THEO_DOI"
            days_elapsed = (now - created_dt).days
            
            if target_val > 0 and curr_price >= target_val:
                new_status = "DAT_MUC_TIEU"
            elif stop_loss_val > 0 and curr_price <= stop_loss_val:
                new_status = "CHAM_CAT_LO"
            elif days_elapsed >= horizon_days:
                new_status = "HET_HAN_KHONG_DAT"
                
            cursor.execute("""
                UPDATE track_records
                SET current_price = ?, actual_return_percent = ?, status = ?, timestamp_updated = ?
                WHERE id = ?
            """, (curr_price, actual_return, new_status, now.isoformat(), record_id))

        conn.commit()
    except Exception as e:
        print(f"Error in update_all_open_records: {e}")
    finally:
        conn.close()

def get_track_record_stats() -> Dict[str, Any]:
    """
    Calculates summary statistics:
    - tong_so_khuyen_nghi
    - ty_le_thang (% DAT_MUC_TIEU over finished records)
    - loi_nhuan_trung_binh (average return % over finished records)
    - phan_bo_theo_nganh (grouped stats by sector)
    """
    init_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM track_records")
    total_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT * FROM track_records WHERE status IN ('DAT_MUC_TIEU', 'CHAM_CAT_LO', 'HET_HAN_KHONG_DAT')")
    finished_rows = cursor.fetchall()
    finished_count = len(finished_rows)
    
    win_count = sum(1 for r in finished_rows if r["status"] == "DAT_MUC_TIEU")
    
    if finished_count > 0:
        win_rate = round((win_count / finished_count) * 100.0, 1)
        avg_return = round(sum(float(r["actual_return_percent"] or 0) for r in finished_rows) / finished_count, 2)
    else:
        win_rate = 0.0
        avg_return = 0.0

    cursor.execute("SELECT COUNT(*) FROM track_records WHERE source != 'fallback'")
    deepseek_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM track_records WHERE source = 'fallback'")
    fallback_count = cursor.fetchone()[0]

    # Sector breakdown
    cursor.execute("SELECT sector_name, status, actual_return_percent FROM track_records")
    all_rows = cursor.fetchall()
    
    sector_map = {}
    for r in all_rows:
        sec = r["sector_name"] or "KHÁC"
        if sec not in sector_map:
            sector_map[sec] = {"total": 0, "finished": 0, "wins": 0, "returns": []}
        sector_map[sec]["total"] += 1
        if r["status"] in ('DAT_MUC_TIEU', 'CHAM_CAT_LO', 'HET_HAN_KHONG_DAT'):
            sector_map[sec]["finished"] += 1
            if r["status"] == "DAT_MUC_TIEU":
                sector_map[sec]["wins"] += 1
            sector_map[sec]["returns"].append(float(r["actual_return_percent"] or 0))

    phan_bo_theo_nganh = []
    for sec, data in sector_map.items():
        fin = data["finished"]
        s_win_rate = round((data["wins"] / fin) * 100.0, 1) if fin > 0 else 0.0
        s_avg_return = round(sum(data["returns"]) / fin, 2) if fin > 0 else 0.0
        phan_bo_theo_nganh.append({
            "sector_name": sec,
            "tong_so": data["total"],
            "da_ket_thuc": fin,
            "ty_le_thang": s_win_rate,
            "loi_nhuan_trung_binh": s_avg_return
        })
        
    conn.close()

    return {
        "tong_so_khuyen_nghi": total_count,
        "da_ket_thuc": finished_count,
        "so_win": win_count,
        "ty_le_thang": win_rate,
        "loi_nhuan_trung_binh": avg_return,
        "deepseek_count": deepseek_count,
        "gemini_count": 0,
        "fallback_count": fallback_count,
        "phan_bo_theo_nganh": phan_bo_theo_nganh
    }

def get_track_records(ticker: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns filtered track records list for API / UI.
    """
    init_db()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM track_records WHERE 1=1"
    params = []
    
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker.upper().strip())
    if status:
        query += " AND status = ?"
        params.append(status.strip())
        
    query += " ORDER BY id DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "ticker": r["ticker"],
            "timestamp_created": r["timestamp_created"],
            "source": r["source"],
            "action": r["action"],
            "entry_zone": r["entry_zone"],
            "target_price": r["target_price"],
            "stop_loss_price": r["stop_loss_price"],
            "holding_horizon": r["holding_horizon"],
            "price_at_creation": r["price_at_creation"],
            "status": r["status"],
            "current_price": r["current_price"],
            "actual_return_percent": r["actual_return_percent"],
            "timestamp_updated": r["timestamp_updated"],
            "sector_name": r["sector_name"]
        })
        
    conn.close()
    return result
