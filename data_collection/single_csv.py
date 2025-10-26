#!/usr/bin/env python3
"""
kalshi_logger_single_csv.py
- Logs quotes straight from /markets and depth from /orderbook
- Unified CSV (daily/global) with top-of-book and second-best levels:
  Columns:
    timestamp, ticker, title, status, market_type,
    yes_bid, yes_bid2, yes_ask, yes_ask2,
    no_bid,  no_bid2,  no_ask,  no_ask2,
    yes_depth, no_depth,
    liquidity_dollars, volume_24h
"""

import os
import time
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# =============================
# 🔐 Config — EDIT THESE
# =============================
API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"     # optional but recommended
API_DOMAIN = "https://api.elections.kalshi.com"
HEADERS = {"Accept": "application/json"}
if API_KEY and API_KEY != "e8b43912-6603-413c-b544-3ca7f47cd06b":
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUTPUT_DIR = "data_curr"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Unified CSV settings
USE_DAILY_FILE = True
GLOBAL_CSV_PATH = os.path.join(OUTPUT_DIR, "kalshi_snapshots_10/26.csv")

# Discovery/selection knobs
SERIES_TICKERS = ["KXNFLGAME", "KXNBAGAME"]               # e.g. ["KXHIGHNY"]; empty = crawl all (paginated)
PAGINATION_PAGE_LIMIT = 50        # cap pages during listing crawl
MAX_TICKERS = 50                  # per cycle, log up to N tickers
MIN_LIQUIDITY_DOLLARS = 0.0       # filter listing by liquidity floor (coerced)

# Loop timing
POLL_INTERVAL = 15                # seconds between cycles
RUN_DURATION_MINUTES = None         # None for infinite

# =============================
# 📡 API helpers
# =============================
def to_float(x):
    try:
        # Handle strings like "0.6200" or numbers
        return float(x)
    except Exception:
        return None

def calc_spread(bid, ask):
    b = to_float(bid)
    a = to_float(ask)
    return round(a - b, 4) if (a is not None and b is not None) else None

def get_markets_page(cursor=None, series_ticker=None, status=None):
    url = f"{API_DOMAIN}/trade-api/v2/markets"
    params = {}
    if cursor: params["cursor"] = cursor
    if series_ticker: params["series_ticker"] = series_ticker
    if status: params["status"] = status
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    if r.status_code != 200:
        print(f"❌ markets page HTTP {r.status_code}: {r.text[:300]}")
        return None, None
    data = r.json()
    return data.get("markets", []), data.get("cursor")

def get_all_markets():
    all_markets = []
    if SERIES_TICKERS:
        for s in SERIES_TICKERS:
            cursor, pages = None, 0
            while True:
                pages += 1
                markets, cursor = get_markets_page(cursor=cursor, series_ticker=s, status="open")
                if markets is None: break
                all_markets.extend(markets)
                if not cursor or pages >= PAGINATION_PAGE_LIMIT: break
    else:
        cursor, pages = None, 0
        while True:
            pages += 1
            markets, cursor = get_markets_page(cursor=cursor)
            if markets is None: break
            all_markets.extend(markets)
            if not cursor or pages >= PAGINATION_PAGE_LIMIT: break
    return all_markets

def fetch_orderbook(ticker):
    url = f"{API_DOMAIN}/trade-api/v2/markets/{ticker}/orderbook/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(2)
        return None
    except Exception:
        return None

