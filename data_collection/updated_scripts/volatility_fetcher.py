"""
Targeted Kalshi fetcher:
- Auth via env KALSHI_API_KEY (same host as kalshi_fetcher.py).
- Fetch only the requested series, then client-filter to active markets.
- Filter to a target event date (defaults to today US/Eastern).
- Write per-series CSVs with a fixed schema.
"""

import os
import sys
import time
import argparse
import random
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import pytz
import requests
from requests.adapters import HTTPAdapter

# -------------
# Configuration
# -------------
RUN_DURATION_SECONDS = 300  # Run indefinitely. Set to a number for a fixed duration.
SLEEP_TIME_SECONDS = 20      # Time to sleep between fetch cycles.

API_DOMAIN = "https://api.elections.kalshi.com"
API_KEY = "95ebf414-44b0-43fa-af17-36221039f78c"  # Hardcoded API Key
DEFAULT_OUTPUT_DIR = "volatility_data_logs"
DEFAULT_SERIES: List[str] = [
    "KXNFLGAME", "KXNBAGAME", "KXNCAAFGAME", "KXNCAAMBGAME", "KXNCAAWBGAME"
]

""""KXNFLTOTAL", "KXNFLSPREAD", "KXNBATOTAL", "KXNBASPREAD",
    "KXNCAAFTOTAL", "KXNCAAFSPREAD", "KXNCAAMBTOTAL", "KXNCAAMBSPREAD","""

SERIES_TO_FILENAME = {
    "KXNFLGAME": "nfl_winners.csv",
    "KXNBAGAME": "nba_winners.csv",
    "KXNCAAFGAME": "ncaaf_winners.csv",
    "KXNCAAMBGAME": "ncaab_winners.csv",
    "KXNCAAWBGAME": "ncaabw_winners.csv"
}

""""KXNFLTOTAL": "nfl_totals.csv",
    "KXNFLSPREAD": "nfl_spreads.csv",
    "KXNBATOTAL": "nba_totals.csv",
    "KXNBASPREAD": "nba_spreads.csv",
    "KXNCAAFTOTAL": "ncaaf_totals.csv",
    "KXNCAAFSPREAD": "ncaaf_spreads.csv",
    "KXNCAAMBTOTAL": "ncaab_totals.csv",
    "KXNCAAMBSPREAD": "ncaab_spreads.csv","""
TICKER_DATE_RE = re.compile(r"-(\d{2}[A-Z]{3}\d{2})")
LOCAL_TZ = pytz.timezone("US/Eastern")


# -------------
# HTTP session
# -------------
def _build_session() -> requests.Session:
    session = requests.Session()
    if not API_KEY or API_KEY == "PUT_YOUR_KALSHI_API_KEY_HERE":
        sys.exit("❌ Set the API_KEY variable in the script to your Kalshi API key.")

    session.headers.update(
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        }
    )
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = _build_session()


