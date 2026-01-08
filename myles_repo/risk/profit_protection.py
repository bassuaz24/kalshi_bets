import time
import math
from typing import Optional
from app import state
from config import settings
from core.time import now_utc, parse_iso_utc
from utils.tickers import event_key
from kalshi.fees import kalshi_fee_per_contract
from odds_feed.formatting import _parse_period_clock


def aggregate_positions_on_side(event_positions: list, market_ticker: str) -> tuple:
    total_qty = 0.0
    total_cost = 0.0

    for pos in event_positions:
        if pos.get("market_ticker") == market_ticker and not pos.get("settled", False):
            try:
                qty = float(pos.get("stake", 0))
                price = float(pos.get("entry_price", 0))
                if qty > 0 and price > 0:
                    total_qty += qty
                    total_cost += qty * price
            except (TypeError, ValueError):
                continue

    if total_qty <= 0:
        return 0.0, 0.0, 0.0

    weighted_avg = total_cost / total_qty
    return total_qty, weighted_avg, total_cost


def hedge_outcome_rois(qA: float, pA: float, qB: float, pB: float,
                       yes_ask_A: Optional[float] = None, yes_ask_B: Optional[float] = None):
    is_maker_A = (yes_ask_A is not None and pA < yes_ask_A) if yes_ask_A is not None else False
    is_maker_B = (yes_ask_B is not None and pB < yes_ask_B) if yes_ask_B is not None else False
    fA = kalshi_fee_per_contract(pA, is_maker=is_maker_A)
    fB = kalshi_fee_per_contract(pB, is_maker=is_maker_B)
    L = max(1e-9, qA * pA + qB * pB)

    pnl_A = qA * (1 - pA - fA) - qB * (pB + fB)
    pnl_B = qB * (1 - pB - fB) - qA * (pA + fA)

    return pnl_A / L, pnl_B / L


def calculate_settlement_roi(qA: float, pA: float, qB: float, pB: float) -> float:
    roi_A, roi_B = hedge_outcome_rois(qA, pA, qB, pB)
    return min(roi_A, roi_B)


def calculate_current_profit_mtm(
    qA: float, pA: float, qB: float, pB: float,
    current_price_A: float, current_price_B: float,
    yes_ask_A: Optional[float] = None, yes_ask_B: Optional[float] = None,
    yes_bid_A: Optional[float] = None, yes_bid_B: Optional[float] = None,
) -> tuple:
    entry_cost = (qA * pA) + (qB * pB)
    if entry_cost <= 0:
        return 0.0, 0.0, 0.0, 0.0

    if current_price_A is None or current_price_B is None:
        is_maker_A = (yes_ask_A is not None and pA < yes_ask_A) if yes_ask_A is not None else False
        is_maker_B = (yes_ask_B is not None and pB < yes_ask_B) if yes_ask_B is not None else False
        fA = kalshi_fee_per_contract(pA, is_maker=is_maker_A)
        fB = kalshi_fee_per_contract(pB, is_maker=is_maker_B)
        total_entry_costs = qA * (pA + fA) + qB * (pB + fB)

        pnl_A = qA * (1.0 - pA - fA) - qB * (pB + fB)
        pnl_B = qB * (1.0 - pB - fB) - qA * (pA + fA)
        roi_A = pnl_A / total_entry_costs if total_entry_costs > 0 else 0.0
        roi_B = pnl_B / total_entry_costs if total_entry_costs > 0 else 0.0
        best_profit = max(pnl_A, pnl_B)
        best_profit_pct = best_profit / total_entry_costs if total_entry_costs > 0 else 0.0
        return best_profit, best_profit_pct, roi_A, roi_B

    current_price_A = max(0.01, min(0.99, float(current_price_A)))
    current_price_B = max(0.01, min(0.99, float(current_price_B)))

    is_maker_A_entry = (yes_ask_A is not None and pA < yes_ask_A) if yes_ask_A is not None else False
    is_maker_B_entry = (yes_ask_B is not None and pB < yes_ask_B) if yes_ask_B is not None else False
    fA_entry = kalshi_fee_per_contract(pA, is_maker=is_maker_A_entry)
    fB_entry = kalshi_fee_per_contract(pB, is_maker=is_maker_B_entry)

    is_maker_A_exit = (yes_bid_A is not None and current_price_A > yes_bid_A) if yes_bid_A is not None else True
    is_maker_B_exit = (yes_bid_B is not None and current_price_B > yes_bid_B) if yes_bid_B is not None else True
    fA_sell = kalshi_fee_per_contract(current_price_A, is_maker=is_maker_A_exit)
    fB_sell = kalshi_fee_per_contract(current_price_B, is_maker=is_maker_B_exit)

    gross_proceeds_A = qA * current_price_A
    gross_proceeds_B = qB * current_price_B

    net_proceeds_A = gross_proceeds_A - (qA * fA_sell)
    net_proceeds_B = gross_proceeds_B - (qB * fB_sell)
    total_net_proceeds = net_proceeds_A + net_proceeds_B

    total_entry_costs = qA * (pA + fA_entry) + qB * (pB + fB_entry)

    current_profit = total_net_proceeds - total_entry_costs
    current_profit_pct = current_profit / total_entry_costs if total_entry_costs > 0 else 0.0

    pnl_A_settle = qA * (1.0 - pA - fA_entry) - qB * (pB + fB_entry)
    pnl_B_settle = qB * (1.0 - pB - fB_entry) - qA * (pA + fA_entry)
    roi_A = pnl_A_settle / total_entry_costs if total_entry_costs > 0 else 0.0
    roi_B = pnl_B_settle / total_entry_costs if total_entry_costs > 0 else 0.0

    return current_profit, current_profit_pct, roi_A, roi_B


