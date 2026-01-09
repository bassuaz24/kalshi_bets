# Partial Fill Analysis - Stop Loss Exit Orders

## Current Behavior with Partial Fills

### The Problem

**Scenario**: You have a position with 100 contracts, stop loss triggers, and only 50 contracts fill.

### What Currently Happens (Step-by-Step)

1. **Stop Loss Trigger** (`check_stop_losses()`)
   - Current price hits stop loss level
   - Calls `prepare_kalshi_order(market_ticker, "yes", current_price, 100, action="sell")`
   - Sets `closing_in_progress = True`
   - Sets `exit_reason = "stop_loss"`
   - Position `stake` still shows **100 contracts** (not updated)
   - **No order ID is stored or tracked**

2. **Order Submission**
   - Order submitted to Kalshi for 100 contracts
   - Kalshi receives order, but only 50 contracts can be filled immediately
   - Remaining 50 contracts stay as an open order on Kalshi

3. **Next Loop Iteration** (10-60 seconds later)
   - `check_stop_losses()` runs again
   - Position has `closing_in_progress = True` → **SKIPPED** (line 47 in `stop_loss.py`)
   - Position still has `stake = 100` locally

4. **Position Reconciliation** (`reconcile_positions()`)
   - Fetches live positions from Kalshi API
   - Finds position still exists with **50 contracts remaining**
   - Updates `last_seen_live` timestamp
   - But since position exists on Kalshi, it does NOT mark as settled
   - Position remains with:
     - `stake = 100` (local, incorrect)
     - `closing_in_progress = True` (prevents further stop loss checks)
     - Live: 50 contracts remaining on Kalshi

5. **Settlement Check** (`realize_if_settled()`)
   - Checks if position exists on Kalshi
   - Position DOES exist (50 contracts) → **NOT SETTLED**
   - No PnL calculation for the 50 filled contracts

### The Critical Issue

**The remaining 50 contracts are now in limbo:**

- ✅ Cannot be checked for stop loss again (`closing_in_progress = True`)
- ✅ Local stake (100) doesn't match live stake (50) - data inconsistency
- ✅ No tracking of the partial fill
- ✅ No mechanism to complete the exit
- ✅ The open order on Kalshi may expire, get cancelled, or fill later, but the system won't know
- ✅ PnL is incorrect - doesn't account for the 50 contracts that did exit

---

## Code Flow Analysis

### In `risk/stop_loss.py`:

```python
# Line 81-87: When stop loss triggers
if current_price <= stop_loss:
    print(f"🛑 Stop loss triggered...")
    prepare_kalshi_order(market_ticker, side, current_price, stake, action="sell")
    p["closing_in_progress"] = True  # ← Problem: Set before knowing if order filled
    p["exit_reason"] = "stop_loss"
    continue  # ← No order ID tracking, no fill monitoring
```

**Issues:**
1. No order ID captured from response
2. No call to `wait_for_fill_or_cancel()` 
3. `closing_in_progress` set optimistically (assumes full fill)
4. No adjustment of `stake` for partial fills

### In `positions/reconcile.py`:

```python
# Line 53-65: Mark as settled if position doesn't exist
for pos in new_positions:
    if pos.get("closing_in_progress", False):
        continue  # ← Skips positions that are closing
    
    key = (pos.get("market_ticker"), (pos.get("side") or "").lower())
    if key not in live_keys:
        pos["settled"] = True  # ← Only marks settled if position completely gone
        pos["stake"] = 0
```

**Issues:**
1. Skips positions with `closing_in_progress = True`, so partial fills aren't detected
2. Only marks as settled if position completely gone (all contracts exited)
3. Doesn't adjust local `stake` to match live `contracts` when partial fill occurs

### In `execution/settlement.py`:

```python
# Line 28-29: Check if position exists
key = (p.get("market_ticker"), (p.get("side") or "").lower())
if key not in live_keys:
    # Position no longer exists - it's been settled
```

**Issues:**
1. Same problem - only detects full settlement
2. Doesn't handle partial fills at all
3. Can't calculate PnL for partial fills

---

## What SHOULD Happen (Ideal Behavior)

### Proper Partial Fill Handling:

1. **After Order Submission:**
   - Extract order ID from response
   - Store order ID in position: `p["exit_order_id"] = order_id`
   - Call `wait_for_fill_or_cancel(order_id)` to monitor fill status

2. **Detect Partial Fill:**
   - If order fills partially (e.g., 50/100 contracts):
     - Update position `stake` to reflect remaining: `p["stake"] = 50`
     - Store filled quantity: `p["filled_qty"] = 50`
     - Calculate PnL for filled portion
     - Reset `closing_in_progress = False` to allow remaining position to be monitored
     - Continue monitoring remaining 50 contracts for stop loss

