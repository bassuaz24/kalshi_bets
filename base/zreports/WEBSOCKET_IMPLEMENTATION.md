# WebSocket-Based Stop Loss Monitoring Implementation

## Overview

This document describes the implementation of the WebSocket-based stop loss monitoring system integrated with a multi-threaded architecture that supports the user's proposed strategy flow.

## Architecture

### Multi-Threaded Design

The system now runs with **four independent threads**:

1. **Main Strategy Loop Thread** (`StrategyLoop`)
   - **Interval**: `STRATEGY_LOOP_INTERVAL` (default: 30 seconds)
   - **Responsibilities**:
     - Collect market data from OddsAPI and Kalshi
     - Discover new markets
     - Run strategy engine to compute optimal trades
     - Execute trades and create positions
     - Reconcile positions periodically (every `RECONCILE_INTERVAL` seconds)
     - Sync WebSocket subscriptions when positions change

2. **Stop Loss Monitoring Thread** (`StopLossMonitor`)
   - **Interval**: `STOP_LOSS_CHECK_INTERVAL` (default: 2 seconds)
   - **Responsibilities**:
     - Check all active positions for stop loss/take profit triggers
     - Uses **WebSocket price cache** for real-time prices (no REST API calls)
     - Places exit orders when triggers are hit
     - Monitors partial fills for exit orders
     - Syncs WebSocket subscriptions when positions change

3. **UI Update Thread** (`UIUpdate`)
   - **Interval**: `UI_UPDATE_INTERVAL` (default: 1 second)
   - **Responsibilities**:
     - Update performance metrics for UI consumption
     - Log metrics periodically (every 5 minutes)
     - Generate daily reports (once per day)

4. **WebSocket Client Thread** (`WebSocketClient`)
   - **Continuous**: Runs in background, maintains persistent connection
   - **Responsibilities**:
     - Connect to Kalshi WebSocket API
     - Authenticate using Kalshi API credentials
     - Subscribe to ticker updates for markets with active positions
     - Receive real-time price updates
     - Update thread-safe price cache
     - Handle reconnection with exponential backoff
     - Load initial prices via REST API on connection

## WebSocket Implementation Details

### Connection

- **URL**: `wss://api.elections.kalshi.com/trade-api/ws/v2` (live environment)
- **Authentication**: Uses Kalshi API key and private key signing (same as REST API)
- **Reconnection**: Exponential backoff (starts at 5s, max 60s)

### Price Cache

- **Thread-safe**: Uses `threading.RLock()` for concurrent access
- **Staleness check**: Prices older than `WEBSOCKET_PRICE_CACHE_STALE_SECS` (default: 60s) are considered stale
- **Fallback**: If WebSocket cache unavailable, stop loss monitoring falls back to REST API

### Subscription Strategy (Option A: Targeted)

- Subscribes **only to markets with active positions**
- Automatically syncs subscriptions when:
  - New positions are created (from strategy engine)
  - Positions are closed (from stop loss monitoring)
  - Positions are reconciled
- Unsubscribes from markets when positions are fully closed

### Initial Price Loading

- On WebSocket connection, loads initial prices for all active positions via REST API
- This ensures we have prices immediately while WebSocket subscriptions are being established
- Prevents gaps in price data during connection setup

## Message Format

Based on `auto_trades/live_data.py` example:

### Subscription Message
```json
{
  "id": 1,
  "cmd": "subscribe",
  "params": {
    "channels": ["ticker"],
    "market_tickers": ["MARKET-TICKER-1", "MARKET-TICKER-2"]
  }
}
```

### Ticker Update Message
```json
{
  "type": "ticker",
  "msg": {
    "market_ticker": "MARKET-TICKER-1",
    "yes_bid": 63,  // integer cents (e.g., 63 = $0.63)
    "yes_ask": 64,
    "volume": 12345
  }
}
```

## Integration Points

### Stop Loss Monitoring (`risk/stop_loss.py`)

Modified to:
1. First check WebSocket price cache via `get_websocket_client().get_price(market_ticker)`
2. Fall back to REST API if cache unavailable or stale
3. Use cached prices for trigger checks (much faster than REST API calls)

### Main Loop (`app/loop.py`)

Completely refactored to:
1. Start WebSocket client before worker threads
2. Coordinate three worker threads with proper synchronization
3. Sync WebSocket subscriptions after position changes
4. Use thread-safe locks (`_state_lock`) for shared state access

### Strategy Engine (`strategy/engine.py`)

No changes needed - continues to work as before, but now runs in a dedicated thread at fixed intervals.

## Configuration

New environment variables:

```bash
# Main strategy loop timing (seconds)
STRATEGY_LOOP_INTERVAL=30.0

# Stop loss monitoring timing (seconds)
STOP_LOSS_CHECK_INTERVAL=2.0

# UI/Performance update timing (seconds)
UI_UPDATE_INTERVAL=1.0

# Position reconciliation timing (seconds)
RECONCILE_INTERVAL=10.0

# WebSocket settings
WEBSOCKET_ENABLED=True
WEBSOCKET_RECONNECT_DELAY=5.0
WEBSOCKET_MAX_RECONNECT_DELAY=60.0
WEBSOCKET_PRICE_CACHE_STALE_SECS=60.0

# WebSocket URL (live environment)
KALSHI_WS_URL=wss://api.elections.kalshi.com/trade-api/ws/v2
```

## Benefits

1. **High-Frequency Stop Loss Checks**: 2-second intervals vs. 30-second main loop
2. **Real-Time Prices**: WebSocket provides instant price updates, no rate limiting concerns
3. **Predictable Timing**: Fixed intervals for all operations (no variable sleep times)
4. **Efficient**: Only subscribes to markets we actually need
5. **Resilient**: Automatic reconnection with exponential backoff
6. **Fallback**: REST API fallback if WebSocket unavailable
7. **Thread Safety**: All shared state access properly synchronized

## Thread Safety

All access to shared state (`state.positions`, `state.METRICS`, etc.) is protected by:
- `threading.RLock()` (`_state_lock`) in main loop
- Thread-safe locks within WebSocket client for price cache
- Atomic operations where possible

## Testing

To test the implementation:

1. **Start the bot**: `python3 -m app.loop`
2. **Verify WebSocket connection**: Look for "✅ Connected to Kalshi WebSocket" message
3. **Create a test position**: Via strategy engine (once implemented)
4. **Monitor subscriptions**: Check that WebSocket subscribes to the position's market
5. **Verify price updates**: Enable `VERBOSE=True` to see price update messages
6. **Test stop loss**: Set a stop loss and verify it triggers using WebSocket prices

## Known Limitations

1. **Market Discovery**: Placeholder implementation - needs to be filled in based on your strategy
2. **WebSocket Format**: Based on example code; actual Kalshi API may have variations
3. **Subscription Limits**: If you have many positions, may need to batch subscriptions
4. **Error Handling**: Some edge cases may need additional handling based on real-world usage

## Future Improvements

1. **Orderbook Data**: Could subscribe to `orderbook_delta` channel for more granular price data
2. **Trade Data**: Could subscribe to `trade` channel to see actual executions
3. **Fill Notifications**: Could subscribe to `fill` channel to get instant fill notifications
4. **Batch Subscriptions**: Optimize subscription management for large position sets
5. **Metrics**: Add WebSocket-specific metrics (connection uptime, message rates, etc.)
