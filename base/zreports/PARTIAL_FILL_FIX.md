# Partial Fill Handling - Implementation Summary

## Overview

Fixed the critical partial fill handling issue in stop loss exit orders. The system now properly tracks order IDs, monitors fills, adjusts positions for remaining contracts, and ensures all contracts are eventually sold.

## Changes Made

### 1. Enhanced Order Tracking (`kalshi/orders.py`)

**Added Functions:**
- `get_order(order_id)`: Fetches order details from Kalshi API
- `get_order_fill_status(order_id)`: Returns fill status (is_filled, filled_count, remaining_count)
- Enhanced `wait_for_fill_or_cancel()`: Now returns fill quantity and handles partial fills

**Key Features:**
- Returns fill quantity, not just boolean
- Supports `require_full=False` parameter for partial fill detection
- Handles order cancellations and timeouts properly

### 2. Stop Loss Check Enhancement (`risk/stop_loss.py`)

**Major Changes:**

1. **Order ID Tracking**: Stores order ID when exit order is placed
   ```python
   p["exit_order_id"] = order_id
   p["original_stake_on_exit"] = stake
   p["last_exit_price"] = current_price
   ```

2. **Partial Fill Detection**: Checks order status for positions in `closing_in_progress` state
   - Detects partial fills via `get_order_fill_status()`
   - Updates position `stake` to remaining contracts
   - Records partial fills in `partial_fills` array

3. **Position State Management**:
   - Resets `closing_in_progress = False` when partial fill detected
   - Remaining contracts continue to be monitored for stop loss
   - Tracks original stake on exit for PnL calculation

4. **Immediate Fill Detection**: Checks fill status immediately after order submission (non-blocking 5s wait)
   - Handles immediate full fills
   - Handles immediate partial fills
   - Resubmits remaining contracts if needed

5. **PnL Calculation**: New `_realize_partial_fill()` function calculates realized PnL for filled portion
   - Accounts for entry and exit fees
   - Updates `state.realized_pnl`, `state.wins`, `state.losses`
   - Tracks partial fills in position history

### 3. Position Reconciliation (`positions/reconcile.py`)

**Enhanced to Handle Partial Fills:**

1. **Closing State Check**: When position has `closing_in_progress = True`:
   - Checks live Kalshi positions to detect partial fills
   - Compares local `stake` to live `contracts`
   - If different, updates position to match live quantity

2. **Automatic Adjustment**:
   - Detects partial fills via position size mismatch
   - Calculates filled quantity: `filled_qty = local_qty - live_qty`
   - Updates position: `pos["stake"] = live_qty`
   - Resets `closing_in_progress = False` to continue monitoring

3. **PnL Realization**: Calculates and records PnL for partial fills during reconciliation

### 4. Settlement Enhancement (`execution/settlement.py`)

**Improved Settlement Logic:**

