#!/usr/bin/env python3
"""
kalshi_logger_full.py
- Auto-discovers markets from the Kalshi elections API (paginated)
- Ranks by liquidity, validates via /orderbook
- Logs multiple tickers on a schedule to per-ticker daily CSVs
- Skips empty orderbooks (configurable) to avoid wasting disk
"""

import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# =============================
# 🔐 Config — EDIT THESE
# =============================
API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"  # optional for public reads; recommended to avoid rate limits
API_DOMAIN = "https://api.elections.kalshi.com"
HEADERS = {"Accept": "application/json"}
if API_KEY and API_KEY != "e8b43912-6603-413c-b544-3ca7f47cd06b":
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUTPUT_DIR = "kalshi_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Discovery knobs
PAGINATION_PAGE_LIMIT = 200    # max pages to crawl during discovery
VALIDATE_LIMIT = 400           # max tickers to hit /orderbook for validation
MAX_TICKERS = 25               # final number of tickers to log after filtering
MIN_LIQUIDITY_DOLLARS = 100.0    # ignore markets below this liquidity (string fields are coerced)
REQUIRE_NONEMPTY_OB = True     # True: only log if at least one level exists on YES/NO

# Run behavior
POLL_INTERVAL = 15             # seconds between cycles
SPACING_BETWEEN_TICKERS = 0.4  # small delay between per-ticker requests
RUN_DURATION_MINUTES = 1      # finite run while testing; set to None for infinite

# Periodic re-discovery (helps rotate into newly active markets)
REDISCOVER_EVERY_CYCLES = 20   # set to 0/None to disable


# =============================
# 📡 API helpers
# =============================
def get_markets_page(cursor=None):
    url = f"{API_DOMAIN}/trade-api/v2/markets"
    params = {}
    if cursor:
        params["cursor"] = cursor
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    if r.status_code != 200:
        print(f"❌ markets page HTTP {r.status_code}: {r.text[:300]}")
        return None, None
    data = r.json()
    return data.get("markets", []), data.get("cursor")


def get_all_markets():
    """Fetch all markets via cursor pagination."""
    all_markets, cursor, pages = [], None, 0
    while True:
        pages += 1
        markets, cursor = get_markets_page(cursor)
        if markets is None:
            break
        all_markets.extend(markets)
        if not cursor or pages >= PAGINATION_PAGE_LIMIT:
            break
    print(f"🔍 Downloaded {len(all_markets)} markets across {pages} page(s).")
    return all_markets


def fetch_orderbook(ticker):
    url = f"{API_DOMAIN}/trade-api/v2/markets/{ticker}/orderbook/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            # simple backoff on rate limit
            time.sleep(2)
        return None
    except Exception:
        return None


# =============================
# 🔎 Discovery, Ranking, Validation
# =============================
def summarize_markets(markets, max_rows=5):
    if not markets:
        print("⚠️ No markets to summarize.")
        return
    df = pd.DataFrame(markets)
    print(f"🧾 Columns: {sorted(df.columns.tolist())}")
    for col in ["status", "market_type", "category"]:
        if col in df.columns:
            print(f"📊 {col} distribution:")
            print(df[col].value_counts(dropna=False).head(15).to_string())
    print("\n👀 Sample rows:")
    keep = [c for c in ["ticker", "title", "status", "market_type", "liquidity_dollars", "yes_price", "no_price", "volume"] if c in df.columns]
    print((df[keep].head(max_rows) if keep else df.head(max_rows)).to_string(index=False))


def top_by_liquidity(markets, top_n=50, min_liq=0.0):
    """Return top tickers by liquidity_dollars (coerced to float)."""
    df = pd.DataFrame(markets)
    if "ticker" not in df.columns:
        return []
    if "liquidity_dollars" in df.columns:
        df["liquidity_dollars"] = pd.to_numeric(df["liquidity_dollars"], errors="coerce").fillna(0.0)
        df = df[df["liquidity_dollars"] >= float(min_liq)]
        df = df.sort_values("liquidity_dollars", ascending=False)
        tickers = [t for t in df["ticker"].head(top_n).tolist() if isinstance(t, str)]
        print(f"🏆 Selected {len(tickers)} by liquidity (min_liq={min_liq}).")
        return tickers
    # Fallback: no liquidity field; just take first page subset
    uniq = [m["ticker"] for m in markets if "ticker" in m]
    print(f"🏆 No liquidity field; selected first {min(top_n, len(uniq))} tickers.")
    return uniq[:top_n]


