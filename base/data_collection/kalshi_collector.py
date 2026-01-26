"""
Kalshi market data collector.
- Discovers markets by sport/series and date
- Uses WebSocket for real-time updates
- Appends data to CSV files with timestamps
- Keeps markets until status is no longer active
"""

import os
import sys
import time
import argparse
import asyncio
import json
import threading
import csv
import re
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Set
from pathlib import Path
import pytz
import pandas as pd
import websockets

# Add base directory to path
_BASE_ROOT = Path(__file__).parent.parent.absolute()
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from config import settings
from kalshi.auth import load_private_key, sign_message
from kalshi.markets import get_kalshi_markets, format_price
from core.session import SESSION
from kalshi.auth import kalshi_headers

# Timezone for date filtering
LOCAL_TZ = pytz.timezone("US/Eastern")

# Series ticker mappings for sports
SPORT_TO_SERIES = {
    "NFL": ["KXNFLGAME", "KXNFLTOTAL", "KXNFLSPREAD"],
    "NBA": ["KXNBAGAME", "KXNBATOTAL", "KXNBASPREAD"],
    "CFB": ["KXNCAAFGAME", "KXNCAAFTOTAL", "KXNCAAFSPREAD"],
    "CBBM": ["KXNCAAMBGAME", "KXNCAAMBTOTAL", "KXNCAAMBSPREAD"],
    "CBBW": ["KXNCAAWBGAME"],
    "ATP": ["KXATPMATCH", "KXATPTOTALSETS"],
    "ALL": ["KXNFLGAME", "KXNBAGAME", "KXNCAAFGAME", "KXNCAAMBGAME", "KXNCAAWBGAME",
            "KXNFLTOTAL", "KXNFLSPREAD", "KXNBATOTAL", "KXNBASPREAD",
            "KXNCAAFTOTAL", "KXNCAAFSPREAD", "KXNCAAMBTOTAL", "KXNCAAMBSPREAD"],
}

# CSV columns
CSV_COLUMNS = [
    "timestamp", "ticker", "title", "status", "market_type", "event_start_time",
    "yes_bid", "yes_ask", "no_bid", "no_ask",
    "liquidity_dollars", "volume_24h",
]


def _parse_time(ts_str: Any) -> Optional[datetime]:
    """Parse timestamp string to timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        if isinstance(ts_str, datetime):
            dt = ts_str
        else:
            dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    """Convert value to float, return None if invalid."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _get_markets_by_series(series_ticker: str) -> List[Dict[str, Any]]:
    """Fetch all markets for a series ticker."""
    base_path = "/trade-api/v2/markets"
    
    all_markets = []
    cursor = None
    max_pages = 20
    pages = 0
    
    while pages < max_pages:
        try:
            # Build query string for path (for auth signature)
            query_parts = [f"series_ticker={series_ticker}"]
            if cursor:
                query_parts.append(f"cursor={cursor}")
            query_string = "&".join(query_parts)
            path = f"{base_path}?{query_string}"
            
            url = f"{settings.KALSHI_BASE_URL}{path}"
            headers = kalshi_headers("GET", path)
            
            res = SESSION.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                markets = data.get("markets", [])
                all_markets.extend(markets)
                cursor = data.get("cursor")
                if not cursor:
                    break
            elif res.status_code == 429:
                print(f"⚠️ Rate limited fetching {series_ticker}, waiting...")
                time.sleep(2.0)
                continue
            else:
                print(f"❌ Error fetching {series_ticker}: {res.status_code} - {res.text[:120]}")
                break
        except Exception as e:
            print(f"❌ Exception fetching {series_ticker}: {e}")
            break
        
        pages += 1
        time.sleep(0.1)  # Small delay between pages
    
    return all_markets


def _infer_event_date(market: Dict[str, Any]) -> Optional[date]:
    """Infer event date from market data (tries ticker first, then time fields)."""
    ticker = (market.get("ticker") or "").upper()
    # Try to parse date from ticker (format: -26JAN12)
    ticker_date_re = re.compile(r"-(\d{2}[A-Z]{3}\d{2})")
    m = ticker_date_re.search(ticker)
    if m:
        try:
            return datetime.strptime(m.group(1), "%y%b%d").date()
        except ValueError:
            pass
    # Fall back to time fields
    for key in ("event_start_time", "event_expiration_time", "close_time", "expiry"):
        dt = _parse_time(market.get(key))
        if dt:
            return dt.date()
    return None