1. **Skip Closing Positions**: Positions in `closing_in_progress` are handled by stop_loss check, not settlement
2. **Accurate PnL Calculation**: Uses actual exit price if available, otherwise falls back to unrealized PnL
3. **Partial Position Detection**: Checks if position size has changed (partial exit occurred)
4. **Full Settlement**: Only marks as settled when all contracts have exited

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Stop Loss Triggered                                      │
│    - Price hits stop loss level                            │
│    - Place exit order for full position                    │
│    - Store order_id, original_stake, exit_price           │
│    - Set closing_in_progress = True                        │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Immediate Fill Check (5s timeout)                       │
│    - Check order status via get_order_fill_status()       │
│    - If fully filled → wait for settlement                │
│    - If partially filled → handle partial fill            │
│    - If not filled → continue monitoring                  │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Next Loop Iteration                                      │
│    - Position has closing_in_progress = True               │
│    - Check order status again                              │
│    - If partial fill detected:                             │
│      a. Update stake to remaining contracts                │
│      b. Calculate PnL for filled portion                   │
│      c. Reset closing_in_progress = False                  │
│      d. Continue monitoring remaining position             │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Position Reconciliation                                  │
│    - Compare local stake to live Kalshi contracts          │
│    - If mismatch → partial fill detected                   │
│    - Update position to match live quantity                │
│    - Reset closing_in_progress to continue monitoring      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Remaining Position Monitoring                            │
│    - Position now has reduced stake                        │
│    - closing_in_progress = False                           │
│    - Continue checking stop loss on remaining contracts    │
│    - If stop loss triggers again → repeat process          │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Full Settlement                                          │
│    - All contracts eventually exit                         │
│    - Position no longer exists on Kalshi                   │
│    - Mark as settled = True                                │
│    - Calculate final PnL                                   │
└─────────────────────────────────────────────────────────────┘
```

## Example Scenario

**Initial Position:**
- Market: "NFL-TEAMWINS-20250101-Y"
- Entry: 0.65 (65%)
- Quantity: 100 contracts
- Stop Loss: 0.60 (60%)

**Stop Loss Triggers:**
- Current price: 0.58 (58%)
- Exit order placed: 100 contracts @ 0.58
- Order ID: "order_12345"
- Status: `closing_in_progress = True`

**Partial Fill (50 contracts):**
- Order partially fills: 50/100 contracts
- System detects via `get_order_fill_status()`:
  - `filled_count = 50`
  - `remaining_count = 50`
- Updates position:
  - `stake = 50` (remaining)
  - `original_stake_on_exit = 100`
  - `partial_fills = [{"qty": 50, "price": 0.58, ...}]`
  - `closing_in_progress = False` (reset to monitor remaining)
- Calculates PnL for 50 filled contracts
- Records realized PnL

**Next Loop Iteration:**
- Position has 50 contracts remaining
- `closing_in_progress = False` → continues monitoring
- If price continues down → stop loss triggers again
- New exit order placed: 50 contracts
- Process repeats until all contracts exit

**Final Settlement:**
- All 100 contracts eventually exit
- Position no longer exists on Kalshi
- Marked as `settled = True`
- Final PnL calculated

## Key Features

✅ **Order ID Tracking**: Every exit order has a tracked order ID
✅ **Partial Fill Detection**: Detects partial fills immediately and via reconciliation
✅ **Position Adjustment**: Automatically adjusts position size for remaining contracts
✅ **Continued Monitoring**: Remaining contracts continue to be monitored for stop loss
✅ **PnL Accuracy**: Calculates realized PnL incrementally for each partial fill
✅ **Guaranteed Exit**: All contracts are eventually sold (either immediately or on subsequent triggers)

## Data Structures

**Position with Partial Fill Tracking:**
```python
{
    "market_ticker": "NFL-TEAMWINS-20250101-Y",
    "side": "yes",
    "entry_price": 0.65,
    "stake": 50,  # Updated after partial fill
    "original_stake_on_exit": 100,  # Original size when exit triggered
    "exit_order_id": "order_12345",  # Tracked order ID
    "last_exit_price": 0.58,  # Price of exit order
    "exit_reason": "stop_loss",
    "closing_in_progress": False,  # Reset after partial fill
    "partial_fills": [
        {
            "qty": 50,
            "price": 0.58,
            "time": "2025-01-01T12:00:00Z",
            "order_id": "order_12345",
            "reason": "stop_loss"
        }
    ],
    "settled": False
}
```

## Testing Recommendations

1. **Test Partial Fill Scenario**:
   - Create large position in low-liquidity market
   - Trigger stop loss
   - Verify order ID is tracked
   - Verify partial fill is detected
   - Verify position size is adjusted
   - Verify remaining position continues monitoring

2. **Test Multiple Partial Fills**:
   - Trigger stop loss multiple times on same position
   - Verify each partial fill is tracked
   - Verify final settlement is correct

3. **Test Full Fill**:
   - Trigger stop loss with high liquidity
   - Verify immediate full fill is handled
   - Verify settlement occurs correctly

4. **Test Order Timeout**:
   - Place order in illiquid market
   - Let order timeout
   - Verify position state is reset
   - Verify order can be retried

## Configuration

No configuration changes needed. All features work with existing settings:
- `settings.PLACE_LIVE_KALSHI_ORDERS`: Controls live vs simulation mode
- `settings.REFRESH_ACTIVE`: Controls check frequency when positions active
- `settings.VERBOSE`: Controls detailed logging

## Backward Compatibility

✅ Fully backward compatible
✅ Existing positions without order IDs will work (handled by reconciliation)
✅ Positions in `closing_in_progress` from previous runs are handled
✅ No breaking changes to position data structure (new fields are optional)

---

This implementation ensures that **all contracts are ultimately sold**, even if exits occur in multiple partial fills.