# Entry Trade Partial Fill Handling - Optimized for Taking the Ask

## Understanding Taking the Ask

When you "purchase existing asks", you're:
- **Buying at the ask price** (taking liquidity)
- Using limit orders at ask price, or market orders
- Orders typically fill **quickly** (often within 1-2 seconds)
- But **partial fills can still occur** if:
  - Insufficient liquidity at ask (e.g., ask has 50 contracts, you want 100)
  - Multiple orders competing for same ask
  - Order book depth isn't sufficient
  - Fast-moving markets where asks get filled by others first

## Impact on Partial Fill Handling

**This makes partial fill handling MORE important, not less:**

1. ✅ **Still need fill confirmation** - Even fast fills can be partial
2. ✅ **Still need order ID tracking** - For verification and reconciliation
3. ✅ **Still need actual fill quantity** - Position must match reality
4. ⚡ **Can optimize timing** - Shorter timeout since fills are faster
5. ⚡ **Can check sooner** - Immediate check (1-2 seconds) instead of 30 seconds

## Optimized Fix for Taking the Ask

### Key Changes from Standard Fix:

1. **Shorter Timeout**: 5-10 seconds instead of 30 seconds (asks fill fast)
2. **Immediate Check**: Check fill status within 1-2 seconds
3. **Quick Fallback**: If not filled in 3 seconds, check order status once more, then continue
4. **Still Track Order ID**: For reconciliation and verification

### Recommended Implementation:

```python
# Execute trade (taking the ask)
try:
    side = trade.get("side", "yes")
    max_total_contracts = max_quantity_with_cap(
        entry_price,
        exposure * 1.1
    )
    
    # Get current ask price for limit order
    # (Strategy should pass this, but we'll use entry_price which should be ask)
    ask_price = entry_price  # Assuming strategy computed optimal ask price
    
    result = safe_prepare_kalshi_order(
        market_ticker,
        side,
        ask_price,  # Order at ask price (taking liquidity)
        quantity,
        max_total_contracts=max_total_contracts,
        order_type="limit",  # Limit order at ask price
        action="buy"
    )
    
    if result is None:
        print(f"⚠️ Trade execution failed for {market_ticker}")
        continue
    
    # Extract order ID (CRITICAL - still needed)
    from kalshi.orders import _extract_order_id, wait_for_fill_or_cancel
    order_id = _extract_order_id(result.get("response"))
    
    if not order_id:
        # If no order ID, fall back to reconciliation
        # But log as a warning - we prefer order ID tracking
        print(f"⚠️ Could not extract order ID for {market_ticker}, will rely on reconciliation")
        # Still create position, but reconciliation will correct it
        position = {
            "match": match.get("match", ""),
            "side": side,
            "event_ticker": event_ticker,
            "market_ticker": market_ticker,
            "entry_price": entry_price,
            "effective_entry": entry_price,
            "stake": quantity,  # Assume full fill initially
            "entry_time": now_utc().isoformat(),
            "entry_order_id": None,  # No order ID tracked
            "stop_loss": trade.get("stop_loss"),
            "take_profit": trade.get("take_profit"),
            "settled": False,
            "closing_in_progress": False,
            "odds_prob": 0.5,
        }
        state.positions.append(position)
        continue
    
    # Wait for fill - OPTIMIZED for taking the ask (shorter timeout)
    # Since taking the ask fills quickly, we use a shorter timeout
    fill_timeout = 5.0  # 5 seconds - asks typically fill in 1-2 seconds
    status, filled_qty = wait_for_fill_or_cancel(
        order_id,
        timeout_secs=fill_timeout,
        require_full=False  # Accept partial fills
    )
    
    if filled_qty <= 0:
        # Order didn't fill at ask - this can happen if:
        # 1. Ask moved up before order reached exchange
        # 2. Ask was filled by another order
        # 3. Insufficient liquidity
        print(f"⚠️ Entry order at ask did not fill for {market_ticker} (status: {status})")
        # Cancel remaining order if any
        # Don't create position - retry on next iteration if strategy wants
        continue
    
    # Check if partial fill occurred
    if filled_qty < quantity:
        # Partial fill - ask didn't have enough liquidity
        remaining = quantity - filled_qty
        print(f"📊 Partial fill on entry (taking ask): {market_ticker} - {filled_qty}/{quantity} filled, {remaining} remaining at ask")
        
        # Option 1: Create position with partial fill, don't retry remaining
        # (Strategy will re-evaluate and place new order if needed)
        
        # Option 2: Retry remaining at new ask price
        # (More complex, requires checking ask again)
        
        # For now, we'll do Option 1 - create position with what filled
        # Strategy can decide to add more on next iteration
    
    # Only create position with ACTUAL filled quantity
    position = {
        "match": match.get("match", ""),
        "side": side,
        "event_ticker": event_ticker,
        "market_ticker": market_ticker,
        "entry_price": entry_price,
        "effective_entry": entry_price,
        "stake": filled_qty,  # ← Use actual filled quantity (not requested)
        "entry_time": now_utc().isoformat(),
        "entry_order_id": order_id,  # ← Track order ID
        "original_order_quantity": quantity,  # ← Track what was ordered
        "entry_fill_status": status,  # ← "filled" or "partial"
        "stop_loss": trade.get("stop_loss"),
        "take_profit": trade.get("take_profit"),
        "settled": False,
        "closing_in_progress": False,
        "odds_prob": 0.5,
    }
    
    state.positions.append(position)
    state.METRICS["orders_placed"] += 1
    state.METRICS["orders_filled"] += 1 if status == "filled" else 0
    state.METRICS["orders_partial_filled"] += 1 if 0 < filled_qty < quantity else 0
    
    if filled_qty == quantity:
        print(f"✅ Entry position created: {market_ticker} {side.upper()} x{filled_qty} @ {entry_price:.2%} (full fill)")
    else:
        print(f"✅ Entry position created: {market_ticker} {side.upper()} x{filled_qty} @ {entry_price:.2%} (partial fill: {filled_qty}/{quantity})")
```

