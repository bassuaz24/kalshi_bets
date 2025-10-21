import requests
import pandas as pd
import time
import os
import json
from datetime import datetime

# =============================
# 🔐 Config
# =============================
API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"  # optional for public reads, but recommended
API_DOMAIN = "https://api.elections.kalshi.com"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"} if API_KEY else {"Accept": "application/json"}

OUTPUT_DIR = "kalshi_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Discovery knobs
SERIES_TICKERS = []   # ← put known series tickers here; leave empty [] to skip
ONLY_ACTIVE = True              # status=open
INCLUDE_NON_BINARY = True       # keep flexible
KEYWORDS = None                 # e.g. ["football","usc","texas"]; None = no keyword filter
MIN_VOLUME = 1                  # require at least N contracts traded (as reported by /markets)
REQUIRE_PRICE = True            # require yes_price present
PAGINATION_PAGE_LIMIT = 50      # cap pages when crawling all markets
VALIDATE_LIMIT = 50             # cap orderbook validations per cycle
POLL_INTERVAL = 30              # seconds between logging cycles
SPACING_BETWEEN_TICKERS = 0.5   # seconds spacing per ticker within a cycle

# =============================
# 📡 API helpers
# =============================
def get_series_markets(series_ticker, status="open"):
    url = f"{API_DOMAIN}/trade-api/v2/markets"
    params = {}
    if series_ticker: params["series_ticker"] = series_ticker
    if status: params["status"] = status
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if r.status_code != 200:
        print(f"❌ series markets HTTP {r.status_code}: {r.text}")
        return []
    return r.json().get("markets", [])

def get_markets_page(cursor=None):
    url = f"{API_DOMAIN}/trade-api/v2/markets"
    params = {}
    if cursor: params["cursor"] = cursor
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if r.status_code != 200:
        print(f"❌ markets page HTTP {r.status_code}: {r.text}")
        return None, None
    data = r.json()
    return data.get("markets", []), data.get("cursor")

def get_all_markets():
    all_markets, cursor, pages = [], None, 0
    while True:
        pages += 1
        markets, cursor = get_markets_page(cursor)
        if markets is None: break
        all_markets.extend(markets)
        if not cursor or pages >= PAGINATION_PAGE_LIMIT: break
    print(f"🔍 Downloaded {len(all_markets)} markets across {pages} page(s).")
    return all_markets

def fetch_event(event_ticker):
    url = f"{API_DOMAIN}/trade-api/v2/events/{event_ticker}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    return r.json() if r.status_code == 200 else None

def fetch_orderbook(ticker):
    url = f"{API_DOMAIN}/trade-api/v2/markets/{ticker}/orderbook/"
    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code == 200:
        return r.json()
    return None

# =============================
# 🔎 Discovery & filtering
# =============================
def is_binary_like(m):
    if m.get("market_type") == "binary":
        return True
    # If API returns contracts/options arrays, infer binary if exactly 2
    contracts = m.get("contracts") or m.get("options")
    if isinstance(contracts, list) and len(contracts) == 2:
        return True
    return False

def title_matches(m, keywords):
    if not keywords: return True
    title = (m.get("title") or "").lower()
    return any(kw.lower() in title for kw in keywords)

def filter_markets(markets):
    filtered = []
    for m in markets:
        if ONLY_ACTIVE and m.get("status") != "open":
            continue
        if not INCLUDE_NON_BINARY and not is_binary_like(m):
            continue
        if KEYWORDS and not title_matches(m, KEYWORDS):
            continue
        # volume and price checks (these fields may be present in elections API)
        if REQUIRE_PRICE and m.get("yes_price") is None and m.get("no_price") is None:
            continue
        vol = m.get("volume")
        if MIN_VOLUME and (vol is None or vol < MIN_VOLUME):
            continue
        filtered.append(m)
    print(f"✅ Filtered to {len(filtered)} market(s) after rules.")
    return filtered

def discover_markets():
    discovered = []

    # 1) Prefer series-specific discovery if provided
    for series in SERIES_TICKERS:
        s_markets = get_series_markets(series_ticker=series, status="open" if ONLY_ACTIVE else None)
        print(f"🧭 Series {series}: found {len(s_markets)}")
        discovered.extend(s_markets)

    # 2) Fallback: full crawl if nothing found via series
    if not discovered:
        print("↪️ Falling back to full market crawl…")
        discovered = get_all_markets()

    # Filter & dedupe by ticker
    filtered = filter_markets(discovered)
    uniq = {m["ticker"]: m for m in filtered if "ticker" in m}
    print(f"🗂️ Unique candidates: {len(uniq)}")
    return list(uniq.values())

def validate_tickers(markets, limit=VALIDATE_LIMIT):
    tickers, tested = [], 0
    for m in markets:
        t = m.get("ticker")
        if not t: continue
        tested += 1
        ob = fetch_orderbook(t)
        if ob and isinstance(ob, dict) and ob.get("orderbook") is not None:
            tickers.append(t)
        if tested >= limit: break
    print(f"🧪 Validated {len(tickers)} ticker(s) (of {tested} tested).")
    return tickers

# =============================
# 💾 Logging
# =============================
def flatten_snapshot(raw, ticker):
    ts = datetime.utcnow().isoformat() + "Z"
    ob = raw.get("orderbook") or {}
    yes = ob.get("yes_dollars", [])
    no = ob.get("no_dollars", [])
    best_yes = yes[0][0] if yes else None
    best_no = no[0][0] if no else None
    yes_depth = len(yes)
    no_depth = len(no)
    return {
        "timestamp": ts,
        "ticker": ticker,
        "best_yes": best_yes,
        "best_no": best_no,
        "yes_depth": yes_depth,
        "no_depth": no_depth,
        "raw_yes": json.dumps(yes),
        "raw_no": json.dumps(no),
    }

def save_snapshot(snapshot, ticker):
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    fname = f"{OUTPUT_DIR}/{ticker.replace('.', '_')}_{date_str}.csv"
    df = pd.DataFrame([snapshot])
    if not os.path.exists(fname):
        df.to_csv(fname, index=False)
    else:
        df.to_csv(fname, mode="a", header=False, index=False)

def log_once(ticker):
    raw = fetch_orderbook(ticker)
    if raw:
        snap = flatten_snapshot(raw, ticker)
        save_snapshot(snap, ticker)
        print(f"✅ [{ticker}] {snap['timestamp']} | YES: {snap['best_yes']} | NO: {snap['best_no']}")
    else:
        print(f"⚠️ [{ticker}] orderbook fetch failed.")

# =============================
# ▶️ Main loop
# =============================
def main():
    # Discover → validate
    candidates = discover_markets()
    if not candidates:
        print("⚠️ No candidates discovered via series or crawl.")
    tickers = validate_tickers(candidates, limit=VALIDATE_LIMIT)

    # Fallback: manual tickers if nothing validated
    if not tickers:
        print("⚠️ No validated tickers. Add manual ones below and re-run.")
        tickers = [
            # "PUT_KNOWN_GOOD_TICKER_HERE"
        ]
        if not tickers:
            print("❌ No tickers to log. Exiting.")
            return

    print(f"📝 Will log {len(tickers)} ticker(s): {tickers}")
    try:
        while True:
            cycle_ts = datetime.utcnow().isoformat() + "Z"
            print(f"\n⏱️ Cycle @ {cycle_ts}")
            for t in tickers:
                log_once(t)
                time.sleep(SPACING_BETWEEN_TICKERS)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("🛑 Stopped by user.")

if __name__ == "__main__":
    main()
