#!/usr/bin/env python3
"""
kalshi_logger_listing_csv.py
- Logs quotes straight from the /markets listing (no /orderbook dependency)
- Fields recorded:
  timestamp, ticker, yes_ask, no_ask, yes_bid, no_bid, yes_prob, no_prob,
  liquidity_dollars, volume_24h, status, market_type, title
- Writes ALL snapshots into ONE CSV (daily or global).
"""

import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# ========= Config (edit) =========
API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"      # optional but recommended
API_DOMAIN = "https://api.elections.kalshi.com"
HEADERS = {"Accept": "application/json"}
if API_KEY and API_KEY != "e8b43912-6603-413c-b544-3ca7f47cd06b":
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUTPUT_DIR = "kalshi_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
USE_DAILY_FILE = True
GLOBAL_CSV_PATH = os.path.join(OUTPUT_DIR, "kalshi_snapshots_from_listing.csv")

# Discovery/selection knobs
SERIES_TICKERS = []               # e.g. ["KXHIGHNY"]; empty = crawl all (paginated)
PAGINATION_PAGE_LIMIT = 50        # max pages when crawling all markets
MAX_TICKERS = 50                  # per cycle, log up to N most active by quote signal + liquidity
MIN_LIQUIDITY_DOLLARS = 0.0       # numeric filter; set >0 to force liquidity floor

# Loop timing
POLL_INTERVAL = 15                # seconds between cycles
RUN_DURATION_MINUTES = 2          # None for infinite

# ========= API helpers =========
def get_markets_page(cursor=None, series_ticker=None, status=None):
    url = f"{API_DOMAIN}/trade-api/v2/markets"
    params = {}
    if cursor:
        params["cursor"] = cursor
    if series_ticker:
        params["series_ticker"] = series_ticker
    if status:
        params["status"] = status  # e.g. "open"
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    if r.status_code != 200:
        print(f"❌ markets page HTTP {r.status_code}: {r.text[:300]}")
        return None, None
    data = r.json()
    return data.get("markets", []), data.get("cursor")

def get_all_markets():
    all_markets = []
    if SERIES_TICKERS:
        # series-targeted fetches (faster, cleaner)
        for s in SERIES_TICKERS:
            cursor = None
            pages = 0
            while True:
                pages += 1
                markets, cursor = get_markets_page(cursor=cursor, series_ticker=s, status="open")
                if markets is None:
                    break
                all_markets.extend(markets)
                if not cursor or pages >= PAGINATION_PAGE_LIMIT:
                    break
    else:
        # full crawl
        cursor = None
        pages = 0
        while True:
            pages += 1
            markets, cursor = get_markets_page(cursor=cursor)
            if markets is None:
                break
            all_markets.extend(markets)
            if not cursor or pages >= PAGINATION_PAGE_LIMIT:
                break
    return all_markets

# ========= Selection from listing =========
def select_top_by_listing(markets, max_n=50, min_liq=0.0):
    """Rank by quote signal (presence of any bid/ask) then liquidity_dollars then volume_24h."""
    if not markets:
        return pd.DataFrame(columns=[])

    df = pd.DataFrame(markets)

    # Ensure columns exist
    for col in ["yes_bid_dollars","yes_ask_dollars","no_bid_dollars","no_ask_dollars",
                "liquidity_dollars","volume_24h","status","market_type","title","ticker",
                "yes_price","no_price"]:
        if col not in df.columns:
            df[col] = None

    # Coerce numerics
    df["liquidity_dollars"] = pd.to_numeric(df["liquidity_dollars"], errors="coerce").fillna(0.0)
    df["volume_24h"] = pd.to_numeric(df["volume_24h"], errors="coerce").fillna(0)

    # Quote signal = how many quote fields are non-null on listing
    df["_quote_signal"] = (
        df["yes_bid_dollars"].notna().astype(int) +
        df["yes_ask_dollars"].notna().astype(int) +
        df["no_bid_dollars"].notna().astype(int) +
        df["no_ask_dollars"].notna().astype(int)
    )

    # Filter by liquidity if desired
    if min_liq > 0:
        df = df[df["liquidity_dollars"] >= float(min_liq)]

    # Sort: quote signal desc, liquidity desc, 24h volume desc
    df = df.sort_values(by=["_quote_signal","liquidity_dollars","volume_24h"],
                        ascending=[False, False, False])

    # Keep top N with a ticker
    df = df[df["ticker"].notna()].head(max_n).copy()

    # Compute probabilities from yes_price/no_price (cents) if present
    def cents_to_prob(x):
        try:
            return float(x) / 100.0
        except Exception:
            return None

    df["yes_prob"] = df["yes_price"].apply(cents_to_prob)
    df["no_prob"]  = df["no_price"].apply(cents_to_prob)

    # Rename/shape to the columns we want to log
    df_out = df.rename(columns={
        "yes_ask_dollars": "yes_ask",
        "no_ask_dollars":  "no_ask",
        "yes_bid_dollars": "yes_bid",
        "no_bid_dollars":  "no_bid",
    })[[
        "ticker", "title", "status", "market_type",
        "yes_ask", "no_ask", "yes_bid", "no_bid",
        "yes_prob", "no_prob",
        "liquidity_dollars", "volume_24h"
    ]]

    return df_out.reset_index(drop=True)

# ========= CSV writer =========
def write_unified_rows(df_rows):
    if df_rows is None or df_rows.empty:
        return
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    df_rows = df_rows.copy()
    df_rows.insert(0, "timestamp", ts)  # add snapshot timestamp to all rows

    if USE_DAILY_FILE:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(OUTPUT_DIR, f"kalshi_snapshots_listing_{date_str}.csv")
    else:
        path = GLOBAL_CSV_PATH

    write_header = not os.path.exists(path)
    df_rows.to_csv(path, index=False, mode="a", header=write_header)

# ========= Main loop =========
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

            markets = get_all_markets()
            if not markets:
                print("⚠️ No markets fetched.")
                time.sleep(POLL_INTERVAL)
                continue

            df_rows = select_top_by_listing(markets, max_n=MAX_TICKERS, min_liq=MIN_LIQUIDITY_DOLLARS)
            if df_rows.empty:
                print("↩️ No rows to write this cycle (no listing quotes met filters).")
            else:
                write_unified_rows(df_rows)
                print(f"💾 Wrote {len(df_rows)} rows to unified CSV.")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopped by user.")

if __name__ == "__main__":
    main()