def _filter_markets_by_date(markets: List[Dict[str, Any]], target_date: date) -> List[Dict[str, Any]]:
    """Filter markets to only those for the target date."""
    filtered = []
    for market in markets:
        event_date = _infer_event_date(market)
        if event_date == target_date:
            filtered.append(market)
    return filtered


def _market_to_row(market: Dict[str, Any], timestamp: datetime) -> Dict[str, Any]:
    """Convert market dict to CSV row."""
    yes_bid = format_price(market.get("yes_bid"))
    yes_ask = format_price(market.get("yes_ask"))
    no_bid = format_price(market.get("no_bid"))
    no_ask = format_price(market.get("no_ask"))
    event_time = _parse_time(market.get("event_start_time"))
    
    return {
        "timestamp": timestamp.isoformat(),
        "ticker": market.get("ticker"),
        "title": market.get("title"),
        "status": market.get("status"),
        "market_type": market.get("market_type"),
        "event_start_time": event_time.isoformat() if event_time else None,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "liquidity_dollars": _to_float(market.get("liquidity_dollars")),
        "volume_24h": _to_float(market.get("volume_24h")),
    }


class KalshiCollector:
    """Collects Kalshi market data using REST API and WebSocket."""
    
    def __init__(self, target_date: date, sports: List[str], output_dir: Path, runtime_seconds: Optional[float] = None):
        self.target_date = target_date
        self.sports = sports
        self.output_dir = output_dir
        self.runtime_seconds = runtime_seconds
        
        self.markets: Dict[str, Dict[str, Any]] = {}  # ticker -> market dict
        self.markets_lock = threading.RLock()
        
        self.ws = None  # WebSocket connection (from websockets.connect())
        self.running = False
        self.message_id = 1
        self.message_id_lock = threading.Lock()
        
        self.csv_file: Optional[Path] = None  # Single CSV file per date
        self.csv_lock = threading.Lock()
        
        # Create output directory
        date_str = target_date.isoformat()
        self.date_output_dir = output_dir / date_str
        self.date_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create single CSV file for this date
        self.csv_file = self.date_output_dir / f"markets_{date_str}.csv"
        if not self.csv_file.exists() or self.csv_file.stat().st_size == 0:
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
    
    def _get_next_message_id(self) -> int:
        """Get next WebSocket message ID."""
        with self.message_id_lock:
            msg_id = self.message_id
            self.message_id += 1
            return msg_id
    
    def _create_auth_headers(self) -> Dict[str, str]:
        """Create WebSocket authentication headers."""
        timestamp = str(int(time.time() * 1000))
        method = "GET"
        path = "/trade-api/ws/v2"
        msg = timestamp + method + path
        
        private_key = load_private_key()
        signature = sign_message(private_key, msg)
        
        return {
            "KALSHI-ACCESS-KEY": settings.API_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }
    
    def _append_row(self, row: Dict[str, Any]):
        """Append a row to the CSV file (thread-safe)."""
        if not self.csv_file:
            return
        
        with self.csv_lock:
            with open(self.csv_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writerow(row)
    
    def discover_markets(self) -> int:
        """Discover markets for the target date using REST API."""
        print(f"🔍 Discovering markets for {self.target_date.isoformat()}...")
        
        # Get series tickers for requested sports
        series_tickers = set()
        for sport in self.sports:
            if sport.upper() in SPORT_TO_SERIES:
                series_tickers.update(SPORT_TO_SERIES[sport.upper()])
            elif sport.upper().startswith("KX"):
                # Direct series ticker
                series_tickers.add(sport.upper())
        
        if not series_tickers:
            print(f"❌ No valid sports or series tickers found: {self.sports}")
            return 0
        
        print(f"📊 Fetching markets for series: {', '.join(sorted(series_tickers))}")
        
        all_markets = []
        for series_ticker in series_tickers:
            try:
                print(f"  📡 Fetching {series_ticker}...")
                markets = _get_markets_by_series(series_ticker)
                print(f"    📊 Got {len(markets)} total markets from API")
                filtered = _filter_markets_by_date(markets, self.target_date)
                all_markets.extend(filtered)
                print(f"    ✅ Found {len(filtered)} markets for {self.target_date.isoformat()}")
                time.sleep(0.5)  # Rate limiting
            except Exception as e:
                print(f"    ❌ Error fetching {series_ticker}: {e}")
        
        # Store markets
        with self.markets_lock:
            for market in all_markets:
                ticker = market.get("ticker")
                if ticker:
                    self.markets[ticker] = market
        
        print(f"✅ Discovered {len(self.markets)} unique markets")
        return len(self.markets)
    
    async def _subscribe_to_markets(self, market_tickers: List[str]):
        """Subscribe to WebSocket updates for markets."""
        if not self.ws or not market_tickers:
            return
        
        try:
            if self.ws.closed:
                return
        except AttributeError:
            pass
        
        subscription = {
            "id": self._get_next_message_id(),
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"],
                "market_tickers": market_tickers
            }
        }
        
        try:
            await self.ws.send(json.dumps(subscription))
            print(f"📡 Subscribed to {len(market_tickers)} markets via WebSocket")
        except Exception as e:
            print(f"⚠️ Error subscribing: {e}")
    
    async def _process_websocket_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "ticker":
                ticker_data = data.get("msg") or data.get("data") or {}
                market_ticker = ticker_data.get("market_ticker")
                
                if not market_ticker:
                    return
                
                # Update market data
                with self.markets_lock:
                    if market_ticker not in self.markets:
                        # New market, fetch full data
                        # For now, skip - we only track discovered markets
                        return
                    
                    # Update prices
                    market = self.markets[market_ticker]
                    market["yes_bid"] = ticker_data.get("yes_bid")
                    market["yes_ask"] = ticker_data.get("yes_ask")
                    market["no_bid"] = ticker_data.get("no_bid")
                    market["no_ask"] = ticker_data.get("no_ask")
                    market["liquidity_dollars"] = ticker_data.get("liquidity_dollars")
                    market["volume_24h"] = ticker_data.get("volume_24h")
                    # Status might be in ticker data, but usually we get it from REST API
                
                # Write to CSV
                timestamp = datetime.now(LOCAL_TZ)
                row = _market_to_row(market, timestamp)
                self._append_row(row)
                
        except json.JSONDecodeError as e:
            print(f"⚠️ Error parsing WebSocket message: {e}")
        except Exception as e:
            print(f"⚠️ Error processing WebSocket message: {e}")
    
    async def _update_markets_via_rest(self):
        """Periodically update markets via REST API to check status."""
        while self.running:
            try:
                await asyncio.sleep(60)  # Update every 60 seconds
                
                with self.markets_lock:
                    tickers_to_check = list(self.markets.keys())
                
                if not tickers_to_check:
                    continue
                
                # Group by event ticker (first part before last dash)
                event_tickers = set()
                for ticker in tickers_to_check:
                    parts = ticker.split("-")
                    if len(parts) >= 2:
                        event_ticker = "-".join(parts[:-1])
                        event_tickers.add(event_ticker)
                
                updated_count = 0
                for event_ticker in event_tickers:
                    try:
                        markets = get_kalshi_markets(event_ticker, force_live=True) or []
                        with self.markets_lock:
                            for market in markets:
                                ticker = market.get("ticker")
                                if ticker in self.markets:
                                    old_status = self.markets[ticker].get("status")
                                    new_status = market.get("status")
                                    
                                    # Update market data
                                    self.markets[ticker].update(market)
                                    
                                    # Remove if no longer active
                                    if new_status and new_status not in ["active", "open"]:
                                        # Write final update before removing
                                        timestamp = datetime.now(LOCAL_TZ)
                                        row = _market_to_row(self.markets[ticker], timestamp)
                                        self._append_row(row)
                                        
                                        del self.markets[ticker]
                                        updated_count += 1
                                    elif old_status != new_status:
                                        # Write update if status changed
                                        timestamp = datetime.now(LOCAL_TZ)
                                        row = _market_to_row(self.markets[ticker], timestamp)
                                        self._append_row(row)
                        
                        await asyncio.sleep(0.5)  # Rate limiting
                    except Exception as e:
                        print(f"⚠️ Error updating markets for {event_ticker}: {e}")
                
                if updated_count > 0:
                    print(f"🔄 Removed {updated_count} inactive markets")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Error in REST update loop: {e}")
    
    async def _connection_loop(self):
        """Main WebSocket connection loop."""
        while self.running:
            try:
                headers = self._create_auth_headers()
                
                async with websockets.connect(
                    settings.KALSHI_WS_URL,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                ) as websocket:
                    self.ws = websocket
                    print("✅ Connected to Kalshi WebSocket")
                    
                    # Subscribe to all discovered markets
                    with self.markets_lock:
                        market_tickers = list(self.markets.keys())
                    
                    if market_tickers:
                        # Batch subscribe (Kalshi supports multiple markets)
                        await self._subscribe_to_markets(market_tickers)
                    
                    # Start REST update task
                    rest_task = asyncio.create_task(self._update_markets_via_rest())
                    
                    try:
                        # Process messages
                        async for message in websocket:
                            if not self.running:
                                break
                            await self._process_websocket_message(message)
                    finally:
                        rest_task.cancel()
                        try:
                            await rest_task
                        except asyncio.CancelledError:
                            pass
            
            except websockets.exceptions.ConnectionClosed:
                if self.running:
                    print("⚠️ WebSocket connection closed, reconnecting...")
                    await asyncio.sleep(5.0)
                else:
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.running:
                    print(f"❌ WebSocket error: {e}")
                    await asyncio.sleep(5.0)
                else:
                    break
    
    async def start(self):
        """Start the collector."""
        self.running = True
        
        # Discover markets
        market_count = self.discover_markets()
        if market_count == 0:
            print("⚠️ No markets found, exiting")
            self.running = False
            return
        
        # Write initial snapshot
        timestamp = datetime.now(LOCAL_TZ)
        with self.markets_lock:
            for ticker, market in self.markets.items():
                row = _market_to_row(market, timestamp)
                self._append_row(row)
        
        print(f"💾 Wrote initial snapshot of {market_count} markets")
        
        # Start WebSocket connection
        await self._connection_loop()
    
    async def stop(self):
        """Stop the collector."""
        self.running = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        
        print("🛑 Collector stopped")


