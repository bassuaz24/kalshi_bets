# Stop Loss Implementation Walkthrough

This document explains how the stop loss mechanism works from data collection to order execution.

## Overview

The stop loss system monitors positions and automatically exits them when price movements trigger stop loss or take profit levels. Here's the complete flow:

---

## Step-by-Step Flow

### **Step 1: Position Creation with Stop Loss/Take Profit Values**

**Location**: `strategy/engine.py` - `run_engine()` function

When a trade is executed, the strategy engine creates a position object that includes:
- Entry price
- Quantity (stake)
- **Stop loss price** (from strategy computation)
- **Take profit price** (from strategy computation)

```python
position = {
    "market_ticker": market_ticker,
    "side": side,  # "yes" or "no"
    "entry_price": entry_price,
    "stake": quantity,
    "stop_loss": trade.get("stop_loss"),      # ← Set here
    "take_profit": trade.get("take_profit"),  # ← Set here
    "closing_in_progress": False,
    "settled": False,
    ...
}
```

**Key Point**: Stop loss and take profit values come from `compute_optimal_trade()`, which is currently a placeholder for your strategy logic.

---

### **Step 2: Position Storage**

**Location**: `positions/io.py` - `save_positions()`

The position is:
1. Added to `state.positions` (in-memory list)
2. Saved to `positions.json` file on disk

This ensures stop loss/take profit values persist across restarts.

---

### **Step 3: Main Loop Triggers Stop Loss Check**

**Location**: `app/loop.py` - `main()` function

Every loop iteration (every 10 seconds when active, or 60 seconds when idle):

```python
while state.algorithm_running:
    # ... other operations ...
    
    # Check stop losses and take profits
    if active_matches:
        check_stop_losses()  # ← Called here
    
    # ... rest of loop ...
```

**Frequency**: Checked on every main loop iteration, so roughly every 10-60 seconds depending on activity.

---

### **Step 4: Stop Loss Check Function**

**Location**: `risk/stop_loss.py` - `check_stop_losses()` function

This function iterates through all open positions:

```python
def check_stop_losses():
    for p in state.positions:
        # Skip settled or already-closing positions
        if p.get("settled", False) or p.get("closing_in_progress", False):
            continue
        
        market_ticker = p.get("market_ticker")
        stop_loss = p.get("stop_loss")
        take_profit = p.get("take_profit")
        
        # ... price checking and execution ...
```

---

### **Step 5: Fetch Current Market Price**

**Location**: `risk/stop_loss.py` lines 58-71

For each position, the system:

1. **Fetches live market data**:
   ```python
   mkts = get_kalshi_markets(p.get("event_ticker", ""), force_live=True)
   ```

2. **Finds the specific market**:
   ```python
   m = next((m for m in mkts if m.get("ticker") == market_ticker), None)
   ```

3. **Extracts current prices**:
   ```python
   yes_bid = format_price(m.get("yes_bid"))  # Best bid price
   yes_ask = format_price(m.get("yes_ask"))  # Best ask price
   current_price = yes_bid if yes_bid is not None else yes_ask
   ```

**API Call**: 
- **Endpoint**: `GET /trade-api/v2/markets?event_ticker={event_ticker}`
- **Requires**: Kalshi API authentication
- **Returns**: List of active markets with bid/ask prices in cents (0-100)
- **Price Conversion**: Prices are converted from cents (0-100) to decimals (0-1) via `format_price()`

**Key Point**: `force_live=True` ensures we get the most current prices, bypassing any cache.

---

### **Step 6: Price Comparison Logic**

**Location**: `risk/stop_loss.py` lines 73-99

For a **long YES position** (most common case):

```python
side = (p.get("side") or "").lower()
entry_price = float(p.get("effective_entry", p.get("entry_price", 0.0)))
stake = int(p.get("stake", 0))

# Check stop loss
if stop_loss is not None:
    if side == "yes":
        # Long YES: stop loss if price falls BELOW stop_loss
        if current_price <= stop_loss:
            # Trigger exit
```

**Example**:
- Entry: 0.65 (65%)
- Stop Loss: 0.60 (60%)
- Current Price: 0.58 (58%)
- **Result**: 0.58 <= 0.60 → **STOP LOSS TRIGGERED** ✅

**Take Profit** (similar logic):
```python
if take_profit is not None:
    if side == "yes":
        # Long YES: take profit if price rises ABOVE take_profit
        if current_price >= take_profit:
            # Trigger exit
```

---

### **Step 7: Order Execution**

