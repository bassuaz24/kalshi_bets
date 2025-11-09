import os
import time
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from wakepy import keep
import pytz

# --- Configuration (Same as before) ---
API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"
API_DOMAIN = "https://api.elections.kalshi.com"
HEADERS = {"Accept": "application/json"}
if API_KEY and API_KEY != "e8b43912-6603-413c-b544-3ca7f47cd06b":
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUTPUT_DIR = "kalshi_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
local_tz = pytz.timezone("US/Eastern")

BASE_SERIES_TICKERS = [
    "KXNFLGAME", "KXNBAGAME", "KXNCAAFGAME", "KXNCAAMBGAME", "KXATPMATCH", 
    "KXEPLGAME", "KXUELGAME", "KXNFLTOTAL", "KXNFLSPREAD", "KXNBATOTAL",
    "KXNBASPREAD", "KXNCAAFTOTAL", "KXNCAAFSPREAD", "KXNCAAMBTOTAL", 
    "KXNCAAMBSPREAD"
]
SERIES_TICKERS = BASE_SERIES_TICKERS 

SERIES_TO_FILENAME = {
    "KXNFLGAME": "nfl_winners.csv",
    "KXNBAGAME": "nba_winners.csv",
    "KXNCAAFGAME": "ncaaf_winners.csv",
    "KXNCAAMBGAME": "ncaab_winners.csv",
    "KXATPMATCH": "tennis.csv",
    "KXEPLGAME": "epl.csv",
    "KXUELGAME": "uel.csv",
    "KXNFLTOTAL": "nfl_totals.csv",
    "KXNFLSPREAD": "nfl_spreads.csv",
    "KXNBATOTAL": "nba_totals.csv",
    "KXNBASPREAD": "nba_spreads.csv",
    "KXNCAAFTOTAL": "ncaaf_totals.csv",
    "KXNCAAFSPREAD": "ncaaf_spreads.csv",
    "KXNCAAMBTOTAL": "ncaab_totals.csv",
    "KXNCAAMBSPREAD": "ncaab_spreads.csv"
}

PAGINATION_PAGE_LIMIT = 50        
MAX_TICKERS = None                  
MIN_LIQUIDITY_DOLLARS = 0.0       
POLL_INTERVAL = 60                
RUN_DURATION_MINUTES = None         

# =============================
# 📡 API helpers (Skipped for brevity, same as previous final script)
# =============================
# ... (functions to_float, calc_spread, get_markets_page, get_all_markets, fetch_orderbook remain the same) ...

# --- Placeholder definitions for required functions to ensure the script runs ---
def to_float(x):
    try: return float(x)
    except Exception: return None
def calc_spread(bid, ask):
    b = to_float(bid); a = to_float(ask)
    return round(a - b, 4) if (a is not None and b is not None) else None
def get_markets_page(cursor=None, series_ticker=None, status=None):
    url = f"{API_DOMAIN}/trade-api/v2/markets"
    params = {}
    if cursor: params["cursor"] = cursor
    if series_ticker: params["series_ticker"] = series_ticker
    if status: params["status"] = status
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    if r.status_code != 200: return None, None
    data = r.json()
    return data.get("markets", []), data.get("cursor")
def get_all_markets():
    all_markets = []
    if SERIES_TICKERS:
        for s in SERIES_TICKERS:
            cursor, pages = None, 0
            while True:
                pages += 1
                markets, cursor = get_markets_page(cursor=cursor, series_ticker=s) 
                if markets is None: break
                all_markets.extend(markets)
                if not cursor or pages >= PAGINATION_PAGE_LIMIT: break
    return all_markets
def fetch_orderbook(ticker):
    url = f"{API_DOMAIN}/trade-api/v2/markets/{ticker}/orderbook/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200: return r.json()
        if r.status_code == 429: time.sleep(2)
        return None
    except Exception: return None

