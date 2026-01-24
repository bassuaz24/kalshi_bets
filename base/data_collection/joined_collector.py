"""
Joined Kalshi and OddsAPI data collector.

This collector:
1. Fetches OddsAPI data before subscribing to websockets
2. Matches Kalshi markets to OddsAPI data
3. Writes combined CSV files with both Kalshi and OddsAPI data
"""

import asyncio
import json
import csv
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Any, Optional
import pytz

import os
import sys
from pathlib import Path

# Add base directory to path
_BASE_ROOT = Path(__file__).parent.parent.absolute()
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from data_collection.kalshi_collector import KalshiCollector, CSV_COLUMNS, _market_to_row, _parse_time
from data_collection.market_matcher import MarketMatcher, parse_kalshi_ticker
from data_collection.oddsapi_client import fetch_odds, normalize_odds_data, save_skipped_games
from config import settings

LOCAL_TZ = pytz.timezone("US/Eastern")

# Extended CSV columns for joined data
JOINED_CSV_COLUMNS = CSV_COLUMNS + [
    "oddsapi_price",  # Weighted average price from OddsAPI
    "oddsapi_game_id",
    "oddsapi_team",
    "oddsapi_point",
    "oddsapi_home_team",
    "oddsapi_away_team",
    "oddsapi_start_time",  # Start time from OddsAPI
    "match_status",  # "matched" or "unmatched"
]


