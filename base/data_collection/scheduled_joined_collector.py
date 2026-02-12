"""
Scheduled joined Kalshi and OddsAPI data collector.

Similar to joined_collector but only subscribes to websockets 30 minutes before
each game's oddsapi_start_time. This saves storage by collecting data only from
game start to game end, rather than for all matched markets continuously.

- Same matching process as joined_collector
- OddsAPI fetch every 30 minutes (writes to data dir, not joined file until subscribed)
- Websocket subscriptions only start when current_time >= oddsapi_start_time - 30 min
- Uses intermittent checks (every 60s) to subscribe to new markets as they become eligible
- Same directories and format as joined_collector
"""

import asyncio
import re
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
import pytz

import os
import sys

# Add base directory to path
_BASE_ROOT = Path(__file__).parent.parent.absolute()
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from data_collection.kalshi_collector import KalshiCollector, CSV_COLUMNS, _market_to_row, _parse_time
from data_collection.market_matcher import MarketMatcher, parse_kalshi_ticker
from data_collection.oddsapi_client import (
    CST,
    fetch_odds,
    normalize_odds_data,
    save_market_data,
    save_skipped_games,
)
from data_collection.joined_collector import JoinedCollector, JOINED_CSV_COLUMNS
from config import settings

LOCAL_TZ = pytz.timezone("US/Eastern")

# Minutes before game start when we begin subscribing
SUBSCRIPTION_WINDOW_MINUTES = 30
# How often to check for new markets to subscribe (seconds)
SUBSCRIPTION_CHECK_INTERVAL = 300


def _parse_oddsapi_start_time(start_time_str: Any) -> Optional[datetime]:
    """
    Parse oddsapi start_time to timezone-aware datetime (Central).
    Handles string "2026-02-07 14:30:00 CST" or pandas Timestamp/datetime.
    """
    if start_time_str is None or (isinstance(start_time_str, float) and str(start_time_str) == "nan"):
        return None
    # Handle pandas Timestamp, datetime, etc. - convert to string
    s = str(start_time_str).strip()
    if not s or s == "nan" or s == "NaT":
        return None
    try:
        # Strip timezone suffix (CST, CDT, etc.) - interpret as America/Chicago
        s_clean = re.sub(r"\s+[A-Z]{3,4}$", "", s)
        # Handle pandas output like "2026-02-07 11:00:00" (no TZ suffix)
        s_clean = s_clean.strip()
        if "T" in s_clean:
            # ISO format from pandas
            parsed = datetime.fromisoformat(s_clean.replace("Z", "+00:00"))
            if parsed.tzinfo:
                return parsed.astimezone(CST)
            return CST.localize(parsed)
        dt_naive = datetime.strptime(s_clean, "%Y-%m-%d %H:%M:%S")
        return CST.localize(dt_naive)
    except (ValueError, TypeError):
        return None