## Key Optimizations for Taking the Ask

### 1. Shorter Timeout (5 seconds vs 30 seconds)

**Rationale**: Asks typically fill in 1-2 seconds. If it doesn't fill in 5 seconds, it's likely not going to fill.

```python
fill_timeout = 5.0  # Optimized for taking the ask
```

### 2. Faster Initial Check

Can do an immediate check after order submission:

```python
# Submit order
result = safe_prepare_kalshi_order(...)
order_id = _extract_order_id(result.get("response"))

# Quick initial check (1 second wait)
time.sleep(1.0)
is_filled, filled_qty, remaining = get_order_fill_status(order_id)

if is_filled or filled_qty > 0:
    # Already filled (common for taking the ask)
    # Process immediately
else:
    # Not filled yet - wait a bit more
    status, filled_qty = wait_for_fill_or_cancel(order_id, timeout_secs=4.0)
```

### 3. Accept Partial Fills

Even when taking the ask, partial fills can occur:
- Ask might only have 50 contracts available
- Multiple orders competing for same ask
- Order book changes between decision and execution

**Important**: Always use actual filled quantity, not requested quantity.

### 4. Reconciliation as Backup

Still use reconciliation as a safety net:
- If order ID tracking fails, reconciliation will catch it
- If fill check times out, reconciliation will correct position size
- Acts as verification even when order ID is tracked

## Why This Still Matters Even When Taking the Ask

### Scenario 1: Insufficient Ask Depth
- **You want**: 100 contracts
- **Ask has**: 50 contracts available
- **Result**: 50 contracts fill, 50 remain unfilled
- **Without fix**: Position created with 100 contracts (wrong!)
- **With fix**: Position created with 50 contracts (correct!)

### Scenario 2: Fast-Moving Market
- **You decide**: Buy 100 contracts at ask 0.65
- **Before order executes**: Ask moves to 0.66, only 30 contracts at 0.65
- **Result**: 30 contracts fill at 0.65, order for 70 cancelled
- **Without fix**: Position created with 100 contracts (wrong!)
- **With fix**: Position created with 30 contracts (correct!)

### Scenario 3: Competing Orders
- **You place**: Order for 100 contracts at ask
- **Another trader**: Places order for 50 contracts 0.1 seconds before yours
- **Result**: They get 50, you get 50 (partial fill)
- **Without fix**: Position created with 100 contracts (wrong!)
- **With fix**: Position created with 50 contracts (correct!)

## Comparison: Standard vs Optimized

| Aspect | Standard Fix (Limit Orders) | Optimized Fix (Taking Ask) |
|--------|----------------------------|---------------------------|
| Timeout | 30 seconds | 5 seconds |
| Initial Check | Wait 5 seconds | Wait 1 second |
| Expected Fill Time | 5-30 seconds | 1-2 seconds |
| Partial Fill Likelihood | Higher | Lower, but still possible |
| Order ID Tracking | ✅ Critical | ✅ Still Critical |
| Fill Confirmation | ✅ Required | ✅ Still Required |
| Position Size Accuracy | ✅ Required | ✅ Still Required |

## Updated Recommended Implementation

The optimized fix still requires all the same components:
1. ✅ Extract order ID (still needed)
2. ✅ Wait for fill confirmation (shorter timeout: 5s)
3. ✅ Check for partial fills (still can happen)
4. ✅ Create position with actual filled quantity (still critical)
5. ✅ Track order ID for audit trail (still useful)

**Only difference**: Shorter timeout and faster initial check, but same fundamental logic.

## Alternative: Even Faster Check (Async Pattern)

For maximum speed when taking the ask:

```python
# Submit order
result = safe_prepare_kalshi_order(...)
order_id = _extract_order_id(result.get("response"))

# Quick check immediately (non-blocking)
time.sleep(0.5)  # Brief pause for order to process
is_filled, filled_qty, remaining = get_order_fill_status(order_id)

if filled_qty >= quantity:
    # Fully filled immediately - common when taking ask
    actual_filled = filled_qty
elif filled_qty > 0:
    # Partial fill already occurred - take it
    actual_filled = filled_qty
else:
    # Not filled yet - wait a bit more (but shorter than standard)
    status, filled_qty = wait_for_fill_or_cancel(order_id, timeout_secs=3.0, require_full=False)
    actual_filled = filled_qty if filled_qty > 0 else 0

if actual_filled <= 0:
    # Order didn't fill - skip position creation
    continue

# Create position with actual filled quantity
position = {
    "stake": actual_filled,  # ← Actual filled, not requested
    ...
}
```

## Summary

**Even when taking the ask, you still need partial fill handling**, but it can be optimized:

1. ✅ **Still critical**: Partial fills can still occur
2. ⚡ **Can optimize**: Shorter timeout (5s vs 30s)
3. ⚡ **Can optimize**: Faster initial check (1s vs 5s)
4. ✅ **Still need**: Order ID tracking, fill confirmation, accurate position sizing
5. ✅ **Still need**: Reconciliation as backup

**Bottom line**: The fix is still necessary, but can be faster/shorter due to typical ask fill behavior. The fundamental logic remains the same - we just don't need to wait as long.