async def main_async(args):
    """Async main function."""
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    # Use sports from settings.SPORT_KEYS
    sports = list(settings.SPORT_KEYS.keys())
    output_dir = Path(args.output_dir) if args.output_dir else settings.KALSHI_DATA_DIR / "kalshi_logs"
    runtime_seconds = float(settings.KALSHI_COLLECTOR_RUNTIME) if settings.KALSHI_COLLECTOR_RUNTIME else None
    
    print(f"🎯 Target date: {target_date.isoformat()}")
    print(f"🎯 Sports: {', '.join(sports)}")
    print(f"📁 Output directory: {output_dir}")
    if runtime_seconds:
        print(f"⏱️  Runtime: {runtime_seconds} seconds")
    else:
        print(f"⏱️  Runtime: Indefinite (Ctrl+C to stop)")
    
    collector = KalshiCollector(target_date, sports, output_dir, runtime_seconds)
    
    # Start collector
    collector_task = asyncio.create_task(collector.start())
    
    # Handle runtime limit
    try:
        if runtime_seconds:
            await asyncio.wait_for(collector_task, timeout=runtime_seconds)
        else:
            await collector_task
    except asyncio.TimeoutError:
        print(f"⏱️  Runtime limit reached ({runtime_seconds} seconds)")
        await collector.stop()
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
        await collector.stop()
    except Exception as e:
        print(f"❌ Error: {e}")
        await collector.stop()
        raise


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Collect Kalshi market data")
    parser.add_argument(
        "--date",
        help="Target date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory. Defaults to KALSHI_DATA_DIR/kalshi_logs.",
    )
    
    args = parser.parse_args()
    
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")


if __name__ == "__main__":
    main()
