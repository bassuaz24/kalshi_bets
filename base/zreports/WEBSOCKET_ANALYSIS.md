# WebSocket Integration Analysis for Stop Loss Monitoring

## Current Rate Limiting Problem

### Current Stop Loss Check Frequency:
- **REST API Call Per Position**: `get_kalshi_markets(event_ticker, force_live=True)`
- **With 10 positions, checking every 2 seconds**: 10 API calls every 2 seconds = **300 calls/minute**
- **Kalshi Rate Limits**: Typically 60-100 requests/minute (varies by endpoint)
- **Problem**: ❌ Will hit rate limits quickly with multiple positions

### Current Market Data Fetching:
```python
# In check_stop_losses() - called every 2 seconds for each position
mkts = get_kalshi_markets(p.get("event_ticker", ""), force_live=True)
# This makes a REST API call: GET /trade-api/v2/markets?event_ticker={ticker}
```

## WebSocket Solution

According to [Kalshi's WebSocket documentation](https://docs.kalshi.com/getting_started/quick_start_websockets), we can subscribe to real-time ticker updates which provide:
- **Bid/Ask prices** (exactly what we need for stop loss checks)
- **Real-time updates** (no polling needed)
- **No rate limits** (streaming, not REST requests)
- **Multiple markets** in single subscription

### WebSocket Channels Available:

1. **`ticker`** - Real-time bid/ask updates for all markets (or specific markets)
2. **`orderbook_delta`** - Order book changes (more detailed than needed)
3. **`trades`** - Trade executions
4. **`fills`** - Your order fills (authenticated)

**For Stop Loss Checks, we need: `ticker` channel**

## Proposed WebSocket Integration

### Architecture:

```
┌─────────────────────────────────────────────────────────────┐
│ WebSocket Manager (Thread)                                  │
│ - Maintains single WebSocket connection                     │
│ - Subscribes to ticker updates for active positions        │
│ - Updates shared price cache in real-time                  │
│                                                              │
│ Subscriptions:                                               │
│ - Subscribe to ticker updates for all markets with         │
│   open positions                                            │
│ - Automatically subscribe when position opened              │
│ - Automatically unsubscribe when position closed            │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼ (Updates shared state)
┌─────────────────────────────────────────────────────────────┐
│ Price Cache (Shared State)                                  │
│ {                                                           │
│   "MARKET-TICKER-1": {                                      │
│     "yes_bid": 0.65,                                        │
│     "yes_ask": 0.66,                                        │
│     "last_update": timestamp                                │
│   },                                                         │
│   ...                                                       │
│ }                                                           │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼ (Reads from cache)
┌─────────────────────────────────────────────────────────────┐
│ Stop Loss Check Loop (Thread)                               │
│ - Checks positions every 2 seconds                          │
│ - Reads prices from cache (no API calls!)                  │
│ - Executes exits if triggered                               │
└─────────────────────────────────────────────────────────────┘
```

## Benefits of WebSocket Approach

### 1. No Rate Limits
- ✅ **WebSocket streaming** doesn't count against REST API rate limits
- ✅ Can check prices as frequently as needed (even every 0.1 seconds)
- ✅ Scales to 100+ positions without rate limit concerns

### 2. Real-Time Updates
- ✅ **Instant price updates** when market moves (faster than 2-second polling)
- ✅ Better price accuracy (no stale data)
- ✅ Faster stop loss execution (near-instant detection)

### 3. Efficiency
- ✅ **Single connection** for all markets
- ✅ Server pushes updates (no polling overhead)
- ✅ Lower latency than REST API calls

### 4. Cost Savings
- ✅ Reduces REST API usage (save for critical operations)
- ✅ Less bandwidth (only updates when prices change)

## Implementation Approach

### Step 1: WebSocket Manager Class

Create `kalshi/websocket_client.py`:

```python
class KalshiWebSocketClient:
    """Manages WebSocket connection for real-time market data."""
    
    def __init__(self):
        self.ws = None
        self.price_cache = {}  # Shared price cache
        self.subscribed_markets = set()
        self.message_id = 1
    
    async def connect(self):
        """Establish WebSocket connection with authentication"""
        # Connect to wss://api.elections.kalshi.com/trade-api/ws/v2
        # Include auth headers (same as REST API)
        pass
    
    async def subscribe_to_ticker(self, market_tickers: List[str]):
        """Subscribe to ticker updates for specific markets"""
        subscription = {
            "id": self.message_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"],
                "market_tickers": market_tickers
            }
        }
        # Send subscription
        pass
    
    async def process_messages(self):
        """Process incoming WebSocket messages and update cache"""
        async for message in websocket:
            data = json.loads(message)
            
            if data.get("type") == "ticker":
                market_ticker = data["data"]["market_ticker"]
                yes_bid = data["data"].get("yes_bid")  # May be in cents
                yes_ask = data["data"].get("yes_ask")
                
                # Update price cache
                self.price_cache[market_ticker] = {
                    "yes_bid": format_price(yes_bid),
                    "yes_ask": format_price(yes_ask),
                    "last_update": time.time()
                }
```

### Step 2: Update Stop Loss Checks to Use Cache

Modify `risk/stop_loss.py`:

```python
def check_stop_losses():
    """Check all positions for stop loss triggers - uses WebSocket price cache"""
    
    # Get prices from WebSocket cache (no API calls!)
    for p in state.positions:
        market_ticker = p.get("market_ticker")
        
        # Read from price cache instead of REST API
        price_data = websocket_client.price_cache.get(market_ticker)
        
        if not price_data:
            # Fallback: use REST API if not in cache
            mkts = get_kalshi_markets(...)
            continue
        
        current_price = price_data.get("yes_bid") or price_data.get("yes_ask")
        # ... rest of stop loss logic
```

### Step 3: Dynamic Subscription Management

```python
def subscribe_to_position_markets():
    """Subscribe to ticker updates for all active positions"""
    active_markets = {p.get("market_ticker") for p in state.positions 
                      if not p.get("settled", False)}
    
    # Subscribe to new markets
    new_markets = active_markets - websocket_client.subscribed_markets
    if new_markets:
        await websocket_client.subscribe_to_ticker(list(new_markets))
    
    # Unsubscribe from closed positions
    closed_markets = websocket_client.subscribed_markets - active_markets
    if closed_markets:
        await websocket_client.unsubscribe_from_markets(list(closed_markets))
```

## WebSocket Message Format

From the documentation, ticker messages look like:

```json
{
  "type": "ticker",
  "data": {
    "market_ticker": "CPI-22DEC-TN0.1",
    "yes_bid": 65,  // In cents (0-100)
    "yes_ask": 66,
    "no_bid": 34,
    "no_ask": 35,
    "last_price": 65,
    "volume": 1234,
    "timestamp": "2024-01-01T12:00:00Z"
  }
}
```

## Integration Points

### When Position Opened:
1. Position created in strategy engine
2. Add market ticker to WebSocket subscription
3. Start receiving real-time updates

### When Position Closed:
1. Position settled
2. Remove market ticker from WebSocket subscription
3. Clean up price cache entry

### When Stop Loss Check Runs:
1. Read price from cache (no API call)
2. Check against stop loss/take profit
3. Execute if triggered

## Rate Limiting Comparison

### Current Approach (REST API):
- **10 positions, checking every 2s**: 300 calls/minute
- **20 positions, checking every 2s**: 600 calls/minute
- **Rate limit**: ~60-100 calls/minute
- **Result**: ❌ Will hit rate limits with 2+ positions

### WebSocket Approach:
- **Subscriptions**: 1 subscription command per market (one-time)
- **Updates**: Server pushes (no rate limit)
- **Stop loss checks**: Read from cache (no API calls)
- **Result**: ✅ No rate limits, scales infinitely

## Recommended Architecture

```
Thread 1: Main Strategy Loop (30 seconds)
  ├── Collect Data
  ├── Compute Trades
  ├── Execute Trades
  └── Update WebSocket subscriptions (when positions change)

Thread 2: WebSocket Manager (Continuous)
  ├── Maintain connection
  ├── Process incoming messages
  ├── Update price cache
  └── Handle reconnections

Thread 3: Stop Loss Check (2 seconds)
  ├── Read prices from cache (no API calls!)
  ├── Check stop loss/take profit
  └── Execute exits if triggered

Thread 4: UI Updates (1 second)
  ├── Read from shared state
  └── Calculate metrics
```

## Implementation Details

### WebSocket Connection Management:

1. **Connection Lifecycle**:
   - Connect at startup
   - Maintain persistent connection
   - Automatic reconnection on disconnect
   - Exponential backoff for reconnection

2. **Subscription Management**:
   - Subscribe when position opened
   - Unsubscribe when position closed
   - Batch subscribe/unsubscribe operations
   - Handle subscription confirmations

3. **Price Cache Structure**:
   ```python
   price_cache = {
       "MARKET-TICKER-1": {
           "yes_bid": 0.65,
           "yes_ask": 0.66,
           "last_update": 1234567890.123,
           "stale_after": 60.0  # Consider stale after 60 seconds
       }
   }
   ```

4. **Fallback to REST API**:
   - If WebSocket disconnected, fall back to REST API
   - If price not in cache (new position), use REST API
   - If price is stale (>60s old), refresh via REST API

## Questions & Considerations

1. **WebSocket Connection Stability**: 
   - Need robust reconnection logic
   - Handle network interruptions
   - Graceful degradation to REST API

2. **Initial Price Loading**:
   - On startup, use REST API to get initial prices
   - Then switch to WebSocket updates
   - Or subscribe first, wait for snapshot

3. **Multiple Markets**:
   - Can subscribe to multiple markets in single subscription?
   - Or need separate subscriptions per market?
   - Documentation shows `market_tickers` as array - suggests batch support

4. **Ticker Channel vs Orderbook**:
   - `ticker` channel: Bid/ask prices (what we need)
   - `orderbook_delta`: Full orderbook changes (more data, not needed)
   - **Recommendation**: Use `ticker` channel (simpler, sufficient)

## Recommended Next Steps

1. **Create WebSocket Client**: Implement `kalshi/websocket_client.py`
2. **Update Stop Loss Checks**: Modify to read from price cache
3. **Add Subscription Management**: Subscribe/unsubscribe dynamically
4. **Add Reconnection Logic**: Handle disconnects gracefully
5. **Add Fallback**: Use REST API if WebSocket unavailable

## Code Structure

```
base/
  kalshi/
    websocket_client.py    # NEW: WebSocket connection manager
    websocket_price_cache.py  # NEW: Shared price cache with thread safety
  risk/
    stop_loss.py           # MODIFY: Read from cache instead of REST API
  app/
    loop.py                # MODIFY: Initialize WebSocket, manage subscriptions
```

## Summary

**Using WebSockets solves the rate limiting problem completely** while providing:
- ✅ Real-time price updates (faster than polling)
- ✅ No rate limits (streaming, not REST)
- ✅ Better efficiency (single connection vs many REST calls)
- ✅ Scalability (works with 100+ positions)

**This is the recommended approach** for high-frequency stop loss monitoring!
