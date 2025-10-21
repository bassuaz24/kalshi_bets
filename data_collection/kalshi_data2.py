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
USE_DAILY_FILE = False
GLOBAL_CSV_PATH = os.path.join(OUTPUT_DIR, "kalshi_snapshots2.csv")

# Discovery/selection knobs
SERIES_TICKERS = ["KXNBA"]               # e.g. ["KXHIGHNY"]; empty = crawl all (paginated)
PAGINATION_PAGE_LIMIT = 50        # cap pages during listing crawl
MAX_TICKERS = 50                  # per cycle, log up to N tickers
MIN_LIQUIDITY_DOLLARS = 0.0       # filter listing by liquidity floor (coerced)

# Loop timing
POLL_INTERVAL = 15                # seconds between cycles
RUN_DURATION_MINUTES = 3         # None for infinite

# =============================
# 📡 API helpers
# =============================
def to_float(x):
    try:
        return float(x)
    except Exception:
        return None

def _normalize_levels_to_dollars(ob_side):
    """
    Given [[price, qty], ...] where price may be str/float,
    coerce price→float and keep only valid rows.
    """
    out = []
    for lvl in ob_side or []:
        if not isinstance(lvl, (list, tuple)) or len(lvl) != 2:
            continue
        p = to_float(lvl[0])
        q = to_float(lvl[1])
        if p is None:
            continue
        out.append([p, q])
    return out

def _levels_from_orderbook(orderbook):
    """
    Extract YES/NO ask ladders from the orderbook dict with strong normalization.
    Priority:
      - *_dollars arrays (already dollars)
      - else cent-based arrays (*_cents) -> dollars (/100)
    Return two lists sorted ascending by price (best ask first), de-duplicated and range-checked.
    """
    if not isinstance(orderbook, dict):
        return [], []

    yes_levels = []
    no_levels  = []

    yes_dollars = orderbook.get("yes_dollars")
    no_dollars  = orderbook.get("no_dollars")

    if isinstance(yes_dollars, list):
        yes_levels = _normalize_levels_to_dollars(yes_dollars)
    else:
        yes_cents = orderbook.get("yes")
        if isinstance(yes_cents, list):
            tmp = []
            for lvl in yes_cents:
                if isinstance(lvl, (list, tuple)) and len(lvl) == 2:
                    p_c = to_float(lvl[0])
                    q   = to_float(lvl[1])
                    if p_c is None:
                        continue
                    tmp.append([round(p_c/100.0, 4), q])
            yes_levels = tmp

    if isinstance(no_dollars, list):
        no_levels = _normalize_levels_to_dollars(no_dollars)
    else:
        no_cents = orderbook.get("no")
        if isinstance(no_cents, list):
            tmp = []
            for lvl in no_cents:
                if isinstance(lvl, (list, tuple)) and len(lvl) == 2:
                    p_c = to_float(lvl[0])
                    q   = to_float(lvl[1])
                    if p_c is None:
                        continue
                    tmp.append([round(p_c/100.0, 4), q])
            no_levels = tmp

    # Clean: keep 0.00..1.00, drop Nones, dedupe by price, sort asc
    def clean_sort(levels):
        seen = set()
        cleaned = []
        for p, q in levels:
            if p is None:
                continue
            if p < 0.0 or p > 1.0:
                continue
            # dedupe by price to avoid repeating the same price across events
            key = round(p, 4)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append([key, q])
        return sorted(cleaned, key=lambda x: x[0])

    yes_levels = clean_sort(yes_levels)
    no_levels  = clean_sort(no_levels)

    return yes_levels, no_levels


def derive_bids_from_opposite_asks(opp_asks_sorted):
    """
    Given opposite side asks (sorted asc), derive same-side bids via 1 - price.
    Returns bids sorted DESC (best first).
    """
    bids = []
    for lvl in opp_asks_sorted:
        p = to_float(lvl[0])
        if p is not None:
            bids.append(round(1.0 - p, 4))
    # highest bid first
    return sorted(bids, reverse=True)

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

from datetime import datetime, timezone

RECORD_TOP5 = False  # set True to audit ask ladders into columns yes_ask1..5/no_ask1..5

def calc_spread(bid, ask):
    b = to_float(bid); a = to_float(ask)
    return round(a - b, 4) if (a is not None and b is not None) else None

def build_row_from_listing_and_ob(listing_row, ob_json):
    """
    Best bids/asks:
      - BEST bids/asks from listing (authoritative)
      - SECOND-BEST asks from orderbook (ask index 1 after strong normalization/sort)
      - No synthetic bid2 (removed)
      - Sanity: if OB ask1 diverges from listing best ask by > 0.05, trust listing and drop ask2.
    """
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

    # Listing best (authoritative)
    yes_bid_list = to_float(listing_row.get("yes_bid_dollars"))
    yes_ask_list = to_float(listing_row.get("yes_ask_dollars"))
    no_bid_list  = to_float(listing_row.get("no_bid_dollars"))
    no_ask_list  = to_float(listing_row.get("no_ask_dollars"))

    # Orderbook ladders (asks)
    orderbook = (ob_json or {}).get("orderbook") or {}
    yes_asks, no_asks = _levels_from_orderbook(orderbook)

    # OB best asks (may be None if empty)
    yes_ask_ob1 = yes_asks[0][0] if len(yes_asks) > 0 else None
    no_ask_ob1  = no_asks[0][0]  if len(no_asks)  > 0 else None

    # OB second-best asks
    yes_ask_ob2 = yes_asks[1][0] if len(yes_asks) > 1 else None
    no_ask_ob2  = no_asks[1][0]  if len(no_asks)  > 1 else None

    # Final best asks: listing if present, else OB
    yes_ask = yes_ask_list if yes_ask_list is not None else yes_ask_ob1
    no_ask  = no_ask_list  if no_ask_list  is not None else no_ask_ob1

    # Guard: second-best must be >= best and > best by at least one tick ideally
    def sane_second(best, second):
        b = to_float(best); s = to_float(second)
        if b is None or s is None:
            return None
        if s < b:
            return None
        return s

    # If OB best disagree w/ listing too much, drop second to avoid wrong ladders
    def big_diverge(a, b, tol=0.05):
        if a is None or b is None: return False
        return abs(a - b) > tol

    yes_ask2 = sane_second(yes_ask, yes_ask_ob2)
    no_ask2  = sane_second(no_ask,  no_ask_ob2)

    if big_diverge(yes_ask, yes_ask_ob1):
        yes_ask2 = None
    if big_diverge(no_ask, no_ask_ob1):
        no_ask2 = None

    # Best bids (from listing only)
    yes_bid = yes_bid_list
    no_bid  = no_bid_list

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
        "yes_ask": yes_ask,
        "yes_ask2": yes_ask2,

        "no_bid": no_bid,
        "no_ask": no_ask,
        "no_ask2": no_ask2,

        "yes_spread": yes_spread,
        "no_spread":  no_spread,

        "yes_depth": len(yes_asks),
        "no_depth": len(no_asks),

        "liquidity_dollars": to_float(listing_row.get("liquidity_dollars")),
        "volume_24h": to_float(listing_row.get("volume_24h")),
    }

    if RECORD_TOP5:
        # Add top-5 asks for auditing (temporary)
        for i in range(5):
            row[f"yes_ask{i+1}"] = yes_asks[i][0] if len(yes_asks) > i else None
            row[f"no_ask{i+1}"]  = no_asks[i][0]  if len(no_asks)  > i else None

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
