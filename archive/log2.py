import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# =============================
# 🔐 Config — EDIT THESE
# =============================
API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"  # optional but recommended
API_DOMAIN = "https://api.elections.kalshi.com"
HEADERS = {"Accept": "application/json"}
if API_KEY and API_KEY != "e8b43912-6603-413c-b544-3ca7f47cd06b":
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUTPUT_DIR = "kalshi_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Single CSV settings
USE_DAILY_FILE = True  # True = one CSV per day; False = single global file
GLOBAL_CSV_PATH = os.path.join(OUTPUT_DIR, "kalshi_snapshots1.csv")

# Discovery & logging knobs
PAGINATION_PAGE_LIMIT = 200    # max pages to crawl during discovery
VALIDATE_LIMIT = 300           # max tickers to hit /orderbook for validation
MAX_TICKERS = 25               # final number of tickers to log after filtering
MIN_LIQUIDITY_DOLLARS = 0.0    # ignore markets below this liquidity (coerced)
REQUIRE_NONEMPTY_OB = True     # only log if at least one level exists on YES/NO

POLL_INTERVAL = 15             # seconds between cycles
SPACING_BETWEEN_TICKERS = 0.4  # small delay between per-ticker requests
RUN_DURATION_MINUTES = 2      # set None for infinite

# Periodic re-discovery (helps rotate into newly active markets)
REDISCOVER_EVERY_CYCLES = 5   # set to 0/None to disable

# Quote enrichment:
USE_MARKET_QUOTE = True        # if True, fetch /markets/{ticker} for yes_bid/no_bid/yes_price/no_price
QUOTE_BACKOFF_SECONDS = 2      # simple backoff when quote endpoint throttles


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
            time.sleep(2)  # backoff
        return None
    except Exception:
        return None


def fetch_market_quote(ticker):
    """
    Fetches current top-of-book quote & probabilities from markets API.
    Expected fields (if provided by API):
      yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars,
      yes_price (cents), no_price (cents)
    """
    if not USE_MARKET_QUOTE:
        return None
    url = f"{API_DOMAIN}/trade-api/v2/markets/{ticker}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()  # { "market": { ...fields... } } or sometimes just fields
        if r.status_code == 429:
            time.sleep(QUOTE_BACKOFF_SECONDS)
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
    all_markets = get_all_markets()
    summarize_markets(all_markets)
    df = pd.DataFrame(all_markets)

    # Coerce liquidity to numeric (often string)
    if "liquidity_dollars" in df.columns:
        df["liquidity_dollars"] = pd.to_numeric(df["liquidity_dollars"], errors="coerce").fillna(0.0)
    else:
        df["liquidity_dollars"] = 0.0

    # Strong signal that a market has activity: listing already shows bid/ask
    for col in ["yes_bid_dollars","yes_ask_dollars","no_bid_dollars","no_ask_dollars"]:
        if col not in df.columns:
            df[col] = None

    df["_quote_signal"] = (
        df["yes_bid_dollars"].notna().astype(int) +
        df["yes_ask_dollars"].notna().astype(int) +
        df["no_bid_dollars"].notna().astype(int) +
        df["no_ask_dollars"].notna().astype(int)
    )

    # Also factor recent trading/volume if present
    if "volume_24h" in df.columns:
        df["volume_24h"] = pd.to_numeric(df["volume_24h"], errors="coerce").fillna(0)
    else:
        df["volume_24h"] = 0

    # Rank: (quote signal first) then liquidity, then 24h volume
    df = df.sort_values(
        by=["_quote_signal", "liquidity_dollars", "volume_24h"],
        ascending=[False, False, False]
    )

    # Take a generous pool then prune later in logging if truly empty
    tickers = [t for t in df["ticker"].dropna().astype(str).head(MAX_TICKERS * 3).tolist()]
    # Deduplicate
    tickers = list(dict.fromkeys(tickers))

    print(f"📝 Candidate tickers by quote-signal/liquidity: {len(tickers)}")
    print(tickers[:20])

    # Optional: quick quote probe to keep only those with any live signal
    kept = []
    for t in tickers:
        q = fetch_market_quote(t)
        m = (q or {}).get("market", (q or {}))
        if any(m.get(k) is not None for k in ["yes_bid_dollars","yes_ask_dollars","no_bid_dollars","no_ask_dollars","yes_price","no_price"]):
            kept.append(t)
        if len(kept) >= MAX_TICKERS:
            break

    print(f"✅ Final ticker list ({len(kept)}): {kept}")
    return kept



# =============================
# 💾 Logging (single CSV writer)
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

    yes = [lvl for lvl in yes if isinstance(lvl, (list, tuple)) and len(lvl) == 2]
    no  = [lvl for lvl in no  if isinstance(lvl, (list, tuple)) and len(lvl) == 2]
    return yes, no


def _extract_quote_fields(quote_json):
    """
    Extract yes_bid/ask, no_bid/ask and yes_prob/no_prob from markets quote JSON.
    Supports either { "market": {...} } or flat {...}.
    Returns dict with keys: yes_bid, yes_ask, no_bid, no_ask, yes_prob, no_prob
    Missing fields are set to None.
    """
    if not quote_json:
        return {k: None for k in ["yes_bid", "yes_ask", "no_bid", "no_ask", "yes_prob", "no_prob"]}

    m = quote_json.get("market", quote_json)

    yes_bid = m.get("yes_bid_dollars")
    yes_ask = m.get("yes_ask_dollars")
    no_bid  = m.get("no_bid_dollars")
    no_ask  = m.get("no_ask_dollars")

    # yes_price/no_price in CENTS (if present)
    yp_cents = m.get("yes_price")
    np_cents = m.get("no_price")
    yes_prob = (yp_cents / 100.0) if isinstance(yp_cents, (int, float)) else None
    no_prob  = (np_cents / 100.0) if isinstance(np_cents, (int, float)) else None

    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid":  no_bid,
        "no_ask":  no_ask,
        "yes_prob": yes_prob,
        "no_prob":  no_prob,
    }