# =============================
# 🔎 Selection (Same as before)
# =============================
def select_relevant_markets(markets: list) -> pd.DataFrame:
    if not markets: return pd.DataFrame(columns=[])

    df = pd.DataFrame(markets)
    df["liquidity_dollars"] = pd.to_numeric(df.get("liquidity_dollars"), errors="coerce").fillna(0.0)
    df["volume_24h"] = pd.to_numeric(df.get("volume_24h"), errors="coerce").fillna(0)
    df = df[df["status"] == "active"] 

    def get_market_datetime(row):
        ts = row.get("close_time")
        try: return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(local_tz)
        except Exception: return None

    df["event_time"] = df.apply(get_market_datetime, axis=1)
    df = df[df["event_time"].notna()] 

    now_local = datetime.now(local_tz)
    time_window_from_now = now_local + timedelta(days=30) 
    
    is_not_started = (df["event_time"] > now_local)
    is_closing_soon = (df["event_time"] <= time_window_from_now)
    
    df = df[is_not_started & is_closing_soon]
    df = df[df["liquidity_dollars"] >= MIN_LIQUIDITY_DOLLARS]

    print(f"📅 Local time now: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🕒 Selecting markets closing between now and {time_window_from_now.strftime('%Y-%m-%d')} (30-day window).")
    
    keep_cols = [
        "ticker", "title", "status", "market_type", "yes_bid_dollars", "yes_ask_dollars",
        "no_bid_dollars", "no_ask_dollars", "liquidity_dollars", "volume_24h", "event_time" 
    ]
    return df[keep_cols].reset_index(drop=True)

# =============================
# 🧮 Depth normalization & merge (Skipped for brevity, same as previous final script)
# =============================
# ... (functions _levels_from_orderbook, build_row_from_listing_and_ob remain the same) ...
def _levels_from_orderbook(ob):
    if not isinstance(ob, dict): return [], [], [], []
    yes_bids = ob.get("yes_bids_dollars") or []
    yes_asks = ob.get("yes_asks_dollars") or []
    no_bids = ob.get("no_bids_dollars") or []
    no_asks = ob.get("no_asks_dollars") or []
    def filter_levels(levels):
        if not isinstance(levels, list): return []
        return [lvl for lvl in levels if isinstance(lvl, (list, tuple)) and len(lvl) == 2]
    return (filter_levels(yes_bids), filter_levels(yes_asks), filter_levels(no_bids), filter_levels(no_asks))

def build_row_from_listing_and_ob(listing_row, ob_json):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    yes_bid = to_float(listing_row.get("yes_bid_dollars")); yes_ask = to_float(listing_row.get("yes_ask_dollars"))
    no_bid = to_float(listing_row.get("no_bid_dollars")); no_ask = to_float(listing_row.get("no_ask_dollars"))
    event_time_str = listing_row.get("event_time").isoformat() if listing_row.get("event_time") is not None else None
    yes_bids_levels, yes_asks_levels, no_bids_levels, no_asks_levels = _levels_from_orderbook((ob_json or {}).get("orderbook") or {})
    yes_bid2 = to_float(yes_bids_levels[1][0]) if len(yes_bids_levels) > 1 else None; no_bid2 = to_float(no_bids_levels[1][0]) if len(no_bids_levels) > 1 else None
    yes_ask2 = to_float(yes_asks_levels[1][0]) if len(yes_asks_levels) > 1 else None; no_ask2 = to_float(no_asks_levels[1][0]) if len(no_asks_levels) > 1 else None
    if yes_ask is None and yes_asks_levels: yes_ask = to_float(yes_asks_levels[0][0])
    if no_ask is None and no_asks_levels: no_ask = to_float(no_asks_levels[0][0])
    if yes_bid is None and yes_bids_levels: yes_bid = to_float(yes_bids_levels[0][0])
    if no_bid is None and no_bids_levels: no_bid = to_float(no_bids_levels[0][0])
    yes_spread = calc_spread(yes_bid, yes_ask); no_spread = calc_spread(no_bid, no_ask)
    row = {
        "timestamp": ts, "ticker": listing_row.get("ticker"), "title": listing_row.get("title"), "status": listing_row.get("status"),
        "market_type": listing_row.get("market_type"), "event_start_time": event_time_str,
        "yes_bid": yes_bid, "yes_bid2": yes_bid2, "yes_ask": yes_ask, "yes_ask2": yes_ask2,
        "no_bid": no_bid, "no_bid2": no_bid2, "no_ask": no_ask, "no_ask2": no_ask2,
        "yes_spread": yes_spread, "no_spread": no_spread,
        "yes_depth_bids": len(yes_bids_levels), "yes_depth_asks": len(yes_asks_levels),
        "no_depth_bids": len(no_bids_levels), "no_depth_asks": len(no_asks_levels),
        "liquidity_dollars": to_float(listing_row.get("liquidity_dollars")), "volume_24h": to_float(listing_row.get("volume_24h")),
    }
    return row