# -------------
# API helpers
# -------------
def get_markets_page(
    series_ticker: str,
    cursor: Optional[str] = None,
    timeout: int = 20,
    max_retries: int = 5,
    backoff_factor: float = 0.5,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Fetches a single page of markets for a series, with robust exponential backoff.
    No other filters are applied at the API level.
    """
    params = {"series_ticker": series_ticker}
    if cursor:
        params["cursor"] = cursor

    for attempt in range(max_retries):
        try:
            resp = SESSION.get(f"{API_DOMAIN}/trade-api/v2/markets", params=params, timeout=timeout)

            if resp.status_code == 200:
                data = resp.json()
                return data.get("markets", []), data.get("cursor")

            if resp.status_code == 429 and attempt < max_retries - 1:
                sleep_for = backoff_factor * (2 ** attempt) + random.uniform(0, 0.2)
                print(f"⌛ 429 on {series_ticker}, retrying in {sleep_for:.1f}s...")
                time.sleep(sleep_for)
                continue

            # For other errors, raise an exception to be caught by the main loop
            resp.raise_for_status()

        except requests.RequestException as e:
            if attempt < max_retries - 1:
                sleep_for = backoff_factor * (2 ** attempt) + random.uniform(0, 0.1)
                print(f"⚠️ Request error on {series_ticker} ({e}), retrying in {sleep_for:.1f}s...")
                time.sleep(sleep_for)
                continue
            else:
                # After all retries, raise a final error to be handled by the caller
                raise RuntimeError(f"Fetching failed for {series_ticker} after {max_retries} retries: {e}")

    return [], None


# -------------
# Data Processing
# -------------
OUTPUT_COLUMNS = [
    "timestamp", "ticker", "title", "status", "market_type", "event_start_time",
    "yes_bid", "yes_ask", "no_bid", "no_ask", "yes_spread", "no_spread",
    "liquidity_dollars", "volume_24h",
]

def _parse_time(ts_str: Any) -> Optional[datetime]:
    """Parses a timestamp string into a timezone-aware datetime object."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None

def _infer_event_date(market: Dict[str, Any]) -> Optional[datetime.date]:
    """Infers the event date from various fields in the market data."""
    ticker = (market.get("ticker") or "").upper()
    m = TICKER_DATE_RE.search(ticker)
    if m:
        try:
            return datetime.strptime(m.group(1), "%y%b%d").date()
        except ValueError:
            pass
    for key in ("event_start_time", "event_expiration_time", "close_time", "expiry"):
        dt = _parse_time(market.get(key))
        if dt:
            return dt.date()
    return None

def to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def calc_spread(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is not None and ask is not None:
        return round(ask - bid, 4)
    return None

def process_markets_to_rows(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts a list of market objects from the API into structured rows for the CSV."""
    rows: List[Dict[str, Any]] = []
    ts_now = datetime.now().astimezone(LOCAL_TZ).isoformat()

    for market in markets:
        yes_bid = to_float(market.get("yes_bid_dollars"))
        yes_ask = to_float(market.get("yes_ask_dollars"))
        no_bid = to_float(market.get("no_bid_dollars"))
        no_ask = to_float(market.get("no_ask_dollars"))
        event_time = _parse_time(market.get("event_start_time"))

        rows.append({
            "timestamp": ts_now,
            "ticker": market.get("ticker"),
            "title": market.get("title"),
            "status": market.get("status"),
            "market_type": market.get("market_type"),
            "event_start_time": event_time.isoformat() if event_time else None,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "yes_spread": calc_spread(yes_bid, yes_ask),
            "no_spread": calc_spread(no_bid, no_ask),
            "liquidity_dollars": to_float(market.get("liquidity_dollars")),
            "volume_24h": to_float(market.get("volume_24h")),
        })
    return rows


def write_rows(all_rows: List[Dict[str, Any]], target_date: datetime.date, output_dir: str):
    """Groups rows by series and appends them to dated CSV files."""
    if not all_rows:
        print("↩️ No rows to write for this run.")
        return

    dated_dir = os.path.join(output_dir, target_date.isoformat())
    os.makedirs(dated_dir, exist_ok=True)

    by_series: Dict[str, List[Dict[str, Any]]] = {}
    for row in all_rows:
        ticker = row.get("ticker", "")
        series_key = next((k for k in SERIES_TO_FILENAME if ticker.startswith(k)), "unknown")
        if series_key not in by_series:
            by_series[series_key] = []
        by_series[series_key].append(row)

    for series_ticker, rows_list in by_series.items():
        filename = SERIES_TO_FILENAME.get(series_ticker)
        if not filename:
            print(f"⚠️ No filename mapping for series {series_ticker}, skipping.")
            continue

        path = os.path.join(dated_dir, filename)
        df = pd.DataFrame(rows_list, columns=OUTPUT_COLUMNS)
        
        # Append to CSV, write header only if file doesn't exist
        header = not os.path.exists(path)
        df.to_csv(path, mode='a', index=False, header=header)
        
        print(f"💾 Appended {len(df)} rows to {path}")


# -------------
# Main
# -------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously fetch Kalshi market data for volatility analysis.")
    parser.add_argument(
        "--date",
        help=f"Event date to fetch (YYYY-MM-DD). Defaults to today ({LOCAL_TZ.zone}).",
    )
    parser.add_argument(
        "--series",
        help="Comma-separated list of series tickers to fetch. Defaults to all.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Base output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(LOCAL_TZ).date()
    )
    series_list = (
        [s.strip().upper() for s in args.series.split(",") if s.strip()]
        if args.series
        else DEFAULT_SERIES
    )
    
    print(f"🎯 Target date: {target_date.isoformat()}")
    print(f"🎯 Series: {series_list}")
    if RUN_DURATION_SECONDS:
        print(f"⏳ Running for a total of {RUN_DURATION_SECONDS} seconds.")
    else:
        print("⏳ Running indefinitely.")
    print(f"😴 Sleep time between cycles: {SLEEP_TIME_SECONDS} seconds.")


    start_time = time.time()
    while True:
        if RUN_DURATION_SECONDS and (time.time() - start_time > RUN_DURATION_SECONDS):
            break

        print(f"\n--- Starting new fetch cycle at {datetime.now(LOCAL_TZ).isoformat()} ---")
        all_rows: List[Dict[str, Any]] = []
        for series in series_list:
            series_markets_for_date = []
            cursor = None
            max_pages = 20
            pages = 0

            try:
                while pages < max_pages:
                    markets_page, cursor = get_markets_page(series, cursor=cursor)

                    if markets_page:
                        # Client-side filtering
                        active_markets = [m for m in markets_page if m.get("status") == "active"]
                        date_filtered_markets = [
                            m for m in active_markets if _infer_event_date(m) == target_date
                        ]

                        if date_filtered_markets:
                            series_markets_for_date.extend(date_filtered_markets)

                    pages += 1
                    if not cursor:
                        break
                    time.sleep(0.1)

                if series_markets_for_date:
                    rows = process_markets_to_rows(series_markets_for_date)
                    all_rows.extend(rows)
                    print(f"✅ Found and processed {len(rows)} markets for {series} on {target_date.isoformat()}.")
                else:
                    print(f"ℹ️ No matching markets for {series} on {target_date.isoformat()}.")

            except Exception as exc:
                print(f"⚠️ Skipping {series} due to error: {exc}")
                time.sleep(1.0)
                continue

            # Pause between different series to avoid bursting the API rate limit
            time.sleep(0.5)

        write_rows(all_rows, target_date, args.output_dir)
        
        print(f"--- Cycle complete. Sleeping for {SLEEP_TIME_SECONDS} seconds. ---")
        time.sleep(SLEEP_TIME_SECONDS)

    print(f"\n🏁 Run duration of {RUN_DURATION_SECONDS} seconds complete. Exiting.")


if __name__ == "__main__":
    main()