# =============================
# 🔎 Selection (listing-based)
# =============================
def select_top_from_listing(markets, max_n=50, min_liq=0.0):
    if not markets:
        return pd.DataFrame(columns=[])

    df = pd.DataFrame(markets)

    # Ensure relevant columns exist
    cols = [
        "ticker","title","status","market_type",
        "yes_bid_dollars","yes_ask_dollars","no_bid_dollars","no_ask_dollars",
        "liquidity_dollars","volume_24h"
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None

    # Coerce numerics
    df["liquidity_dollars"] = pd.to_numeric(df["liquidity_dollars"], errors="coerce").fillna(0.0)
    df["volume_24h"] = pd.to_numeric(df["volume_24h"], errors="coerce").fillna(0)

    # Quote signal = how many listing bid/ask fields are present
    df["_quote_signal"] = (
        df["yes_bid_dollars"].notna().astype(int) +
        df["yes_ask_dollars"].notna().astype(int) +
        df["no_bid_dollars"].notna().astype(int) +
        df["no_ask_dollars"].notna().astype(int)
    )

    if min_liq > 0:
        df = df[df["liquidity_dollars"] >= float(min_liq)]

    # Sort: quote-signal desc, liquidity desc, 24h volume desc
    df = df.sort_values(by=["_quote_signal","liquidity_dollars","volume_24h"],
                        ascending=[False, False, False])

    # Keep top N with a ticker
    df = df[df["ticker"].notna()].head(max_n).copy()

    # Shape to we keep for loop context (listing best quotes + meta)
    df_out = df[[
        "ticker","title","status","market_type",
        "yes_bid_dollars","yes_ask_dollars","no_bid_dollars","no_ask_dollars",
        "liquidity_dollars","volume_24h"
    ]].reset_index(drop=True)

    return df_out

# =============================
# 🧮 Depth normalization & merge
# =============================
def _levels_from_orderbook(ob):
    """
    Extract ladders as [[price_dollars, qty], ...] for yes/no asks.
    If only cent-based arrays exist, convert to dollars.
    Always returns (yes_levels, no_levels) lists (never None).
    """
    if not isinstance(ob, dict):
        return [], []
    yes = ob.get("yes_dollars")
    no  = ob.get("no_dollars")

    # Fallback: cent-based arrays
    if yes is None and isinstance(ob.get("yes"), list):
        yes = [[round(p/100.0, 4), q] for p, q in ob.get("yes") if isinstance(p, (int,float))]
    if no is None and isinstance(ob.get("no"), list):
        no  = [[round(p/100.0, 4), q] for p, q in ob.get("no")  if isinstance(p, (int,float))]

    yes = yes or []
    no  = no  or []

    # Keep only [price, qty]
    yes = [lvl for lvl in yes if isinstance(lvl, (list, tuple)) and len(lvl) == 2]
    no  = [lvl for lvl in no  if isinstance(lvl, (list, tuple)) and len(lvl) == 2]
    return yes, no

def build_row_from_listing_and_ob(listing_row, ob_json):
    """
    Compose one row with best + second-best using:
      - Listing for best bid/ask (preferred)
      - Orderbook for second-best asks (index 1)
      - Second-best bids approximated as (1 - other_side_ask2) when available
      - Adds yes_spread = yes_ask - yes_bid
             no_spread  = no_ask  - no_bid
    All arithmetic uses float-coerced values to avoid 'str' ops.
    """
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

    # listing fields (coerce to float)
    yes_bid = to_float(listing_row.get("yes_bid_dollars"))
    yes_ask = to_float(listing_row.get("yes_ask_dollars"))
    no_bid  = to_float(listing_row.get("no_bid_dollars"))
    no_ask  = to_float(listing_row.get("no_ask_dollars"))

    # orderbook ladders (assumed asks for each contract side)
    yes_levels, no_levels = _levels_from_orderbook((ob_json or {}).get("orderbook") or {})

    # second-best asks (already numeric from OB, but coerce for safety)
    yes_ask2 = to_float(yes_levels[1][0]) if len(yes_levels) > 1 else None
    no_ask2  = to_float(no_levels[1][0])  if len(no_levels)  > 1 else None

    # Best asks: fallback to OB if listing missing
    if yes_ask is None and yes_levels:
        yes_ask = to_float(yes_levels[0][0])
    if no_ask is None and no_levels:
        no_ask = to_float(no_levels[0][0])

    # Second-best bids: approximate from the other side second-best ask if available
    yes_bid2 = round(1.0 - no_ask2, 4) if (no_ask2 is not None) else None
    no_bid2  = round(1.0 - yes_ask2, 4) if (yes_ask2 is not None) else None

    # Spreads
    yes_spread = calc_spread(yes_bid, yes_ask)
    no_spread  = calc_spread(no_bid,  no_ask)

    row = {
        "timestamp": ts,
        "ticker": listing_row.get("ticker"),
        "title": listing_row.get("title"),
        "status": listing_row.get("status"),
        "market_type": listing_row.get("market_type"),

        "yes_bid": yes_bid,
        "yes_bid2": yes_bid2,
        "yes_ask": yes_ask,
        "yes_ask2": yes_ask2,

        "no_bid": no_bid,
        "no_bid2": no_bid2,
        "no_ask": no_ask,
        "no_ask2": no_ask2,

        "yes_spread": yes_spread,
        "no_spread":  no_spread,

        "yes_depth": len(yes_levels),
        "no_depth": len(no_levels),

        "liquidity_dollars": to_float(listing_row.get("liquidity_dollars")),
        "volume_24h": to_float(listing_row.get("volume_24h")),
    }
    return row



# =============================
# 💾 CSV writer
# =============================
def write_unified_rows(rows):
    if not rows:
        return
    if USE_DAILY_FILE:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(OUTPUT_DIR, f"kalshi_snapshots_listing+ob_{date_str}.csv")
    else:
        path = GLOBAL_CSV_PATH

    df = pd.DataFrame(rows)
    write_header = not os.path.exists(path)
    df.to_csv(path, index=False, mode="a", header=write_header)

# =============================
# ▶️ Main loop
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

            # 1) Pull listing (series-targeted or full crawl)
            markets = get_all_markets()
            if not markets:
                print("⚠️ No markets fetched.")
                time.sleep(POLL_INTERVAL)
                continue

            # 2) Pick top candidates from listing
            listing_df = select_top_from_listing(markets, max_n=MAX_TICKERS, min_liq=MIN_LIQUIDITY_DOLLARS)
            if listing_df.empty:
                print("↩️ No rows to write this cycle (no listing quotes met filters).")
                time.sleep(POLL_INTERVAL)
                continue

            # 3) For each, fetch orderbook once to get second-best asks
            rows = []
            for _, m in listing_df.iterrows():
                ticker = m["ticker"]
                ob = fetch_orderbook(ticker)
                row = build_row_from_listing_and_ob(m, ob)
                rows.append(row)

            # 4) Write batch to unified CSV
            write_unified_rows(rows)
            print(f"💾 Wrote {len(rows)} rows to unified CSV.")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopped by user.")

if __name__ == "__main__":
    main()