# =============================
# 💾 Write Function (Date Fix Applied)
# =============================
def write_rows_by_series(rows):
    if not rows: return

    # --- FIX: Use local_tz (US/Eastern) for the folder date ---
    now_local = datetime.now(local_tz)
    date_str = now_local.strftime("%Y-%m-%d")
    # --------------------------------------------------------
    
    dated_dir = os.path.join(OUTPUT_DIR, date_str)
    os.makedirs(dated_dir, exist_ok=True)

    by_series = {}
    for row in rows:
        ticker = row.get("ticker", "")
        series = next((k for k in SERIES_TO_FILENAME if ticker.startswith(k)), "unknown")
        by_series.setdefault(series, []).append(row)

    for series_ticker, rows_list in by_series.items():
        filename = SERIES_TO_FILENAME.get(series_ticker, f"{series_ticker.lower()}.csv")
        path = os.path.join(dated_dir, filename)
        df = pd.DataFrame(rows_list)
        write_header = not os.path.exists(path)
        df.to_csv(path, index=False, mode="a", header=write_header)


# =============================
# ▶️ Main loop (No changes needed)
# =============================
def main():
    end_time = None
    if RUN_DURATION_MINUTES:
        end_time = datetime.now(timezone.utc) + timedelta(minutes=RUN_DURATION_MINUTES)

    cycle = 0
    try:
        while True:
            if end_time and datetime.now(timezone.utc) >= end_time:
                print("⏹️ Reached run duration. Exiting.")
                break

            cycle += 1
            ts = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
            print(f"\n⏱️ Cycle {cycle} @ {ts}")

            # 1) Pull listing
            all_markets = get_all_markets()
            
            # Diagnostic Print: Full status of fetched markets
            if all_markets:
                df_full = pd.DataFrame(all_markets)
                print(f"✅ Markets fetched: {len(all_markets)}")
                print(f"✅ Unique statuses:\n{df_full['status'].value_counts(dropna=False)}")
                print(f"✅ Unique market types:\n{df_full['market_type'].value_counts(dropna=False)}") # KEY DIAGNOSTIC
            if not all_markets:
                print("⚠️ No markets fetched.")
                time.sleep(POLL_INTERVAL); continue

            # 2) Filter down to relevant, near-term active markets
            listing_df = select_relevant_markets(all_markets)
            print(f"✅ Selected {len(listing_df)} relevant markets.")
            if listing_df.empty:
                print("↩️ No rows to write this cycle (no active markets remaining after 30-day filter).")
                time.sleep(POLL_INTERVAL); continue
            
            print("\n✅ Top 10 Selected Active Markets (30-day window):")
            print(listing_df.head(10)[["ticker", "title", "market_type", "event_time"]].to_string(index=False))

            # 3) For each, fetch orderbook once to get second-best asks
            rows = []
            for i, m in listing_df.head(MAX_TICKERS).iterrows() if MAX_TICKERS else listing_df.iterrows():
                ticker = m["ticker"]
                ob = fetch_orderbook(ticker)
                row = build_row_from_listing_and_ob(m, ob)
                rows.append(row)

            # 4) Write batch to unified CSV
            write_rows_by_series(rows)
            
            # Use local time for final message confirmation
            current_local_date = datetime.now(local_tz).strftime("%Y-%m-%d")
            print(f"💾 Wrote {len(rows)} rows to CSVs in folder {os.path.join(OUTPUT_DIR, current_local_date)}.")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopped by user.")

if __name__ == "__main__":
    main()