def flatten_snapshot(raw_orderbook, ticker, market_quote=None):
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ob = (raw_orderbook or {}).get("orderbook") or {}
    yes_levels, no_levels = _as_levels_dollars(ob)

    # From orderbook ladders (assume arrays are asks for that contract)
    ob_yes_ask = yes_levels[0][0] if yes_levels else None
    ob_no_ask  = no_levels[0][0]  if no_levels else None

    # From markets quote endpoint (preferred if available)
    q = _extract_quote_fields(market_quote) if market_quote else _extract_quote_fields(None)

    # Merge logic:
    yes_ask = q["yes_ask"] if q["yes_ask"] is not None else ob_yes_ask
    no_ask  = q["no_ask"]  if q["no_ask"]  is not None else ob_no_ask

    # Bids: prefer direct from quote; otherwise approximate from opposite ask
    yes_bid = q["yes_bid"]
    if yes_bid is None and no_ask is not None:
        yes_bid = round(max(0.0, min(1.0, 1.0 - no_ask)), 4)

    no_bid = q["no_bid"]
    if no_bid is None and yes_ask is not None:
        no_bid = round(max(0.0, min(1.0, 1.0 - yes_ask)), 4)

    # Probabilities: prefer quote yes_price/no_price; otherwise mid from asks if available
    yes_prob = q["yes_prob"]
    no_prob  = q["no_prob"]

    if yes_prob is None and (yes_ask is not None or no_ask is not None):
        # Approximate YES probability using mid of YES ask and complement of NO ask
        # If only one side available, just use it
        comp_no = (1.0 - no_ask) if no_ask is not None else None
        if comp_no is not None and yes_ask is not None:
            yes_prob = round((yes_ask + comp_no) / 2.0, 4)
        else:
            yes_prob = yes_ask if yes_ask is not None else (1.0 - no_ask if no_ask is not None else None)

    if no_prob is None and yes_prob is not None:
        no_prob = round(1.0 - yes_prob, 4)

    return {
        "timestamp": ts,
        "ticker": ticker,
        "yes_ask": yes_ask,
        "no_ask":  no_ask,
        "yes_bid": yes_bid,
        "no_bid":  no_bid,
        "yes_depth": len(yes_levels),
        "no_depth":  len(no_levels),
        "yes_prob": yes_prob,
        "no_prob":  no_prob,
    }


def write_rows_unified(rows):
    """Append rows (list of dicts) to a single CSV (daily or global)."""
    if not rows:
        return
    if USE_DAILY_FILE:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(OUTPUT_DIR, f"kalshi_snapshots_{date_str}.csv")
    else:
        path = GLOBAL_CSV_PATH

    df = pd.DataFrame(rows)
    write_header = not os.path.exists(path)
    df.to_csv(path, index=False, mode="a", header=write_header)


def log_once_return_row(ticker):
    """Fetch → quotes → flatten → return row (or None if empty/unavailable)."""
    try:
        ob = fetch_orderbook(ticker)
        if not (ob and ob.get("orderbook")):
            return None

        quote = fetch_market_quote(ticker) if USE_MARKET_QUOTE else None

        row = flatten_snapshot(ob, ticker, market_quote=quote)
        if REQUIRE_NONEMPTY_OB and row["yes_depth"] == 0 and row["no_depth"] == 0:
            return None
        return row
    except Exception:
        return None


# =============================
# ▶️ Main
# =============================
def discover_and_select_tickers():
    all_markets = get_all_markets()
    summarize_markets(all_markets)

    ranked = top_by_liquidity(
        markets=all_markets,
        top_n=MAX_TICKERS * 4,
        min_liq=MIN_LIQUIDITY_DOLLARS
    )
    tickers = validate_nonempty_ob(ranked, limit=VALIDATE_LIMIT)
    tickers = tickers[:MAX_TICKERS]
    print(f"📝 Final ticker list ({len(tickers)}): {tickers}")
    return tickers


def main():
    tickers = discover_and_select_tickers()
    if not tickers:
        print("❌ No tickers to log. Try lowering filters or running later.")
        return

    print(f"📝 Logging {len(tickers)} tickers into a single CSV…")

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

            batch_rows = []
            for t in tickers:
                row = log_once_return_row(t)
                if row:
                    batch_rows.append(row)
                time.sleep(SPACING_BETWEEN_TICKERS)

            if batch_rows:
                write_rows_unified(batch_rows)
                print(f"💾 Wrote {len(batch_rows)} rows to unified CSV.")
            else:
                print("↩️ No rows to write this cycle (likely empty orderbooks).")

            if REDISCOVER_EVERY_CYCLES and cycle % REDISCOVER_EVERY_CYCLES == 0:
                print("🔄 Periodic re-discovery…")
                new_list = discover_and_select_tickers()
                if new_list:
                    tickers = new_list

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopped by user.")


if __name__ == "__main__":
    main()