class JoinedCollector(KalshiCollector):
    """Collector that combines Kalshi and OddsAPI data."""
    
    def __init__(self, target_date: date, sports: List[str], output_dir: Path, runtime_seconds: Optional[float] = None):
        super().__init__(target_date, sports, output_dir, runtime_seconds)
        
        # Initialize matcher
        match_cache_file = output_dir / f"match_cache_{target_date.isoformat()}.json"
        self.matcher = MarketMatcher(settings.DATA_DIR, match_cache_file)
        
        # Create joined CSV file
        date_str = target_date.isoformat()
        self.joined_csv_file = self.date_output_dir / f"joined_{date_str}.csv"
        if not self.joined_csv_file.exists() or self.joined_csv_file.stat().st_size == 0:
            with open(self.joined_csv_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=JOINED_CSV_COLUMNS)
                writer.writeheader()
        
        # Track unmatched markets for logging
        self.unmatched_log: List[Dict[str, Any]] = []
    
    def _append_joined_row(self, row: Dict[str, Any]):
        """Append a row to the joined CSV file (thread-safe)."""
        if not self.joined_csv_file:
            return
        
        with self.csv_lock:
            with open(self.joined_csv_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=JOINED_CSV_COLUMNS)
                writer.writerow(row)
    
    def _fetch_oddsapi_data(self, overwrite: bool = True) -> bool:
        """
        Fetch OddsAPI data and save to CSV files.
        
        Args:
            overwrite: If True, overwrite existing files (default: True for periodic updates)
        
        Returns True if successful, False otherwise.
        """
        print("📡 Fetching OddsAPI data...")
        target_dates = {self.target_date}
        
        # Fetch data for each sport
        all_skipped = []
        for sport_code, oddsapi_sport_key in settings.SPORT_KEYS.items():
            # #region agent log
            with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"joined_collector.py:91","message":"Checking sport","data":{"sport_code":sport_code,"oddsapi_sport_key":oddsapi_sport_key,"sports":self.sports,"will_process":sport_code in self.sports or "ALL" in self.sports},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
            # #endregion
            
            if sport_code not in self.sports and "ALL" not in self.sports:
                # #region agent log
                with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"joined_collector.py:94","message":"Skipping sport (not in self.sports)","data":{"sport_code":sport_code},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
                # #endregion
                continue
            
            print(f"  📊 Fetching {sport_code} ({oddsapi_sport_key})...")
            games = fetch_odds(oddsapi_sport_key)
            
            # #region agent log
            with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"joined_collector.py:99","message":"After fetch_odds in joined_collector","data":{"sport_code":sport_code,"oddsapi_sport_key":oddsapi_sport_key,"games_is_none":games is None,"games_count":len(games) if games else 0},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
            # #endregion
            
            if not games:
                print(f"    ⚠️ No data for {sport_code}")
                # #region agent log
                with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"joined_collector.py:104","message":"No games returned, skipping","data":{"sport_code":sport_code},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
                # #endregion
                continue
            
            # Normalize data
            rows_by_date, skipped = normalize_odds_data(sport_code, games, target_dates)
            
            # #region agent log
            with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"joined_collector.py:110","message":"After normalize_odds_data in joined_collector","data":{"sport_code":sport_code,"rows_by_date_keys":list(str(k) for k in rows_by_date.keys()),"rows_by_date_counts":{str(k):len(v) for k,v in rows_by_date.items()},"skipped_count":len(skipped)},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
            # #endregion
            
            # Save skipped games
            if skipped:
                skipped_dir = settings.DATA_DIR / "skipped_games"
                skipped_dir.mkdir(parents=True, exist_ok=True)
                skipped_file = skipped_dir / f"skipped_games_{self.target_date.isoformat()}.csv"
                save_skipped_games(skipped, skipped_file)
                all_skipped.extend(skipped)
            
            # Save data to CSV files (overwrite existing files)
            for game_date, rows in rows_by_date.items():
                if not rows:
                    continue
                
                # Create date directory
                # Ensure game_date is a date object
                if isinstance(game_date, str):
                    from datetime import datetime as dt
                    game_date = dt.strptime(game_date, "%Y-%m-%d").date()
                date_dir = settings.DATA_DIR / game_date.isoformat()
                date_dir.mkdir(parents=True, exist_ok=True)
                
                # Separate by market type and save
                import pandas as pd
                df = pd.DataFrame(rows)
                for market_type in df["market"].unique():
                    market_rows = df[df["market"] == market_type]
                    
                    # File naming: {sport_name}_{market_type}.csv
                    sport_name_lower = sport_code.lower()
                    filename = f"{sport_name_lower}_{market_type}.csv"
                    filepath = date_dir / filename
                    
                    # Save (overwrite if overwrite=True)
                    if overwrite or not filepath.exists():
                        market_rows.to_csv(filepath, index=False)
                        print(f"    💾 Saved {len(market_rows)} rows to {filepath.name}")
            
            print(f"    ✅ Processed {len(games)} games for {sport_code}")
        
        # Skipped games are saved to CSV, no need to print to terminal
        
        return True
    
    def discover_markets(self) -> int:
        """Discover markets and perform initial matching."""
        # First fetch OddsAPI data
        self._fetch_oddsapi_data()
        
        # Then discover Kalshi markets
        market_count = super().discover_markets()
        
        # Perform initial matching
        print("🔍 Performing initial market matching...")
        matched_count = 0
        with self.markets_lock:
            for ticker, market in self.markets.items():
                match_key = self.matcher.find_match(ticker, market)
                if match_key:
                    matched_count += 1
                else:
                    # Log unmatched market during initial matching
                    parsed = parse_kalshi_ticker(ticker)
                    self.unmatched_log.append({
                        "timestamp": datetime.now(LOCAL_TZ).isoformat(),
                        "ticker": ticker,
                        "title": market.get("title"),
                        "sport": parsed.get("sport") if parsed else None,
                        "market_type": parsed.get("market_type") if parsed else None,
                    })
        
        stats = self.matcher.get_stats()
        print(f"📊 Matching stats:")
        print(f"   Total markets: {market_count}")
        print(f"   Matched: {stats['matched']}")
        print(f"   Unmatched: {stats['unmatched']}")
        print(f"   Unmatched (unique): {stats['unmatched_count']}")
        print(f"   H2H matched: {stats['h2h_matched']}")
        print(f"   Spread matched: {stats['spread_matched']}")
        print(f"   Total matched: {stats['total_matched']}")
        print(f"   Unmatched log entries: {len(self.unmatched_log)}")
        print(f"✅ Initial matching complete. Proceeding to websocket subscription...")
        
        # Save unmatched markets immediately after initial matching (in case script doesn't call stop())
        self._save_unmatched_markets()
        
        return market_count
    
    async def _process_websocket_message(self, message: str):
        """Process incoming WebSocket message and write joined data."""
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
                        return
                    
                    market = self.markets[market_ticker]
                    market["yes_bid"] = ticker_data.get("yes_bid")
                    market["yes_ask"] = ticker_data.get("yes_ask")
                    market["no_bid"] = ticker_data.get("no_bid")
                    market["no_ask"] = ticker_data.get("no_ask")
                    market["liquidity_dollars"] = ticker_data.get("liquidity_dollars")
                    market["volume_24h"] = ticker_data.get("volume_24h")
                
                # Write to regular CSV (parent class behavior)
                timestamp = datetime.now(LOCAL_TZ)
                row = _market_to_row(market, timestamp)
                self._append_row(row)
                
                # Write joined data
                self._write_joined_row(market, timestamp)
                
        except json.JSONDecodeError as e:
            print(f"⚠️ Error parsing WebSocket message: {e}")
        except Exception as e:
            print(f"⚠️ Error processing WebSocket message: {e}")
    
    def _write_joined_row(self, market: Dict[str, Any], timestamp: datetime):
        """Write a joined row with Kalshi and OddsAPI data."""
        ticker = market.get("ticker")
        if not ticker:
            return
        
        # Get match
        match_key = self.matcher.find_match(ticker, market)
        
        # Start with Kalshi data
        kalshi_row = _market_to_row(market, timestamp)
        
        # Add OddsAPI data if matched
        if match_key:
            parsed = parse_kalshi_ticker(ticker)
            if parsed:
                event_date = parsed.get("date")
                if event_date:
                    weighted_price = self.matcher.get_weighted_price(ticker, match_key, event_date)
                    oddsapi_rows = self.matcher.get_oddsapi_rows(ticker, match_key, event_date)
                    
                    if oddsapi_rows and len(oddsapi_rows) > 0:
                        first_row = oddsapi_rows[0]
                        joined_row = {
                            **kalshi_row,
                            "oddsapi_price": weighted_price,
                            "oddsapi_game_id": str(first_row.get("game_id", "")),
                            "oddsapi_team": str(first_row.get("team", "")),
                            "oddsapi_point": first_row.get("point"),
                            "oddsapi_home_team": str(first_row.get("home_team", "")),
                            "oddsapi_away_team": str(first_row.get("away_team", "")),
                            "oddsapi_start_time": str(first_row.get("start_time", "")),
                            "match_status": "matched",
                        }
                    else:
                        # Match found but no rows (shouldn't happen)
                        joined_row = {
                            **kalshi_row,
                            "oddsapi_price": None,
                            "oddsapi_game_id": None,
                            "oddsapi_team": None,
                            "oddsapi_point": None,
                            "oddsapi_home_team": None,
                            "oddsapi_away_team": None,
                            "oddsapi_start_time": None,
                            "match_status": "matched_no_data",
                        }
                else:
                    joined_row = {
                        **kalshi_row,
                        "oddsapi_price": None,
                        "oddsapi_game_id": None,
                        "oddsapi_team": None,
                        "oddsapi_point": None,
                        "oddsapi_home_team": None,
                        "oddsapi_away_team": None,
                        "oddsapi_start_time": None,
                        "match_status": "parse_error",
                    }
            else:
                joined_row = {
                    **kalshi_row,
                    "oddsapi_price": None,
                    "oddsapi_game_id": None,
                    "oddsapi_team": None,
                    "oddsapi_point": None,
                    "oddsapi_home_team": None,
                    "oddsapi_away_team": None,
                    "oddsapi_start_time": None,
                    "match_status": "parse_error",
                }
        else:
            # No match - write Kalshi-only data
            joined_row = {
                **kalshi_row,
                "oddsapi_price": None,
                "oddsapi_game_id": None,
                "oddsapi_team": None,
                "oddsapi_point": None,
                "oddsapi_home_team": None,
                "oddsapi_away_team": None,
                "oddsapi_start_time": None,
                "match_status": "unmatched",
            }
            
            # Log unmatched market
            parsed = parse_kalshi_ticker(ticker)
            self.unmatched_log.append({
                "timestamp": timestamp.isoformat(),
                "ticker": ticker,
                "title": market.get("title"),
                "sport": parsed.get("sport") if parsed else None,
                "market_type": parsed.get("market_type") if parsed else None,
            })
        
        self._append_joined_row(joined_row)
    
    async def _update_oddsapi_periodically(self):
        """Periodically fetch OddsAPI data and refresh CSV files.
        
        Note: Matches are stable and don't need to be re-computed. The existing
        matches will automatically use the fresh OddsAPI data from the updated CSV files.
        """
        # Use ODDS_API_FETCH_INTERVAL from settings, default to 30 minutes (1800 seconds)
        fetch_interval = getattr(settings, 'ODDS_API_FETCH_INTERVAL', 1800.0)
        if fetch_interval <= 0:
            print("⚠️ OddsAPI periodic updates disabled (ODDS_API_FETCH_INTERVAL <= 0)")
            return  # Disabled
        
        print(f"⏰ Starting periodic OddsAPI updates (every {fetch_interval/60:.1f} minutes)")
        
        while self.running:
            try:
                await asyncio.sleep(fetch_interval)
                
                if not self.running:
                    break
                
                print(f"\n🔄 Periodic OddsAPI fetch (every {fetch_interval/60:.1f} minutes)...")
                
                # Fetch fresh OddsAPI data and overwrite CSV files
                # Matches remain unchanged - they'll automatically use the fresh data
                self._fetch_oddsapi_data(overwrite=True)
                
                print(f"✅ OddsAPI data refreshed. Existing matches will use updated prices.")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Error in periodic OddsAPI update: {e}")
                import traceback
                if settings.VERBOSE:
                    traceback.print_exc()
    
    async def start(self):
        """Start the collector."""
        self.running = True
        
        # Discover markets (includes OddsAPI fetch and matching)
        market_count = self.discover_markets()
        if market_count == 0:
            print("⚠️ No markets found, exiting")
            self.running = False
            return
        
        # Write initial snapshot with joined data
        print("💾 Writing initial snapshot with joined data...")
        timestamp = datetime.now(LOCAL_TZ)
        try:
            with self.markets_lock:
                for ticker, market in self.markets.items():
                    row = _market_to_row(market, timestamp)
                    self._append_row(row)
                    self._write_joined_row(market, timestamp)
            
            print(f"💾 Wrote initial snapshot of {market_count} markets (joined data)")
        except Exception as e:
            print(f"⚠️ Error writing initial snapshot: {e}")
            import traceback
            traceback.print_exc()
        
        # Start periodic OddsAPI updates
        print("🔄 Starting periodic OddsAPI updates...")
        oddsapi_task = asyncio.create_task(self._update_oddsapi_periodically())
        
        try:
            # Start WebSocket connection
            print("🔌 Connecting to Kalshi WebSocket...")
            await self._connection_loop()
        finally:
            # Cancel periodic task when done
            oddsapi_task.cancel()
            try:
                await oddsapi_task
            except asyncio.CancelledError:
                pass
    
    def _save_unmatched_markets(self):
        """Save unmatched markets log to CSV (can be called multiple times safely)."""
        try:
            if not self.unmatched_log:
                return
            
            if not hasattr(self, 'date_output_dir') or not self.date_output_dir:
                print("⚠️ Cannot save unmatched markets: date_output_dir not set")
                return
            
            unmatched_file = self.date_output_dir / f"unmatched_markets_{self.target_date.isoformat()}.csv"
            import pandas as pd
            df = pd.DataFrame(self.unmatched_log)
            df.to_csv(unmatched_file, index=False)
            print(f"📝 Saved {len(self.unmatched_log)} unmatched markets to {unmatched_file}")
        except Exception as e:
            print(f"⚠️ Error saving unmatched markets CSV: {e}")
            import traceback
            traceback.print_exc()
    
    async def stop(self):
        """Stop the collector and save unmatched markets log."""
        await super().stop()
        
        # Save unmatched markets log
        self._save_unmatched_markets()
        
        # Print final matching stats
        stats = self.matcher.get_stats()
        print(f"\n📊 Final matching statistics:")
        print(f"   Total attempted: {stats['total_attempted']}")
        print(f"   Matched: {stats['matched']}")
        print(f"   Unmatched: {stats['unmatched']}")
        print(f"   Unmatched count (unique): {stats['unmatched_count']}")
        print(f"   H2H matched: {stats['h2h_matched']}")
        print(f"   Spread matched: {stats['spread_matched']}")
        print(f"   Total matched: {stats['total_matched']}")
        print(f"   Match success rate: {stats['matched'] / max(stats['total_attempted'], 1) * 100:.1f}%")


async def main_async(args):
    """Async main function."""
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
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
    
    collector = JoinedCollector(target_date, sports, output_dir, runtime_seconds)
    
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
    import argparse
    parser = argparse.ArgumentParser(description="Collect joined Kalshi and OddsAPI market data")
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
