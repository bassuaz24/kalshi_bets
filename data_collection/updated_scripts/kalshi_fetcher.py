import os
import time
import json
import re
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from wakepy import keep
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter

# --- Configuration (Same as before) ---
API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"
API_DOMAIN = "https://api.elections.kalshi.com"
ORDERBOOK_WORKERS = 12
HEADERS = {"Accept": "application/json"}
if API_KEY and API_KEY != "e8b43912-6603-413c-b544-3ca7f47cd06b":
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

def _build_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = HTTPAdapter(pool_connections=ORDERBOOK_WORKERS, pool_maxsize=ORDERBOOK_WORKERS + 4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

SESSION = _build_session()

OUTPUT_DIR = "kalshi_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
local_tz = pytz.timezone("US/Eastern")

BASE_SERIES_TICKERS = [
    "KXNFLGAME", "KXNBAGAME", "KXNCAAFGAME", "KXNCAAMBGAME", "KXNCAAWBGAME",
    "KXNFLTOTAL", "KXNFLSPREAD", "KXNBATOTAL","KXNBASPREAD", 
    "KXNCAAFTOTAL", "KXNCAAFSPREAD", "KXNCAAMBTOTAL", "KXNCAAMBSPREAD",
    
]
SERIES_TICKERS = BASE_SERIES_TICKERS 

SERIES_TO_FILENAME = {
    "KXNFLGAME": "nfl_winners.csv",
    "KXNBAGAME": "nba_winners.csv",
    "KXNCAAFGAME": "ncaaf_winners.csv",
    "KXNCAAMBGAME": "ncaabm_winners.csv",
    "KXNCAAWBGAME": "ncaabw_winners.csv",
    "KXNFLTOTAL": "nfl_totals.csv",
    "KXNFLSPREAD": "nfl_spreads.csv",
    "KXNBATOTAL": "nba_totals.csv",
    "KXNBASPREAD": "nba_spreads.csv",
    "KXNCAAFTOTAL": "ncaaf_totals.csv",
    "KXNCAAFSPREAD": "ncaaf_spreads.csv",
"KXNCAAMBTOTAL": "ncaabm_totals.csv",
"KXNCAAMBSPREAD": "ncaabm_spreads.csv"
}
TICKER_DATE_RE = re.compile(r"-(\d{2}[A-Z]{3}\d{2})")

PAGINATION_PAGE_LIMIT = 50        
MAX_TICKERS = None                  
MIN_LIQUIDITY_DOLLARS = 0.0       
POLL_INTERVAL = 15                
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
    r = SESSION.get(url, params=params, timeout=20)
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
def fetch_orderbook(ticker, retries=3, backoff=0.4):
    url = f"{API_DOMAIN}/trade-api/v2/markets/{ticker}/orderbook/"
    session = SESSION
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=12)
            if r.status_code == 200: return r.json()
            if r.status_code == 429:
                time.sleep(backoff * (attempt + 1))
                continue
            if 500 <= r.status_code < 600:
                time.sleep(backoff * (attempt + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(backoff * (attempt + 1))
    return None

# =============================
# 🔎 Selection (Same as before)
# =============================
def select_relevant_markets(markets: list) -> pd.DataFrame:
    if not markets: return pd.DataFrame(columns=[])

    df = pd.DataFrame(markets)
    df["liquidity_dollars"] = pd.to_numeric(df.get("liquidity_dollars"), errors="coerce").fillna(0.0)
    df["volume_24h"] = pd.to_numeric(df.get("volume_24h"), errors="coerce").fillna(0)
    df = df[df["status"] == "active"] 

    def parse_market_time(ts):
        if not ts: return None
        if isinstance(ts, datetime): dt = ts
        else:
            try: dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except Exception: return None
        if dt.tzinfo is None: dt = pytz.utc.localize(dt)
        return dt.astimezone(local_tz)

    def get_market_datetime(row):
        for key in ("event_expiration_time", "close_time", "expiry"):
            dt = parse_market_time(row.get(key))
            if dt: return dt
        return None

    def infer_event_date(row):
        ticker = (row.get("ticker") or "").upper()
        m = TICKER_DATE_RE.search(ticker)
        if m:
            try: return datetime.strptime(m.group(1), "%y%b%d").date()
            except ValueError: pass
        evt_dt = row.get("event_time")
        if isinstance(evt_dt, datetime): return evt_dt.date()
        for key in ("event_expiration_time", "close_time", "expiry"):
            dt = parse_market_time(row.get(key))
            if dt: return dt.date()
        return None

    df["event_time"] = df.apply(get_market_datetime, axis=1)
    df["event_date"] = df.apply(infer_event_date, axis=1)
    df = df[df["event_date"].notna()]

    now_local = datetime.now(local_tz)
    target_date = now_local.date()
    df = df[df["event_date"] == target_date]
    df = df[df["liquidity_dollars"] >= MIN_LIQUIDITY_DOLLARS]

    print(f"📅 Local time now: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🕒 Selecting markets scheduled for {target_date.isoformat()} (local).")
    
    keep_cols = [
        "ticker", "title", "status", "market_type", "yes_bid_dollars", "yes_ask_dollars",
        "no_bid_dollars", "no_ask_dollars", "liquidity_dollars", "volume_24h", "event_time", "event_date"
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

def build_rows_concurrently(listing_df):
    if listing_df.empty: return []
    records = list(enumerate(listing_df.to_dict("records")))
    workers = min(ORDERBOOK_WORKERS, len(records)) or 1
    results = []

    def _fetch(idx, record):
        ob = fetch_orderbook(record.get("ticker"))
        return idx, build_row_from_listing_and_ob(record, ob)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(_fetch, idx, record): (idx, record) for idx, record in records}
        for future in as_completed(future_map):
            idx, record = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                ticker = record.get("ticker")
                print(f"⚠️ Failed to fetch/orderbook for {ticker}: {exc}")

    results.sort(key=lambda item: item[0])
    return [row for _, row in results if row is not None]

# =============================
# 💾 Write Function (Date Fix Applied)
# =============================
def _write_csv(df, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as csvfile:
        df.to_csv(csvfile, index=False)

def _resolve_filename(series_ticker: str) -> str:
    """
    Ensure KXNCAAMB* markets always write to ncaabm_* files regardless of the
    caller that triggered the fetch (prevents duplicate ncaab_* outputs).
    """
    default_name = SERIES_TO_FILENAME.get(series_ticker, f"{series_ticker.lower()}.csv")
    if not series_ticker:
        return default_name

    normalized = series_ticker.upper()
    if normalized.startswith("KXNCAAMB"):
        if "TOTAL" in normalized:
            return "ncaabm_totals.csv"
        if "SPREAD" in normalized:
            return "ncaabm_spreads.csv"
        return "ncaabm_winners.csv"

    return default_name


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
        filename = _resolve_filename(series_ticker)
        path = os.path.join(dated_dir, filename)
        df = pd.DataFrame(rows_list)
        _write_csv(df, path)


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
            target_df = listing_df.head(MAX_TICKERS) if MAX_TICKERS else listing_df
            rows = build_rows_concurrently(target_df)

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