class ScheduledJoinedCollector(JoinedCollector):
    """
    Collector that combines Kalshi and OddsAPI data, but only subscribes to
    websockets 30 minutes before each game's start time to save storage.
    """

    def __init__(
        self,
        target_date: date,
        sports: List[str],
        output_dir: Path,
        runtime_seconds: Optional[float] = None,
    ):
        super().__init__(target_date, sports, output_dir, runtime_seconds)

        # ticker -> oddsapi_start_time (datetime) for matched markets
        self.ticker_start_times: Dict[str, datetime] = {}
        # tickers we have subscribed to via websocket
        self.subscribed_tickers: Set[str] = set()
        self.subscribed_tickers_lock = threading.RLock()

    def _build_ticker_start_times(self) -> None:
        """Build map of ticker -> oddsapi_start_time for matched markets."""
        self.ticker_start_times.clear()
        with self.markets_lock:
            for ticker, market in self.markets.items():
                match_key = self.matcher.find_match(ticker, market)
                if not match_key:
                    continue
                parsed = parse_kalshi_ticker(ticker)
                if not parsed:
                    continue
                event_date = parsed.get("date")
                if not event_date:
                    continue
                oddsapi_rows = self.matcher.get_oddsapi_rows(ticker, match_key, event_date)
                if not oddsapi_rows:
                    continue
                first_row = oddsapi_rows[0]
                start_str = first_row.get("start_time")
                dt = _parse_oddsapi_start_time(start_str)
                if dt:
                    self.ticker_start_times[ticker] = dt

    def _get_tickers_to_subscribe(self) -> List[str]:
        """
        Return list of tickers that should be subscribed but aren't yet.
        A ticker is eligible when: now >= oddsapi_start_time - 30 minutes.
        """
        now = datetime.now(CST)
        cutoff = now + timedelta(minutes=SUBSCRIPTION_WINDOW_MINUTES)

        with self.markets_lock:
            valid_tickers = set(self.markets.keys())
        with self.subscribed_tickers_lock:
            already_subscribed = set(self.subscribed_tickers)

        to_subscribe = []
        for ticker, start_dt in self.ticker_start_times.items():
            if ticker in already_subscribed:
                continue
            if ticker not in valid_tickers:
                continue
            # Subscribe if we're within 30 min of start (start_dt <= now + 30min)
            if start_dt <= cutoff:
                to_subscribe.append(ticker)
        return to_subscribe

    async def _run_subscription_check(self) -> None:
        """
        Periodically check for new markets that have entered the subscription
        window and subscribe to them.
        """
        while self.running:
            try:
                if not self.running:
                    break

                # Clean up stale tickers (markets that have closed)
                with self.markets_lock:
                    valid = set(self.markets.keys())
                with self.subscribed_tickers_lock:
                    stale = self.subscribed_tickers - valid
                    if stale:
                        self.subscribed_tickers -= stale

                to_subscribe = self._get_tickers_to_subscribe()
                added = 0
                if to_subscribe and self.ws:
                    try:
                        ws_closed = getattr(self.ws, "closed", True)
                        if not ws_closed:
                            with self.subscribed_tickers_lock:
                                for t in to_subscribe:
                                    self.subscribed_tickers.add(t)
                            # Use add_to_existing=True when we have sid; else use new subscribe
                            await self._subscribe_to_markets(to_subscribe, add_to_existing=True)
                            added = len(to_subscribe)
                        else:
                            print(
                                f"   ⚠️ Cannot subscribe: ws.closed={ws_closed} "
                                f"_ticker_sid={self._ticker_sid}"
                            )
                    except Exception as e:
                        print(f"⚠️ Error subscribing to new markets: {e}")
                print(f"📋 Subscription check: {added} new markets added")

                # Diagnostic when 0 added (helps debug scheduling)
                if added == 0:
                    if not self.ticker_start_times:
                        print("   ⚠️ No markets have start times - check OddsAPI data & matching")
                    else:
                        now = datetime.now(CST)
                        cutoff = now + timedelta(minutes=SUBSCRIPTION_WINDOW_MINUTES)
                        with self.markets_lock:
                            valid = set(self.markets.keys())
                        with self.subscribed_tickers_lock:
                            subbed = set(self.subscribed_tickers)
                        not_valid = sum(1 for t in self.ticker_start_times if t not in valid)
                        already = sum(1 for t in self.ticker_start_times if t in subbed)
                        future = sum(1 for t, st in self.ticker_start_times.items() if t in valid and t not in subbed and st > cutoff)
                        in_window = sum(1 for t, st in self.ticker_start_times.items() if t in valid and t not in subbed and st <= cutoff)
                        if in_window > 0 or future > 0:
                            print(
                                f"   📊 {len(self.ticker_start_times)} with start times | "
                                f"now={now.strftime('%H:%M')} CT cutoff={cutoff.strftime('%H:%M')} CT | "
                                f"not_in_markets={not_valid} already_subscribed={already} "
                                f"future={future} in_window={in_window}"
                            )
                        if in_window > 0 and added == 0:
                            print("   ⚠️ Markets in window but not subscribed - WebSocket may be disconnected")

                await asyncio.sleep(SUBSCRIPTION_CHECK_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Error in subscription check: {e}")

    def discover_markets(self) -> int:
        """Discover markets, perform matching, and build start time map."""
        market_count = super().discover_markets()
        self._build_ticker_start_times()
        print(
            f"📅 Built start times for {len(self.ticker_start_times)} matched markets. "
            f"Websocket subscriptions will start {SUBSCRIPTION_WINDOW_MINUTES} min before each game."
        )
        return market_count

    async def _update_oddsapi_periodically(self):
        """Periodically fetch OddsAPI data and refresh ticker_start_times for subscription eligibility."""
        fetch_interval = getattr(settings, "ODDS_API_FETCH_INTERVAL", 1800.0)
        if fetch_interval <= 0:
            print("⚠️ OddsAPI periodic updates disabled (ODDS_API_FETCH_INTERVAL <= 0)")
            return
        print(f"⏰ Starting periodic OddsAPI updates (every {fetch_interval/60:.1f} minutes)")
        while self.running:
            try:
                await asyncio.sleep(fetch_interval)
                if not self.running:
                    break
                print(f"\n🔄 Periodic OddsAPI fetch (every {fetch_interval/60:.1f} minutes)...")
                self._fetch_oddsapi_data()
                # Refresh ticker_start_times so we pick up any games added in the latest fetch
                self._build_ticker_start_times()
                print(
                    f"✅ OddsAPI refreshed. {len(self.ticker_start_times)} markets with start times."
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Error in periodic OddsAPI update: {e}")

    async def _connection_loop(self):
        """WebSocket connection loop - subscribe only to eligible markets."""
        import websockets

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
                    self._ticker_sid = None  # Reset on reconnect; captured from "subscribed" response
                    print("✅ Connected to Kalshi WebSocket")

                    # Add newly eligible markets, then subscribe to all (subscribed_tickers ∩ markets)
                    newly_eligible = self._get_tickers_to_subscribe()
                    with self.subscribed_tickers_lock:
                        for t in newly_eligible:
                            self.subscribed_tickers.add(t)
                        with self.markets_lock:
                            to_sub = [
                                t
                                for t in self.subscribed_tickers
                                if t in self.markets
                            ]

                    if to_sub:
                        await self._subscribe_to_markets(to_sub)
                        print(
                            f"📡 Subscribed to {len(to_sub)} markets "
                            f"({len(newly_eligible)} newly eligible)"
                        )
                    else:
                        print(
                            f"⏳ No markets in subscription window yet. "
                            f"Checking every {SUBSCRIPTION_CHECK_INTERVAL}s for new games."
                        )

                    # Start REST update task
                    rest_task = asyncio.create_task(self._update_markets_via_rest())
                    # Start subscription check task (for markets entering the window)
                    sub_check_task = asyncio.create_task(self._run_subscription_check())

                    try:
                        async for message in websocket:
                            if not self.running:
                                break
                            await self._process_websocket_message(message)
                    finally:
                        rest_task.cancel()
                        sub_check_task.cancel()
                        try:
                            await rest_task
                        except asyncio.CancelledError:
                            pass
                        try:
                            await sub_check_task
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

async def main_async(args):
    """Async main function."""
    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    )
    sports = list(settings.SPORT_KEYS.keys())
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else settings.KALSHI_DATA_DIR / "kalshi_logs"
    )
    runtime_seconds = (
        float(settings.KALSHI_COLLECTOR_RUNTIME)
        if settings.KALSHI_COLLECTOR_RUNTIME
        else None
    )

    print(f"🎯 Target date: {target_date.isoformat()}")
    print(f"🎯 Sports: {', '.join(sports)}")
    print(f"📁 Output directory: {output_dir}")
    print(
        f"⏱️  Subscription window: {SUBSCRIPTION_WINDOW_MINUTES} min before game start"
    )
    if runtime_seconds:
        print(f"⏱️  Runtime: {runtime_seconds} seconds")
    else:
        print(f"⏱️  Runtime: Indefinite (Ctrl+C to stop)")

    # Prevent system sleep while collector is running
    try:
        from wakepy import keep

        print("💤 Preventing system sleep (using wakepy)...")
    except ImportError:
        print("⚠️  wakepy not installed - system may sleep if lid is closed")
        keep = None

    collector = ScheduledJoinedCollector(
        target_date, sports, output_dir, runtime_seconds
    )

    if keep:
        with keep.running():
            collector_task = asyncio.create_task(collector.start())
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
    else:
        collector_task = asyncio.create_task(collector.start())
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

    parser = argparse.ArgumentParser(
        description="Collect joined Kalshi/OddsAPI data only during game windows "
        "(30 min before start to game end)"
    )
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