def validate_nonempty_ob(tickers, limit=200):
    """Return only tickers whose /orderbook has at least one level on YES or NO."""
    valid = []
    tested = 0
    for t in tickers:
        ob = fetch_orderbook(t)
        if ob and isinstance(ob, dict) and ob.get("orderbook"):
            yes = (ob["orderbook"].get("yes_dollars")
                   or ob["orderbook"].get("yes")
                   or [])
            no = (ob["orderbook"].get("no_dollars")
                  or ob["orderbook"].get("no")
                  or [])
            if yes or no:
                valid.append(t)
        tested += 1
        if tested >= limit:
            break
    print(f"🧪 Non-empty OB: {len(valid)} / {tested} tickers")
    return valid


def discover_and_select_tickers():
    """Full discovery flow with ranking and validation."""
    all_markets = get_all_markets()
    summarize_markets(all_markets)

    # Rank by liquidity (bigger lists then pruned by validation)
    ranked = top_by_liquidity(
        markets=all_markets,
        top_n=MAX_TICKERS * 4,  # oversample, we’ll prune later
        min_liq=MIN_LIQUIDITY_DOLLARS
    )

    # Validate have non-empty OB
    tickers = validate_nonempty_ob(ranked, limit=VALIDATE_LIMIT)

    # Final cap
    tickers = tickers[:MAX_TICKERS]
    print(f"📝 Final ticker list ({len(tickers)}): {tickers}")
    return tickers


# =============================
# 💾 Logging
# =============================
def _as_levels_dollars(ob):
    """
    Normalize orderbook levels to [[price_dollars, qty], ...].
    Accepts *_dollars arrays or cent-based arrays, coerces None -> [].
    """
    if not isinstance(ob, dict):
        return [], []
    yes = ob.get("yes_dollars")
    no = ob.get("no_dollars")

    # fallback: cent-based arrays
    if yes is None and isinstance(ob.get("yes"), list):
        yes = [[round(p / 100.0, 4), q] for p, q in ob.get("yes") if isinstance(p, (int, float))]
    if no is None and isinstance(ob.get("no"), list):
        no = [[round(p / 100.0, 4), q] for p, q in ob.get("no") if isinstance(p, (int, float))]

    yes = yes or []
    no = no or []

    # keep only [price, qty] pairs
    yes = [lvl for lvl in yes if isinstance(lvl, (list, tuple)) and len(lvl) == 2]
    no = [lvl for lvl in no if isinstance(lvl, (list, tuple)) and len(lvl) == 2]
    return yes, no


def flatten_snapshot(raw, ticker):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ob = (raw or {}).get("orderbook") or {}
    yes_levels, no_levels = _as_levels_dollars(ob)
    return {
        "timestamp": ts,
        "ticker": ticker,
        "best_yes": yes_levels[0][0] if yes_levels else None,
        "best_no":  no_levels[0][0]  if no_levels else None,
        "yes_depth": len(yes_levels),
        "no_depth":  len(no_levels),
        "raw_yes": json.dumps(yes_levels),
        "raw_no":  json.dumps(no_levels),
    }


def save_snapshot(snapshot, ticker):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname = f"{OUTPUT_DIR}/{ticker.replace('.', '_')}_{date_str}.csv"
    df = pd.DataFrame([snapshot])
    if not os.path.exists(fname):
        df.to_csv(fname, index=False)
    else:
        df.to_csv(fname, mode="a", header=False, index=False)


def log_once(ticker):
    try:
        raw = fetch_orderbook(ticker)
        if not (raw and raw.get("orderbook")):
            print(f"⚠️ [{ticker}] empty/unavailable orderbook.")
            return False
        snap = flatten_snapshot(raw, ticker)
        if REQUIRE_NONEMPTY_OB and snap["yes_depth"] == 0 and snap["no_depth"] == 0:
            print(f"↩️ [{ticker}] skipped (empty OB).")
            return False
        save_snapshot(snap, ticker)
        print(f"✅ [{ticker}] {snap['timestamp']} | YES: {snap['best_yes']} | NO: {snap['best_no']} | depth Y/N: {snap['yes_depth']}/{snap['no_depth']}")
        return True
    except Exception as e:
        print(f"⚠️ [{ticker}] logging error: {e}")
        return False


# =============================
# ▶️ Main
# =============================
def main():
    # Initial discovery
    tickers = discover_and_select_tickers()
    if not tickers:
        print("❌ No tickers to log. Try lowering filters or running later.")
        return

    print(f"📝 Logging {len(tickers)} tickers…")

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
            cycle_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            print(f"\n⏱️ Cycle {cycle} @ {cycle_ts}")

            for t in tickers:
                log_once(t)
                time.sleep(SPACING_BETWEEN_TICKERS)

            # Periodic re-discovery to keep list fresh
            if REDISCOVER_EVERY_CYCLES and cycle % REDISCOVER_EVERY_CYCLES == 0:
                print("🔄 Periodic re-discovery…")
                tickers = discover_and_select_tickers() or tickers

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopped by user.")


if __name__ == "__main__":
    main()
