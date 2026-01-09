# Entry Trade Partial Fill Handling - Implementation Summary

## Overview

Implemented optimized partial fill handling for entry trades when taking the ask. The system now properly tracks order IDs, waits for fill confirmation, detects partial fills, and only creates positions with actual filled quantities.

## Changes Made

### 1. Updated `strategy/engine.py` - `run_engine()` function

**Key Features Implemented:**

1. **Order ID Extraction**: Extracts order ID from response for tracking
2. **Immediate Fill Check**: Quick check after 0.5 seconds (asks fill fast)
3. **Optimized Timeout**: 5 seconds instead of 30 (asks typically fill in 1-2 seconds)
4. **Partial Fill Detection**: Detects partial fills immediately and via wait
5. **Accurate Position Sizing**: Only creates position with actual filled quantity
6. **Order Tracking**: Stores `entry_order_id`, `original_order_quantity`, `entry_fill_status`
7. **Fallback Handling**: If order ID can't be extracted, falls back to reconciliation

### 2. Updated `app/state.py` - Metrics

**Added Metric:**
- `orders_partial_filled`: Tracks number of entry orders that partially filled

## Implementation Flow

### Step-by-Step Process:

1. **Order Submission**
   ```python
   result = safe_prepare_kalshi_order(
       market_ticker, side, entry_price, quantity,
       order_type="limit", action="buy"
   )
   ```

2. **Order ID Extraction**
   ```python
   order_id = _extract_order_id(result.get("response"))
   ```

3. **Quick Initial Check** (0.5 second wait)
   ```python
   time.sleep(0.5)  # Brief pause for order to process
   is_filled, filled_qty_immediate, remaining = get_order_fill_status(order_id)
   ```

4. **Handle Immediate Fill**
   - If fully filled: Use filled quantity, status = "filled"
   - If partially filled: Use filled quantity, status = "partial"
   - If not filled: Wait up to 5 seconds with `wait_for_fill_or_cancel()`

5. **Create Position with Actual Fill**
   ```python
   position = {
       "stake": actual_filled,  # ← Actual filled, not requested
       "entry_order_id": order_id,
       "original_order_quantity": quantity,
       "entry_fill_status": fill_status,
       ...
   }
   ```

## Key Optimizations for Taking the Ask

### 1. Fast Initial Check (0.5 seconds)
- Checks fill status immediately after order submission
- Catches fast fills that occur within 1-2 seconds
- Reduces unnecessary waiting

### 2. Short Timeout (5 seconds)
- Optimized for ask fills which typically complete in 1-2 seconds
- If not filled in 5 seconds, likely won't fill (ask moved or insufficient liquidity)
- Much faster than standard 30-second timeout

### 3. Accept Partial Fills
- Uses `require_full=False` in `wait_for_fill_or_cancel()`
- Creates position with whatever filled, even if partial
- Strategy can re-evaluate and add more on next iteration

### 4. Fallback to Reconciliation
- If order ID can't be extracted, still creates position
- Reconciliation will correct position size on next loop iteration
- Ensures system works even if order tracking fails

## Position Data Structure

**New Fields Added:**
```python
{
    "entry_order_id": "order_12345",  # Tracked order ID
    "original_order_quantity": 100,   # What was requested
    "entry_fill_status": "filled",    # "filled" or "partial"
    "stake": 100,                     # Actual filled quantity
    ...
}
```

## Metrics Tracking

**Updated Metrics:**
- `orders_placed`: Incremented when order is placed
- `orders_filled`: Incremented when order fully fills
- `orders_partial_filled`: Incremented when order partially fills (NEW)

## Example Scenarios

### Scenario 1: Full Fill (Common)
- **Order**: 100 contracts at ask 0.65
- **Result**: 100 contracts fill immediately
- **Position Created**: `stake = 100`, `entry_fill_status = "filled"`
- **Time**: ~0.5-1 second

### Scenario 2: Partial Fill
- **Order**: 100 contracts at ask 0.65
- **Result**: 50 contracts fill (ask only had 50 available)
- **Position Created**: `stake = 50`, `entry_fill_status = "partial"`
- **Log**: "📊 Partial fill on entry (taking ask): MARKET - 50/100 filled, 50 remaining"
- **Time**: ~0.5-2 seconds

### Scenario 3: No Fill
- **Order**: 100 contracts at ask 0.65
- **Result**: Ask moved to 0.66 before order executed
- **Action**: No position created, order cancelled
- **Log**: "⚠️ Entry order at ask did not fill for MARKET (status: timeout)"
- **Time**: ~5 seconds (timeout)

## Benefits

✅ **Data Consistency**: Position size always matches reality from creation
✅ **Risk Accuracy**: Risk calculations are correct immediately
✅ **Fast Execution**: Optimized for taking the ask (5s timeout vs 30s)
✅ **Partial Fill Support**: Handles partial fills gracefully
✅ **Order Tracking**: Full audit trail with order IDs
✅ **Fallback Safety**: Reconciliation as backup if order ID missing

## Backward Compatibility

✅ **Fully Compatible**: 
- Existing positions without `entry_order_id` work fine
- Reconciliation handles positions without order tracking
- No breaking changes to position structure

## Testing Recommendations

1. **Test Full Fill**: Place order in liquid market, verify immediate full fill
2. **Test Partial Fill**: Place large order in low-liquidity market, verify partial fill handling
3. **Test No Fill**: Place order when ask moves, verify no position created
4. **Test Order ID Missing**: Simulate missing order ID, verify fallback works
5. **Test Metrics**: Verify `orders_partial_filled` increments correctly

## Configuration

No configuration changes needed. Works with existing settings:
- `settings.PLACE_LIVE_KALSHI_ORDERS`: Controls live vs simulation
- `settings.VERBOSE`: Controls detailed logging

---

**Implementation Complete**: Entry trades now properly handle partial fills with optimized timing for taking the ask!