def check_if_positions_growing_recently(
    side_A_positions: list,
    side_B_positions: list,
    window_seconds: float = 300,
) -> tuple:
    now = time.time()
    cutoff_time = now - window_seconds

    most_recent_entry = None
    total_qty_now = 0.0

    for pos in side_A_positions + side_B_positions:
        if pos.get("settled", False):
            continue

        entry_time_str = pos.get("entry_time")
        if entry_time_str:
            try:
                entry_time = parse_iso_utc(entry_time_str)
                if entry_time:
                    entry_ts = entry_time.timestamp()
                    if most_recent_entry is None or entry_ts > most_recent_entry:
                        most_recent_entry = entry_ts
            except Exception:
                pass

        total_qty_now += float(pos.get("stake", 0))

    if most_recent_entry is None:
        return False, None, total_qty_now

    last_trade_age = now - most_recent_entry
    is_growing = last_trade_age < window_seconds

    return is_growing, last_trade_age, total_qty_now


def calculate_theoretical_max_profit(
    qA: float, pA: float, qB: float, pB: float,
    yes_ask_A: Optional[float] = None, yes_ask_B: Optional[float] = None,
) -> tuple:
    locked_capital = (qA * pA) + (qB * pB)
    if locked_capital <= 0:
        return 0.0, 0.0

    is_maker_A = (yes_ask_A is not None and pA < yes_ask_A) if yes_ask_A is not None else False
    is_maker_B = (yes_ask_B is not None and pB < yes_ask_B) if yes_ask_B is not None else False
    fA = kalshi_fee_per_contract(pA, is_maker=is_maker_A)
    fB = kalshi_fee_per_contract(pB, is_maker=is_maker_B)

    pnl_A = qA * (1.0 - pA - fA) - qB * (pB + fB)
    pnl_B = qB * (1.0 - pB - fB) - qA * (pA + fA)

    max_profit = max(pnl_A, pnl_B)
    max_profit_pct = max_profit / locked_capital

    return max_profit, max_profit_pct


def calculate_target_sell_prices_for_max_roi(
    qA: float, entry_A: float, qB: float, entry_B: float, max_settlement_roi: float,
) -> tuple:
    entry_cost = (qA * entry_A) + (qB * entry_B)
    if entry_cost <= 0 or qA <= 0 or qB <= 0:
        return None, None

    target_total_value = entry_cost * (1.0 + max_settlement_roi)

    total_contracts = qA + qB
    if total_contracts <= 0:
        return None, None

    avg_price_needed = target_total_value / total_contracts

    distance_A = 1.0 - entry_A
    distance_B = 1.0 - entry_B

    total_weighted_distance = (qA * distance_A) + (qB * distance_B)

    if total_weighted_distance <= 0:
        return None, None

    extra_value_needed = entry_cost * max_settlement_roi

    if total_weighted_distance > 0:
        proportion = extra_value_needed / total_weighted_distance
        proportion = max(0.0, min(1.0, proportion))
    else:
        proportion = 0.0

    target_price_A = entry_A + (proportion * distance_A)
    target_price_B = entry_B + (proportion * distance_B)

    target_price_A = max(0.01, min(0.99, target_price_A))
    target_price_B = max(0.01, min(0.99, target_price_B))

    return target_price_A, target_price_B