**Location**: `risk/stop_loss.py` lines 82-87 (stop loss) or 94-99 (take profit)

When a trigger is detected:

```python
print(f"🛑 Stop loss triggered for {market_ticker} at {current_price:.2%} (stop: {stop_loss:.2%})")

# Exit position by selling at current market price
prepare_kalshi_order(
    market_ticker=market_ticker,
    side=side,              # "yes"
    price=current_price,    # Current market price (bid or ask)
    quantity=stake,         # Full position size
    action="sell"           # ← SELL to exit
)

# Mark position as closing to prevent duplicate checks
p["closing_in_progress"] = True
p["exit_reason"] = "stop_loss"  # or "take_profit"
```

**Key Details**:
- **Action**: `"sell"` (exits the long position)
- **Price**: Uses current market price (bid for YES positions)
- **Quantity**: Full position size (stake)
- **Order Type**: Limit order (default)

---

### **Step 8: Order Preparation**

**Location**: `kalshi/orders.py` - `prepare_kalshi_order()` function

The order is prepared:

```python
def prepare_kalshi_order(
    market_ticker, side, price, quantity,
    order_type="limit", action="buy"
):
    path = "/trade-api/v2/portfolio/orders"
    headers = kalshi_headers("POST", path)  # Auth headers
    
    payload = {
        "ticker": market_ticker,
        "action": action.lower(),        # "sell"
        "side": side.lower(),            # "yes"
        "count": int(quantity),          # Contract count
        "type": order_type,              # "limit"
        "yes_price": int(round(price * 100)),  # Convert to cents
        "client_order_id": str(uuid.uuid4()),
    }
```

**Price Conversion**: 
- Input: Decimal (0.58 = 58%)
- Output: Cents (58)
- Formula: `int(round(price * 100))`

---

### **Step 9: Order Submission**

**Location**: `kalshi/orders.py` lines 54-67

```python
if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
    # Send live order to Kalshi API
    response = SESSION.post(
        settings.KALSHI_BASE_URL + path,  # Full URL
        headers=headers,                   # Auth headers
        json=payload,                      # Order payload
        timeout=10
    )
    return {"response": response, "payload": payload}
else:
    # Simulation mode - just log, don't submit
    print("🧪 SAFE MODE: Order preview only, not submitted.")
    return {"response": None, "payload": payload}
```

**API Call**:
- **Method**: `POST`
- **Endpoint**: `https://trading-api.kalshi.com/trade-api/v2/portfolio/orders`
- **Authentication**: Via `kalshi_headers()` which includes:
  - `KALSHI-ACCESS-KEY`
  - `KALSHI-ACCESS-SIGNATURE` (RSA-PSS signed timestamp + method + path)
  - `KALSHI-ACCESS-TIMESTAMP`
- **Response**: Order confirmation with order ID

---

### **Step 10: Position State Update**

**Location**: `risk/stop_loss.py` lines 85-86

After order submission:

```python
p["closing_in_progress"] = True  # Prevent duplicate triggers
p["exit_reason"] = "stop_loss"   # Track why it was closed
```

**Then**:
```python
save_positions()  # Persist to disk at end of function
```

This ensures:
1. Position won't be checked again this iteration
2. Reason for exit is tracked
3. State is saved to `positions.json`

---

### **Step 11: Order Fill Monitoring (Future Enhancement)**

**Location**: `kalshi/orders.py` - `wait_for_fill_or_cancel()` function

Currently, the stop loss check doesn't wait for order fills. However, the infrastructure exists:

```python
def wait_for_fill_or_cancel(order_id: str, timeout_secs: float = 30.0):
    """Wait for an order to fill or timeout and cancel it."""
    # Polls order status every 1 second
    # Returns (filled: bool, status: str)
```

**Note**: This isn't currently called in the stop loss flow, but could be added for confirmation.

---

### **Step 12: Position Settlement**

**Location**: `execution/settlement.py` - `realize_if_settled()` function

Later in the main loop (after stop loss check):

```python
if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
    reconcile_positions()      # Sync with live Kalshi positions
    realize_if_settled()       # Calculate realized PnL
```

