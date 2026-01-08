from config import settings
from app import state
from kalshi.markets import get_kalshi_markets, format_price
from kalshi.fees import kalshi_fee_per_contract


def _roi_pct_from_equity(equity: float) -> float:
    denom = max(1e-9, float(settings.CAPITAL_SIM))
    return 100.0 * (equity - settings.CAPITAL_SIM) / denom


def _current_unrealized_and_equity():
    unreal = 0.0

    for p in state.positions:
        try:
            mkts = get_kalshi_markets(p["event_ticker"], force_live=True)
            if not mkts:
                continue
            m = next((m for m in mkts if m.get("ticker") == p.get("market_ticker")), None)
            if not m:
                continue

            yes_bid = format_price(m.get("yes_bid"))
            yes_ask = format_price(m.get("yes_ask"))

            if (p.get("side") or "").lower() == "yes":
                entry = float(p.get("effective_entry", p.get("entry_price", 0.0)))
                exit_price = yes_bid if yes_bid is not None else yes_ask
                if exit_price is None:
                    continue

                exit_fee = kalshi_fee_per_contract(exit_price, is_maker=True)
                mtm_per_ct = (exit_price - entry) - exit_fee
                unreal += float(p.get("stake", 0)) * mtm_per_ct

        except Exception:
            continue

    if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
        from kalshi.balance import get_kalshi_balance
        live_cash = get_kalshi_balance()
        equity = live_cash + unreal
    else:
        equity = state.capital_sim + state.realized_pnl + unreal

    return unreal, equity