def check_profit_protection(
    event_ticker: str,
    side_A_positions: list,
    side_B_positions: list,
    side_A_ticker: str,
    side_B_ticker: str,
    side_A_sell_price: float,
    side_B_sell_price: float,
    side_A_ask: Optional[float] = None,
    side_B_ask: Optional[float] = None,
    side_A_bid: Optional[float] = None,
    side_B_bid: Optional[float] = None,
    odds_feed_home_prob: Optional[float] = None,
    odds_feed_away_prob: Optional[float] = None,
    side_A_is_home: bool = None,
    period_clock: Optional[str] = None,
    match_name: Optional[str] = None,
) -> dict:
    evt_key = event_key(event_ticker)

    qty_A, entry_A, _ = aggregate_positions_on_side(side_A_positions, side_A_ticker)
    qty_B, entry_B, _ = aggregate_positions_on_side(side_B_positions, side_B_ticker)

    if qty_A <= 0 or qty_B <= 0:
        return {
            "should_close": False,
            "reason": "not_hedged",
            "current_profit_pct": 0.0,
        }

    if settings.ODDS_FEED_AGGRESSIVE_EXIT_ENABLED:
        can_trigger = _can_trigger_7pct_exit(period_clock, match_name, event_ticker=event_ticker)

        if not can_trigger:
            parsed = _parse_period_clock(period_clock)
            if parsed:
                period, minutes = parsed
                is_nba = event_ticker and str(event_ticker).startswith("KXNBAGAME-")
                is_womens = "(W)" in str(match_name) if match_name else False

                if is_nba:
                    game_type = "NBA"
                    period_label = "4th quarter"
                elif is_womens:
                    game_type = "women's"
                    period_label = "4th quarter"
                else:
                    game_type = "men's"
                    period_label = "2nd half"

                if settings.VERBOSE:
                    print(
                        f"⏸️  7% EXIT BLOCKED: {game_type} game in period {period} with {minutes:.1f} min left "
                        f"- only allowed in {period_label} with ≤{settings.ODDS_FEED_EXIT_TIME_MINUTES}min remaining"
                    )

        side_A_bid_check = side_A_bid if side_A_bid is not None else side_A_sell_price
        side_B_bid_check = side_B_bid if side_B_bid is not None else side_B_sell_price

        if (
            can_trigger and side_A_bid_check is not None
            and side_A_bid_check <= settings.ODDS_FEED_EXIT_THRESHOLD
            and side_A_bid_check >= settings.ODDS_FEED_EXIT_THRESHOLD_MIN
        ):
            if settings.VERBOSE:
                print(
                    f"🚨 ABSOLUTE EXIT: Side A best bid {side_A_bid_check:.2%} is between "
                    f"{settings.ODDS_FEED_EXIT_THRESHOLD_MIN:.0%}-{settings.ODDS_FEED_EXIT_THRESHOLD:.0%} threshold "
                    "- EXITING IMMEDIATELY (bypassing all other checks)"
                )
            return {
                "should_close": True,
                "reason": f"absolute_exit_side_A_{side_A_bid_check:.1%}",
                "current_profit_pct": 0.0,
                "kalshi_price_triggered": True,
                "partial_exit_side": "A",
                "side_A_sell_price": side_A_sell_price,
                "side_B_sell_price": side_B_sell_price,
            }
        if can_trigger and side_A_bid_check is not None and side_A_bid_check < settings.ODDS_FEED_EXIT_THRESHOLD_MIN:
            if settings.VERBOSE:
                print(
                    f"🛡️ EXIT BLOCKED: Side A best bid {side_A_bid_check:.2%} < "
                    f"{settings.ODDS_FEED_EXIT_THRESHOLD_MIN:.0%} minimum - holding to settlement"
                )

        if (
            can_trigger and side_B_bid_check is not None
            and side_B_bid_check <= settings.ODDS_FEED_EXIT_THRESHOLD
            and side_B_bid_check >= settings.ODDS_FEED_EXIT_THRESHOLD_MIN
        ):
            if settings.VERBOSE:
                print(
                    f"🚨 ABSOLUTE EXIT: Side B best bid {side_B_bid_check:.2%} is between "
                    f"{settings.ODDS_FEED_EXIT_THRESHOLD_MIN:.0%}-{settings.ODDS_FEED_EXIT_THRESHOLD:.0%} threshold "
                    "- EXITING IMMEDIATELY (bypassing all other checks)"
                )
            return {
                "should_close": True,
                "reason": f"absolute_exit_side_B_{side_B_bid_check:.1%}",
                "current_profit_pct": 0.0,
                "kalshi_price_triggered": True,
                "partial_exit_side": "B",
                "side_A_sell_price": side_A_sell_price,
                "side_B_sell_price": side_B_sell_price,
            }
        if can_trigger and side_B_bid_check is not None and side_B_bid_check < settings.ODDS_FEED_EXIT_THRESHOLD_MIN:
            if settings.VERBOSE:
                print(
                    f"🛡️ EXIT BLOCKED: Side B best bid {side_B_bid_check:.2%} < "
                    f"{settings.ODDS_FEED_EXIT_THRESHOLD_MIN:.0%} minimum - holding to settlement"
                )

    hedge_ratio = min(qty_A, qty_B) / max(qty_A, qty_B) if max(qty_A, qty_B) > 0 else 0.0
    if hedge_ratio < 0.30:
        if settings.VERBOSE:
            print(
                f"🛡️ Profit protection blocked: Unbalanced hedge "
                f"(qA={qty_A:.1f}, qB={qty_B:.1f}, ratio={hedge_ratio:.1%})"
            )
        return {
            "should_close": False,
            "reason": "unbalanced_hedge",
            "current_profit_pct": 0.0,
            "hedge_ratio": hedge_ratio,
        }

    is_growing, last_trade_age, total_qty_now = check_if_positions_growing_recently(
        side_A_positions, side_B_positions, settings.PROFIT_PROTECTION_PYRAMIDING_WINDOW
    )

    if is_growing and settings.PROFIT_PROTECTION_REQUIRE_NO_RECENT_GROWTH:
        if settings.VERBOSE:
            print(
                "🛡️ Profit protection blocked: Positions actively growing "
                f"(last trade {last_trade_age:.0f}s ago)"
            )
        return {
            "should_close": False,
            "reason": "active_pyramiding",
            "current_profit_pct": 0.0,
            "pyramiding_active": True,
        }

    roi_A, roi_B = hedge_outcome_rois(qty_A, entry_A, qty_B, entry_B)
    settlement_roi_min = min(roi_A, roi_B)

    _, current_profit_pct, _, _ = calculate_current_profit_mtm(
        qty_A, entry_A, qty_B, entry_B,
        side_A_sell_price, side_B_sell_price,
    )

    total_price = side_A_sell_price + side_B_sell_price
    if total_price > 0:
        prob_A = side_A_sell_price / total_price
        prob_B = side_B_sell_price / total_price
    else:
        prob_A = 0.5
        prob_B = 0.5

    weighted_settlement_roi = (prob_A * roi_A) + (prob_B * roi_B)

    if weighted_settlement_roi < 0 and current_profit_pct > 0:
        if settings.VERBOSE:
            print(
                f"⚠️ Weighted settlement ROI is negative ({weighted_settlement_roi:.2%}) but current MTM is positive "
                f"({current_profit_pct:.2%}) - allowing exit consideration"
            )

    settlement_floor = weighted_settlement_roi

    if current_profit_pct < settlement_floor:
        if settings.VERBOSE:
            print(
                f"🛡️ Profit protection blocked: Current MTM {current_profit_pct:.2%} < expected settlement "
                f"{settlement_floor:.2%} (prob-weighted: {prob_A:.1%} × {roi_A:.2%} + {prob_B:.1%} × {roi_B:.2%})"
            )
        return {
            "should_close": False,
            "reason": "worse_than_settlement",
            "current_profit_pct": current_profit_pct,
            "settlement_roi": weighted_settlement_roi,
            "settlement_roi_min": settlement_roi_min,
            "roi_A": roi_A,
            "roi_B": roi_B,
            "prob_A": prob_A,
            "prob_B": prob_B,
        }

    _, theoretical_max_pct = calculate_theoretical_max_profit(qty_A, entry_A, qty_B, entry_B)
    max_settlement_roi = theoretical_max_pct

    peak_key = f"{evt_key}_peak"
    if peak_key not in state._PEAK_PROFITS:
        state._PEAK_PROFITS[peak_key] = {
            "profit_pct": current_profit_pct,
            "timestamp": time.time(),
        }
    else:
        peak_data = state._PEAK_PROFITS[peak_key]
        if current_profit_pct > peak_data["profit_pct"]:
            peak_data["profit_pct"] = current_profit_pct
            peak_data["timestamp"] = time.time()

    peak_profit_pct = state._PEAK_PROFITS[peak_key]["profit_pct"]

    target_price_A, target_price_B = calculate_target_sell_prices_for_max_roi(
        qty_A, entry_A, qty_B, entry_B, max_settlement_roi
    )

    result = {
        "should_close": False,
        "reason": None,
        "current_profit_pct": current_profit_pct,
        "peak_profit_pct": peak_profit_pct,
        "max_profit_pct": max_settlement_roi,
        "settlement_roi": weighted_settlement_roi,
        "settlement_roi_min": settlement_roi_min,
        "roi_A": roi_A,
        "roi_B": roi_B,
        "prob_A": prob_A,
        "prob_B": prob_B,
        "is_pyramiding": is_growing if is_growing else False,
        "target_price_A": target_price_A,
        "target_price_B": target_price_B,
    }

    latest_entry_time = None
    for pos in side_A_positions + side_B_positions:
        entry_time_str = pos.get("entry_time")
        if entry_time_str:
            try:
                entry_time = parse_iso_utc(entry_time_str)
                if entry_time and (latest_entry_time is None or entry_time > latest_entry_time):
                    latest_entry_time = entry_time
            except Exception:
                pass

    if latest_entry_time:
        now_dt = now_utc()
        hold_duration = (now_dt - latest_entry_time).total_seconds()

        if hold_duration < settings.PROFIT_PROTECTION_MIN_HOLD_SECONDS:
            if settings.VERBOSE:
                print(
                    f"🛡️ Profit protection blocked: Only {hold_duration:.0f}s since hedge "
                    f"(need {settings.PROFIT_PROTECTION_MIN_HOLD_SECONDS}s minimum)"
                )
            return {
                "should_close": False,
                "reason": "too_soon_after_hedge",
                "current_profit_pct": current_profit_pct,
            }

    if settings.PROFIT_PROTECTION_ENABLED:
        time_remaining = None
        if period_clock:
            parsed = _parse_period_clock(period_clock)
            if parsed:
                period, minutes = parsed
                time_remaining = minutes * 60.0

        if time_remaining is not None and time_remaining < settings.PROFIT_PROTECTION_MIN_TIME_REMAINING:
            if settings.VERBOSE:
                print(
                    f"⏸️ Skip profit protection exit — only {time_remaining:.1f}s remaining "
                    f"(minimum {settings.PROFIT_PROTECTION_MIN_TIME_REMAINING:.1f}s required)"
                )
            return {
                "should_close": False,
                "reason": "insufficient_time_remaining",
                "current_profit_pct": current_profit_pct,
                "time_remaining": time_remaining,
            }

    if settings.MAX_PROFIT_DETECTION_ENABLED and max_settlement_roi > 0 and not is_growing:
        max_profit_ratio = current_profit_pct / max_settlement_roi if max_settlement_roi > 0 else 0.0

        if max_profit_ratio >= settings.MAX_PROFIT_THRESHOLD:
            margin_for_max_profit = min(
                settings.PROFIT_PROTECTION_MIN_MARGIN_ABOVE_SETTLEMENT * 0.33,
                0.01,
            )
            required_profit = weighted_settlement_roi + margin_for_max_profit

            if current_profit_pct >= required_profit and current_profit_pct >= settings.PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT:
                result["should_close"] = True
                result["reason"] = f"max_profit_{max_profit_ratio:.0%}_no_pyramiding"
                if settings.VERBOSE:
                    target_A = result.get("target_price_A")
                    target_B = result.get("target_price_B")
                    target_info = ""
                    if target_A is not None and target_B is not None:
                        target_info = f" (target prices for max ROI: {target_A:.2%}, {target_B:.2%})"

                    print(
                        f"🚀 MAX PROFIT: {current_profit_pct:.2%} ({max_profit_ratio:.0%} of settlement max "
                        f"{max_settlement_roi:.2%}), above settlement+margin {required_profit:.2%} "
                        f"(reduced margin: {margin_for_max_profit:.2%}) and absolute min "
                        f"{settings.PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT:.1%}, NOT pyramiding - taking profit!{target_info}"
                    )
                return result
            if settings.VERBOSE:
                if current_profit_pct >= required_profit:
                    print(
                        f"🛡️ Max profit trigger activated ({max_profit_ratio:.0%}) and above settlement+margin "
                        f"({current_profit_pct:.2%} > {required_profit:.2%}), but below absolute minimum "
                        f"{settings.PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT:.1%} - holding for safety"
                    )
                else:
                    print(
                        f"🛡️ Max profit reached ({max_profit_ratio:.0%}) but {current_profit_pct:.2%} < "
                        f"settlement+margin {required_profit:.2%} (reduced margin: {margin_for_max_profit:.2%}) - holding"
                    )

    if settings.TRAILING_STOP_ENABLED and current_profit_pct >= settings.MIN_PROFIT_FOR_TRAILING_STOP and not is_growing:
        trailing_stop_pct = settings.TRAILING_STOP_PCT
        if peak_profit_pct >= settings.TRAILING_STOP_TIGHTEN_THRESHOLD:
            trailing_stop_pct = settings.TRAILING_STOP_PCT * 0.5
        stop_distance = trailing_stop_pct

        drop_from_peak = max(0.0, peak_profit_pct - current_profit_pct)

        if drop_from_peak >= stop_distance:
            margin_for_trailing = min(
                settings.PROFIT_PROTECTION_MIN_MARGIN_ABOVE_SETTLEMENT * 0.5,
                0.0075,
            )
            required_profit = weighted_settlement_roi + margin_for_trailing

            if current_profit_pct > required_profit and current_profit_pct >= settings.PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT:
                result["should_close"] = True
                result["reason"] = f"trailing_stop_drop_{drop_from_peak:.1%}_no_pyramiding"
                if settings.VERBOSE:
                    print(
                        f"🛑 TRAILING STOP: {current_profit_pct:.2%} dropped {drop_from_peak:.2%} from peak "
                        f"{peak_profit_pct:.2%}, {current_profit_pct:.2%} > settlement+margin {required_profit:.2%} "
                        f"(reduced margin: {margin_for_trailing:.2%}) and absolute min "
                        f"{settings.PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT:.1%}, NOT pyramiding - closing!"
                    )
            elif settings.VERBOSE:
                if current_profit_pct > required_profit:
                    print(
                        f"🛡️ Trailing stop activated (drop {drop_from_peak:.2%}) and above settlement+margin "
                        f"({current_profit_pct:.2%} > {required_profit:.2%}), but below absolute minimum "
                        f"{settings.PROFIT_PROTECTION_MIN_ABSOLUTE_PROFIT:.1%} - holding for safety"
                    )
                else:
                    print(
                        f"🛡️ Trailing stop triggered but {current_profit_pct:.2%} <= settlement+margin "
                        f"{required_profit:.2%} (reduced margin: {margin_for_trailing:.2%}) - holding"
                    )

    if settings.VERBOSE and current_profit_pct > 0.03:
        max_profit_ratio = current_profit_pct / max_settlement_roi if max_settlement_roi > 0 else 0.0
        drop_from_peak = max(0.0, peak_profit_pct - current_profit_pct)

        reasons = []
        if not settings.MAX_PROFIT_DETECTION_ENABLED:
            reasons.append("max profit disabled")
        elif max_profit_ratio < settings.MAX_PROFIT_THRESHOLD:
            reasons.append(f"only {max_profit_ratio:.0%} of max")

        if not settings.TRAILING_STOP_ENABLED:
            reasons.append("trailing stop disabled")
        elif drop_from_peak < (settings.TRAILING_STOP_TIGHTENED_PCT if peak_profit_pct >= settings.TRAILING_STOP_TIGHTEN_THRESHOLD else settings.TRAILING_STOP_INITIAL_PCT):
            reasons.append(f"drop {drop_from_peak:.1%} < threshold")

        if is_growing:
            reasons.append("pyramiding active")

        if reasons:
            print(
                f"💤 Holding position: {current_profit_pct:.2%} profit (peak: {peak_profit_pct:.2%}, "
                f"max: {max_settlement_roi:.2%}) - {', '.join(reasons)}"
            )

    return result


def _can_trigger_7pct_exit(period_clock: Optional[str], match_name: Optional[str], event_ticker: Optional[str] = None) -> bool:
    if not period_clock or not match_name:
        return True

    parsed = _parse_period_clock(period_clock)
    if not parsed:
        return True

    period, minutes_remaining = parsed

    is_nba = event_ticker and str(event_ticker).startswith("KXNBAGAME-")

    if is_nba:
        return period == 4 and minutes_remaining <= settings.ODDS_FEED_EXIT_TIME_MINUTES

    is_womens = "(W)" in str(match_name)

    if is_womens:
        return period == 4 and minutes_remaining <= settings.ODDS_FEED_EXIT_TIME_MINUTES
    return period == 2 and minutes_remaining <= settings.ODDS_FEED_EXIT_TIME_MINUTES