This function:
1. Checks if position no longer exists on Kalshi (been filled)
2. Calculates realized PnL
3. Updates `state.realized_pnl`, `state.wins`, `state.losses`
4. Moves position to `state.closed_trades`

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Strategy Engine (run_engine)                            │
│    - Computes stop_loss and take_profit values             │
│    - Creates position with these values                    │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Position Storage (save_positions)                       │
│    - Saves to state.positions                              │
│    - Persists to positions.json                            │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Main Loop (app/loop.py)                                 │
│    - Calls check_stop_losses() every iteration             │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Stop Loss Check (check_stop_losses)                     │
│    - Iterates through state.positions                      │
│    - For each position:                                    │
│      a. Fetches current market price (get_kalshi_markets)  │
│      b. Compares price to stop_loss/take_profit           │
│      c. If triggered: calls prepare_kalshi_order("sell")   │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Order Execution (prepare_kalshi_order)                  │
│    - Builds order payload                                   │
│    - POST to Kalshi API                                    │
│    - Returns order response                                │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Position Update                                          │
│    - Sets closing_in_progress = True                       │
│    - Sets exit_reason = "stop_loss"                        │
│    - Saves to positions.json                               │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. Settlement (realize_if_settled)                         │
│    - Detects filled order                                  │
│    - Calculates realized PnL                               │
│    - Updates wins/losses                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Implementation Details

### **Price Resolution Priority**
When fetching current price:
1. Try `yes_bid` first (best bid)
2. Fallback to `yes_ask` if bid unavailable
3. Skip if both unavailable

### **Order Price Selection**
- **Exit orders use current market price** (not the stop loss price itself)
- This ensures the order can execute quickly
- For stop loss: uses bid price (lower, but guaranteed execution)
- Risk: May get worse fill than stop loss level

### **Duplicate Prevention**
- `closing_in_progress` flag prevents multiple exit orders for same position
- Flag is set immediately after order preparation
- Prevents race conditions in main loop

### **Error Handling**
- If market data fetch fails → skip position, continue with others
- If order submission fails → position remains open, will retry next iteration
- All errors are logged with `settings.VERBOSE` flag

---

## Current Limitations & Future Improvements

1. **No Order Fill Confirmation**: Currently doesn't wait for order to fill before marking as closed
   - **Solution**: Call `wait_for_fill_or_cancel()` after order submission

2. **Uses Market Price, Not Stop Price**: Executes at current bid/ask, which may be worse than stop loss
   - **Solution**: Could use limit order at stop loss price, but may not fill

3. **No Trailing Stop**: Stop loss is static, doesn't adjust with favorable price movement
   - **Solution**: Implement trailing stop logic in `check_stop_losses()`

4. **Single Check Per Loop**: Only checked once per main loop iteration (10-60s)
   - **Solution**: Could add dedicated high-frequency check for stop losses

5. **No Partial Fills**: Assumes full position exits
   - **Solution**: Track partial fills and adjust stake accordingly

---

## Example Scenario

**Initial Trade**:
- Market: "NFL-TEAMWINS-20250101-Y"
- Entry: 0.65 (65%)
- Quantity: 100 contracts
- Stop Loss: 0.60 (60%)
- Take Profit: 0.75 (75%)

**Main Loop Iteration 1** (10 seconds later):
- Fetches market: Current price = 0.62 (62%)
- Check: 0.62 <= 0.60? → No
- Check: 0.62 >= 0.75? → No
- **Result**: Position remains open

**Main Loop Iteration 2** (10 seconds later):
- Fetches market: Current price = 0.59 (59%)
- Check: 0.59 <= 0.60? → **YES** ✅
- **Action**: 
  - Prints: `"🛑 Stop loss triggered for NFL-TEAMWINS-20250101-Y at 59.00% (stop: 60.00%)"`
  - Creates sell order: 100 contracts @ 0.59
  - Sets `closing_in_progress = True`
  - Saves position to file

**Order Submission**:
- POST to Kalshi: `{"ticker": "NFL-TEAMWINS-20250101-Y", "action": "sell", "side": "yes", "count": 100, "yes_price": 59}`
- Kalshi responds with order ID

**Next Loop Iteration**:
- Position has `closing_in_progress = True` → skipped in check
- `reconcile_positions()` detects order filled
- `realize_if_settled()` calculates PnL: (0.59 - 0.65) * 100 = -$6.00 (loss)
- Position marked as `settled = True`

---

## Configuration

Stop loss behavior is controlled by:

- `settings.REFRESH_ACTIVE`: How often to check when positions are active (default: 10s)
- `settings.REFRESH_IDLE`: How often to check when no positions (default: 60s)
- `settings.PLACE_LIVE_KALSHI_ORDERS`: "YES" for live, anything else for simulation
- `settings.VERBOSE`: Enable detailed logging

---

This completes the stop loss implementation walkthrough. The system is designed to be robust, persistent, and ready for your strategy implementation!