3. **Reconcile Positions:**
   - When `closing_in_progress = True`, check live positions
   - Compare local `stake` to live `contracts`
   - If different, adjust: `p["stake"] = live_contracts`
   - If live contracts < original stake, partial fill occurred

4. **Settlement:**
   - Track multiple exits (partial fills)
   - Calculate realized PnL incrementally
   - Only mark fully settled when all contracts exited

---

## Current State Impact

### Data Inconsistency:
- Local position: `stake = 100`
- Live Kalshi position: `contracts = 50`
- **Mismatch leads to incorrect:**
  - PnL calculations
  - Risk exposure calculations
  - Position size reporting

### Operational Issues:
- Remaining contracts not protected by stop loss
- No visibility into partial fills
- Cannot manually intervene (system thinks position is closing)
- Metrics and reporting are inaccurate

### Risk Exposure:
- If price continues to move against remaining position, no stop loss protection
- Exposure limits may be violated (system thinks 100 contracts exited, but 50 remain)
- Portfolio risk calculation is incorrect

---

## Recommended Fixes

### Option 1: Track Order IDs and Monitor Fills (Recommended)

```python
def check_stop_losses():
    # ... existing code ...
    
    if current_price <= stop_loss:
        result = prepare_kalshi_order(...)
        order_id = _extract_order_id(result.get("response"))
        
        if order_id:
            p["exit_order_id"] = order_id
            p["original_stake_on_exit"] = stake
            p["closing_in_progress"] = True
            
            # Wait for fill with partial fill support
            filled, status = wait_for_fill_or_cancel(order_id, timeout_secs=30.0)
            if filled:
                # Get actual filled quantity
                filled_qty = get_filled_quantity(order_id)  # Need to implement
                remaining_qty = stake - filled_qty
                
                if remaining_qty > 0:
                    # Partial fill - update position and continue monitoring
                    p["stake"] = remaining_qty
                    p["closing_in_progress"] = False  # Reset to monitor remaining
                    p["partial_fills"].append({
                        "qty": filled_qty,
                        "price": current_price,
                        "time": now_utc().isoformat()
                    })
                    # Calculate PnL for filled portion
                    realize_partial_fill(p, filled_qty, current_price)
                else:
                    # Full fill - settle position
                    p["settled"] = True
```

### Option 2: Improved Reconciliation (Simpler)

```python
def reconcile_positions():
    # ... existing code ...
    
    for pos in new_positions:
        # Handle positions in closing state
        if pos.get("closing_in_progress", False):
            key = (pos.get("market_ticker"), (pos.get("side") or "").lower())
            
            # Find live position
            live_pos = next((lp for lp in live if (lp["ticker"], lp["side"]) == key), None)
            
            if live_pos:
                live_qty = live_pos["contracts"]
                local_qty = pos.get("stake", 0)
                
                if live_qty < local_qty:
                    # Partial fill detected
                    filled_qty = local_qty - live_qty
                    pos["stake"] = live_qty  # Update to remaining
                    pos["closing_in_progress"] = False  # Reset to monitor remaining
                    # Log partial fill, calculate PnL
                elif live_qty == 0:
                    # Full fill
                    pos["settled"] = True
                    pos["stake"] = 0
            else:
                # Position completely gone - full fill
                pos["settled"] = True
                pos["stake"] = 0
```

### Option 3: Use Order Status API (Most Robust)

Implement proper order tracking using Kalshi's order status endpoints:
- Monitor order status via `GET /trade-api/v2/portfolio/orders?order_id={order_id}`
- Parse `filled_count` vs `remaining_count` from order response
- Update position incrementally as fills occur
- Handle order cancellations and rejections

---

## Immediate Workaround

Until proper partial fill handling is implemented, you could:

1. **Set shorter timeout on exit orders**: Use market orders instead of limit orders for exits
2. **Monitor positions more frequently**: Check every iteration, not just when `closing_in_progress = False`
3. **Manual intervention**: Periodically check for positions stuck in `closing_in_progress` state

---

## Testing Partial Fill Scenario

To test this issue:

1. Create a test position with large quantity (harder to fill fully)
2. Trigger stop loss when market has low liquidity
3. Monitor the position state after order submission
4. Check if local `stake` matches live Kalshi `contracts`
5. Verify that remaining contracts continue to be monitored for stop loss

---

## Summary

**Current behavior with partial fills is broken.** The system:
- ✅ Submits exit order correctly
- ❌ Doesn't track order ID or fill status
- ❌ Marks position as "closing" before confirmation
- ❌ Doesn't detect partial fills
- ❌ Doesn't adjust position size for remaining contracts
- ❌ Leaves remaining contracts unprotected (no stop loss monitoring)
- ❌ Produces incorrect PnL and risk calculations

**This is a critical bug that needs to be fixed before live trading with large positions or illiquid markets.**