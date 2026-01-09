"""
WebSocket client for real-time Kalshi market data.
Provides real-time ticker updates to avoid REST API rate limits.
"""

import asyncio
import json
import time
import threading
from typing import Dict, Any, Optional, Set, List
import websockets
from websockets.client import WebSocketClientProtocol

from config import settings
from kalshi.auth import load_private_key, sign_message
from kalshi.markets import format_price
from app import state


class KalshiWebSocketClient:
    """Manages WebSocket connection for real-time Kalshi market data."""
    
    def __init__(self):
        self.ws: Optional[WebSocketClientProtocol] = None
        self.price_cache: Dict[str, Dict[str, Any]] = {}  # Thread-safe dict (accessed with locks)
        self.price_cache_lock = threading.RLock()
        self.subscribed_markets: Set[str] = set()  # Markets we're subscribed to
        self.subscription_lock = threading.RLock()
        self.message_id = 1
        self.message_id_lock = threading.Lock()
        self.running = False
        self.connection_task: Optional[asyncio.Task] = None
        self.reconnect_delay = settings.WEBSOCKET_RECONNECT_DELAY
        self.loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _get_next_message_id(self) -> int:
        """Get next message ID (thread-safe)."""
        with self.message_id_lock:
            msg_id = self.message_id
            self.message_id += 1
            return msg_id
    
    def _create_auth_headers(self) -> Dict[str, str]:
        """Create authentication headers for WebSocket connection."""
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
    
    def get_price(self, market_ticker: str) -> Optional[Dict[str, float]]:
        """Get current price from cache (thread-safe).
        
        Args:
            market_ticker: Market ticker to get price for
        
        Returns:
            Dictionary with yes_bid and yes_ask, or None if not available/stale
        """
        with self.price_cache_lock:
            price_data = self.price_cache.get(market_ticker)
            if not price_data:
                return None
            
            # Check if stale
            last_update = price_data.get("last_update", 0)
            if time.time() - last_update > settings.WEBSOCKET_PRICE_CACHE_STALE_SECS:
                if settings.VERBOSE:
                    print(f"⚠️ Price cache stale for {market_ticker}")
                return None
            
            return {
                "yes_bid": price_data.get("yes_bid"),
                "yes_ask": price_data.get("yes_ask"),
            }
    
    def update_price_cache(self, market_ticker: str, yes_bid: Optional[float], yes_ask: Optional[float]):
        """Update price cache with new data (thread-safe)."""
        with self.price_cache_lock:
            self.price_cache[market_ticker] = {
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "last_update": time.time(),
            }
    
    async def _subscribe_to_markets(self, market_tickers: List[str]):
        """Subscribe to ticker updates for specific markets."""
        if not self.ws:
            return
        
        try:
            if self.ws.closed:
                return
        except AttributeError:
            pass
        
        if not market_tickers:
            return
        
        # Kalshi WebSocket subscription format (based on auto_trades/live_data.py)
        subscription = {
            "id": self._get_next_message_id(),
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"],  # Note: "channels" is plural, list format
                "market_tickers": market_tickers
            }
        }
        
        try:
            await self.ws.send(json.dumps(subscription))
            with self.subscription_lock:
                self.subscribed_markets.update(market_tickers)
            print(f"📡 Subscribed to ticker updates for {len(market_tickers)} markets: {', '.join(market_tickers[:5])}{'...' if len(market_tickers) > 5 else ''}")
        except Exception as e:
            print(f"⚠️ Error subscribing to markets: {e}")
            if settings.VERBOSE:
                import traceback
                traceback.print_exc()
    
    async def _unsubscribe_from_markets(self, market_tickers: List[str]):
        """Unsubscribe from ticker updates for specific markets."""
        if not self.ws or self.ws.closed:
            return
        
        if not market_tickers:
            return
        
        # Get subscription IDs (would need to track these, simplified for now)
        # For now, we'll just remove from our set - Kalshi will stop sending if we disconnect
        with self.subscription_lock:
            self.subscribed_markets.difference_update(market_tickers)
    
    async def _process_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "ticker":
                # Handle ticker update (format: {"type": "ticker", "msg": {...}} or {"type": "ticker", "data": {...}})
                ticker_data = data.get("msg") or data.get("data") or {}
                market_ticker = ticker_data.get("market_ticker")
                
                if not market_ticker:
                    return
                
                # Extract prices (Kalshi sends in cents as integers)
                yes_bid_raw = ticker_data.get("yes_bid")
                yes_ask_raw = ticker_data.get("yes_ask")
                
                yes_bid = format_price(yes_bid_raw) if yes_bid_raw is not None else None
                yes_ask = format_price(yes_ask_raw) if yes_ask_raw is not None else None
                
                # Update cache
                self.update_price_cache(market_ticker, yes_bid, yes_ask)
                
                if settings.VERBOSE:
                    print(f"📊 Price update: {market_ticker} | Bid: {yes_bid:.2% if yes_bid else 'N/A'} | Ask: {yes_ask:.2% if yes_ask else 'N/A'}")
            
            elif msg_type == "subscribed":
                if settings.VERBOSE:
                    print(f"✅ WebSocket subscription confirmed: {data}")
            
            elif msg_type == "error":
                error_data = data.get("msg") or data.get("data") or {}
                error_code = error_data.get("code")
                error_msg = error_data.get("msg") or error_data.get("message")
                print(f"❌ WebSocket error {error_code}: {error_msg}")
            
        except json.JSONDecodeError as e:
            print(f"⚠️ Error parsing WebSocket message: {e}")
        except Exception as e:
            print(f"⚠️ Error processing WebSocket message: {e}")
            if settings.VERBOSE:
                import traceback
                traceback.print_exc()
    
    async def _connection_loop(self):
        """Main WebSocket connection loop with reconnection."""
        while self.running:
            try:
                # Create authentication headers
                headers = self._create_auth_headers()
                
                if settings.VERBOSE:
                    print(f"🔌 Connecting to Kalshi WebSocket: {settings.KALSHI_WS_URL}")
                
                # Connect to WebSocket
                async with websockets.connect(
                    settings.KALSHI_WS_URL,
                    additional_headers=headers,
                    ping_interval=20,  # Send ping every 20 seconds
                    ping_timeout=10,   # Wait 10 seconds for pong
                ) as websocket:
                    self.ws = websocket
                    self.reconnect_delay = settings.WEBSOCKET_RECONNECT_DELAY  # Reset delay on successful connection
                    
                    print("✅ Connected to Kalshi WebSocket")
                    
                    # Load initial prices for active positions via REST API
                    await self._load_initial_prices()
                    
                    # Subscribe to markets with active positions
                    await self._sync_subscriptions()
                    
                    # Process messages
                    try:
                        async for message in websocket:
                            if not self.running:
                                break
                            await self._process_message(message)
                    except asyncio.CancelledError:
                        # Expected when shutting down
                        raise
                    except websockets.exceptions.ConnectionClosed:
                        if self.running:
                            raise  # Re-raise to trigger reconnection
                        # Otherwise, we're shutting down - exit cleanly
                        break
            
            except websockets.exceptions.ConnectionClosed:
                if self.running:
                    print(f"⚠️ WebSocket connection closed, reconnecting in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(
                        self.reconnect_delay * 2,
                        settings.WEBSOCKET_MAX_RECONNECT_DELAY
                    )
            
            except Exception as e:
                if self.running:
                    print(f"❌ WebSocket connection error: {e}")
                    if settings.VERBOSE:
                        import traceback
                        traceback.print_exc()
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(
                        self.reconnect_delay * 2,
                        settings.WEBSOCKET_MAX_RECONNECT_DELAY
                    )
    
    async def _load_initial_prices(self):
        """Load initial prices for active positions via REST API before switching to WebSocket."""
        from kalshi.markets import get_kalshi_markets
        
        # Get all active positions
        with threading.RLock():  # Thread-safe access to state.positions
            active_positions = [p for p in state.positions if not p.get("settled", False)]
        
        if not active_positions:
            return
        
        print(f"📡 Loading initial prices for {len(active_positions)} positions via REST API...")
        
        # Get unique market tickers
        market_tickers = {p.get("market_ticker") for p in active_positions if p.get("market_ticker")}
        
        # Group by event ticker to minimize API calls
        event_tickers = {p.get("event_ticker") for p in active_positions if p.get("event_ticker")}
        
        for event_ticker in event_tickers:
            try:
                markets = get_kalshi_markets(event_ticker, force_live=True)
                if not markets:
                    continue
                
                # Update cache with initial prices
                for market in markets:
                    market_ticker = market.get("ticker")
                    if not market_ticker or market_ticker not in market_tickers:
                        continue
                    
                    yes_bid = format_price(market.get("yes_bid"))
                    yes_ask = format_price(market.get("yes_ask"))
                    self.update_price_cache(market_ticker, yes_bid, yes_ask)
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.25)
            
            except Exception as e:
                if settings.VERBOSE:
                    print(f"⚠️ Error loading initial prices for {event_ticker}: {e}")
        
        print(f"✅ Loaded initial prices for {len(self.price_cache)} markets")
    
    async def _sync_subscriptions(self):
        """Synchronize WebSocket subscriptions with active positions."""
        if not self.ws:
            return
        
        try:
            if self.ws.closed:
                return
        except AttributeError:
            pass  # Some websocket implementations don't have closed attribute
        
        # Get markets that need subscription (thread-safe)
        with threading.RLock():
            active_positions = [p for p in state.positions if not p.get("settled", False)]
        
        required_markets = {p.get("market_ticker") for p in active_positions if p.get("market_ticker")}
        
        with self.subscription_lock:
            # Find markets to subscribe
            markets_to_subscribe = list(required_markets - self.subscribed_markets)
            
            # Find markets to unsubscribe (if any)
            markets_to_unsubscribe = list(self.subscribed_markets - required_markets)
        
        # Subscribe to new markets
        if markets_to_subscribe:
            # Batch subscribe (Kalshi supports multiple markets in one subscription)
            await self._subscribe_to_markets(markets_to_subscribe)
        
        # Unsubscribe from closed positions (simplified - just remove from set)
        if markets_to_unsubscribe:
            await self._unsubscribe_from_markets(markets_to_unsubscribe)
    
    def sync_subscriptions_sync(self):
        """Synchronous wrapper to sync subscriptions from non-async context."""
        if self.loop and self.loop.is_running() and not self.loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._sync_subscriptions(), self.loop)
            except Exception as e:
                if settings.VERBOSE:
                    print(f"⚠️ Error syncing subscriptions: {e}")
    
    async def start(self):
        """Start WebSocket connection in async loop."""
        if not settings.WEBSOCKET_ENABLED:
            print("⚠️ WebSocket disabled in configuration")
            return
        
        self.running = True
        self.loop = asyncio.get_event_loop()
        self.connection_task = asyncio.create_task(self._connection_loop())
        await self.connection_task
    
    async def stop(self):
        """Stop WebSocket connection."""
        self.running = False
        if self.connection_task and not self.connection_task.done():
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        print("🔌 WebSocket connection closed")


