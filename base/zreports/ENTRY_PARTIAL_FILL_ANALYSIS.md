# Entry Trade Partial Fill Analysis

## Current Behavior

**Entry trades currently do NOT properly handle partial fills.** This is a critical gap compared to exit trades.

### Current Flow (Entry Trades)

**Location**: `strategy/engine.py` - `run_engine()` function (lines 122-156)

1. **Order Placed**: Calls `safe_prepare_kalshi_order()` to place entry order
2. **Position Created Immediately**: Creates position object with FULL quantity requested
   ```python
   position = {
       "stake": quantity,  # ← Uses FULL quantity, not actual filled quantity
       "entry_price": entry_price,
       ...
   }
   state.positions.append(position)  # ← Added to positions immediately
   ```
3. **No Fill Confirmation**: Does NOT wait for order to fill
4. **No Order ID Tracking**: Does NOT extract or store order ID from response
5. **No Partial Fill Detection**: Assumes full fill occurred

### The Problem

If you place an order for 100 contracts and only 50 fill:

**What Currently Happens:**
- ❌ Position created with `stake = 100` (incorrect - should be 50)
- ❌ No order ID tracked (can't check fill status later)
- ❌ No wait for fill confirmation
- ❌ System thinks you have 100 contracts when you only have 50
- ❌ Position reconciliation will eventually catch this, but only on next loop iteration
- ❌ Risk calculations are incorrect until reconciliation occurs
- ❌ Stop loss monitoring uses wrong position size

**Data Inconsistency:**
```
Local position: stake = 100
Live Kalshi: contracts = 50
Mismatch until reconciliation runs (10-60 seconds later)
```

---

## How It SHOULD Work (Reference: myles_repo)

Looking at `myles_repo/strategy/engine_core.py` (lines 2214-2253), the reference implementation:

1. **Extract Order ID**:
   ```python
   order_id, client_oid = _extract_order_id(resp)
   if not order_id:
       print("⚠️ Could not parse order_id; skipping position.")
       continue
   ```

2. **Wait for Fill**:
   ```python
   status, filled_qty = wait_for_fill_or_cancel(
       order_id,
       client_order_id=client_oid,
       timeout_s=ORDER_FILL_TIME,
       poll_s=1.0,
       expected_count=quantity,
       require_full=False,  # ← Accept partial fills
       verify_ticker=market.get("ticker"),
       verify_side="yes"
   )
   ```

3. **Check Fill Status**:
   ```python
   if filled_qty <= 0:
       # Order didn't fill - skip position creation
       continue
   
   if status == "filled" and filled_qty > 0:
       # Filled (fully or partially)
       pass
   ```

4. **Create Position with ACTUAL Fill Quantity**:
   ```python
   position["stake"] = filled_qty  # ← Use actual filled quantity
   commit_trade_and_persist(position, order_id, filled_qty)
   ```

**Key Differences:**
- ✅ Waits for fill confirmation
- ✅ Tracks order ID
- ✅ Only creates position with actual filled quantity
- ✅ Handles partial fills correctly
- ✅ No data inconsistency

---

## Recommended Fix

### Option 1: Wait for Fill Before Creating Position (Recommended)

Modify `strategy/engine.py` to wait for fill confirmation:

```python
# Execute trade
try:
    side = trade.get("side", "yes")
    max_total_contracts = max_quantity_with_cap(
        entry_price,
        exposure * 1.1
    )
    
    result = safe_prepare_kalshi_order(
        market_ticker,
        side,
        entry_price,
        quantity,
        max_total_contracts=max_total_contracts,
        action="buy"
    )
    
    if result is None:
        print(f"⚠️ Trade execution failed for {market_ticker}")
        continue
    
    # Extract order ID
    from kalshi.orders import _extract_order_id, wait_for_fill_or_cancel
    order_id = _extract_order_id(result.get("response"))
    
    if not order_id:
        print(f"⚠️ Could not extract order ID for {market_ticker}, skipping position")
        continue
    
    # Wait for fill (with timeout)
    fill_timeout = 30.0  # seconds
    status, filled_qty = wait_for_fill_or_cancel(
        order_id,
        timeout_secs=fill_timeout,
        require_full=False  # Accept partial fills
    )
    
    if filled_qty <= 0:
        print(f"⚠️ Entry order did not fill for {market_ticker} (status: {status})")
        continue
    
    # Only create position with actual filled quantity
    if filled_qty < quantity:
        print(f"📊 Partial fill on entry: {market_ticker} - {filled_qty}/{quantity} contracts filled")
    
    # Record position with ACTUAL filled quantity
    position = {
        "match": match.get("match", ""),
        "side": side,
        "event_ticker": event_ticker,
        "market_ticker": market_ticker,
        "entry_price": entry_price,
        "effective_entry": entry_price,
        "stake": filled_qty,  # ← Use actual filled quantity
        "entry_time": now_utc().isoformat(),
        "entry_order_id": order_id,  # ← Track order ID
        "original_order_quantity": quantity,  # ← Track what was ordered
        "stop_loss": trade.get("stop_loss"),
        "take_profit": trade.get("take_profit"),
        "settled": False,
        "closing_in_progress": False,
        "odds_prob": 0.5,
    }
    
    state.positions.append(position)
    state.METRICS["orders_placed"] += 1
    state.METRICS["orders_filled"] += 1 if status == "filled" else 0
    
    print(f"✅ Position created: {market_ticker} {side.upper()} x{filled_qty} @ {entry_price:.2%} | "
          f"SL: {trade.get('stop_loss'):.2%} | TP: {trade.get('take_profit'):.2%}")
```

### Option 2: Track Order ID and Handle in Reconciliation (Less Ideal)

Keep current flow but track order ID and handle partial fills in reconciliation:

```python
# After placing order
order_id = _extract_order_id(result.get("response"))

position = {
    ...
    "entry_order_id": order_id,  # Track for later
    "entry_order_quantity": quantity,  # Track requested quantity
    "stake": quantity,  # Assume full fill initially
    ...
}

# In reconciliation: Check if order filled partially
# Compare entry_order_quantity to live contracts
```

**Problems with Option 2:**
- Data inconsistency exists until reconciliation
- Risk calculations are wrong during the gap
- More complex reconciliation logic needed

---

## Current Impact

### Data Inconsistency Window

**Timeline:**
1. T+0s: Entry order placed, position created with `stake = 100`
2. T+5s: Only 50 contracts actually fill on Kalshi
3. T+10-60s: Next reconciliation runs, detects mismatch
4. T+10-60s: Position updated to `stake = 50`

**During this window:**
- ❌ Risk calculations assume 100 contracts
- ❌ Stop loss monitoring uses wrong position size
- ❌ Exposure limits may be violated
- ❌ PnL calculations are incorrect
- ❌ Position summary shows wrong quantities

### Reconciliation as Fallback

The system DOES eventually catch this via `reconcile_positions()`:
- Compares local `stake` to live Kalshi `contracts`
- Updates position to match live quantity
- But this happens 10-60 seconds after the fact

---

## Comparison: Entry vs Exit Trades

| Feature | Entry Trades (Current) | Exit Trades (Fixed) |
|---------|------------------------|---------------------|
| Order ID Tracking | ❌ No | ✅ Yes |
| Fill Confirmation | ❌ No | ✅ Yes |
| Partial Fill Detection | ❌ Via reconciliation only | ✅ Immediate + reconciliation |
| Position Size Accuracy | ❌ Initially wrong | ✅ Always accurate |
| Wait for Fill | ❌ No | ✅ Yes (5s immediate + ongoing) |
| Data Consistency | ❌ Delayed (10-60s) | ✅ Immediate |

---

## Recommended Implementation

I recommend implementing **Option 1** (wait for fill before creating position) because:

1. ✅ **Data Consistency**: Position size is always accurate from creation
2. ✅ **Risk Accuracy**: Risk calculations are correct immediately
3. ✅ **Simpler Logic**: No need for complex reconciliation fallbacks
4. ✅ **Matches Best Practices**: Same pattern as reference implementation
5. ✅ **Prevents Issues**: No window of incorrect data

**Trade-offs:**
- Slightly slower position creation (must wait for fill confirmation)
- Blocks strategy engine during fill wait (but only 30 seconds max)

---

## Implementation Notes

When implementing the fix:

1. **Timeout Handling**: Use a reasonable timeout (30 seconds is good)
2. **Partial Fill Acceptance**: Set `require_full=False` to accept partial fills
3. **Order Tracking**: Store `entry_order_id` for audit trail
4. **Metrics**: Track partial fills separately from full fills
5. **Logging**: Log partial fills clearly for monitoring

**Example Metrics to Track:**
```python
state.METRICS["entry_orders_placed"] += 1
state.METRICS["entry_orders_full_filled"] += 1 if filled_qty == quantity else 0
state.METRICS["entry_orders_partial_filled"] += 1 if 0 < filled_qty < quantity else 0
state.METRICS["entry_orders_failed"] += 1 if filled_qty == 0 else 0
```

---

## Summary

**Current State**: Entry trades do NOT properly handle partial fills. Position is created with requested quantity, not actual filled quantity. Reconciliation eventually fixes this, but there's a 10-60 second window of data inconsistency.

**Recommended Fix**: Implement Option 1 - wait for fill confirmation before creating position, similar to how exit trades now work. This ensures data consistency from the moment the position is created.

**Priority**: **HIGH** - This affects risk calculations, position sizing, and stop loss monitoring accuracy.