# Global WebSocket client instance
_websocket_client: Optional[KalshiWebSocketClient] = None
_websocket_thread: Optional[threading.Thread] = None


def get_websocket_client() -> KalshiWebSocketClient:
    """Get or create global WebSocket client instance."""
    global _websocket_client
    if _websocket_client is None:
        _websocket_client = KalshiWebSocketClient()
    return _websocket_client


def start_websocket_client():
    """Start WebSocket client in a separate thread."""
    global _websocket_thread, _websocket_client
    
    if not settings.WEBSOCKET_ENABLED:
        print("⚠️ WebSocket disabled in configuration, will use REST API fallback")
        return
    
    if _websocket_thread and _websocket_thread.is_alive():
        print("⚠️ WebSocket client already running")
        return
    
    client = get_websocket_client()
    
    def run_websocket():
        """Run WebSocket client in asyncio event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client.loop = loop
        try:
            loop.run_until_complete(client.start())
        except asyncio.CancelledError:
            # Expected when shutting down - don't print error
            pass
        except KeyboardInterrupt:
            # Allow keyboard interrupt to propagate
            raise
        except Exception as e:
            print(f"❌ WebSocket thread error: {e}")
            if settings.VERBOSE:
                import traceback
                traceback.print_exc()
        finally:
            try:
                # Cancel any pending tasks
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except (asyncio.CancelledError, RuntimeError):
                pass
            except Exception:
                if settings.VERBOSE:
                    import traceback
                    traceback.print_exc()
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
    
    _websocket_thread = threading.Thread(target=run_websocket, daemon=True, name="WebSocketClient")
    _websocket_thread.start()
    print("🚀 WebSocket client thread started")


def stop_websocket_client():
    """Stop WebSocket client."""
    global _websocket_client, _websocket_thread
    
    if _websocket_client:
        _websocket_client.running = False
        if _websocket_client.loop and _websocket_client.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(_websocket_client.stop(), _websocket_client.loop)
            except Exception as e:
                if settings.VERBOSE:
                    print(f"⚠️ Error stopping WebSocket: {e}")
    
    if _websocket_thread and _websocket_thread.is_alive():
        _websocket_thread.join(timeout=5.0)
    
    print("🛑 WebSocket client stopped")