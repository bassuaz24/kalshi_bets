from typing import List, Dict, Any
import app.engine as main_state
from kalshi.markets import get_kalshi_markets, format_price
from kalshi.fees import kalshi_fee_per_contract


def get_active_matches_for_api() -> List[Dict[str, Any]]:
    import copy
    return copy.deepcopy(main_state._active_matches_for_api)


def get_positions_for_api() -> List[Dict[str, Any]]:
    import copy
    result = []

    for p in main_state.positions:
        if p.get("settled", False) or p.get("closing_in_progress", False):
            continue

        pos_copy = copy.deepcopy(p)

        unrealized_pnl = None
        try:
            mkts = get_kalshi_markets(p.get("event_ticker", ""), force_live=True)
            if mkts:
                m = next((m for m in mkts if m.get("ticker") == p.get("market_ticker")), None)
                if m:
                    yes_bid = format_price(m.get("yes_bid"))
                    yes_ask = format_price(m.get("yes_ask"))

                    if (p.get("side") or "").lower() == "yes":
                        entry = float(p.get("effective_entry", p.get("entry_price", 0.0)))
                        exit_price = yes_bid if yes_bid is not None else yes_ask
                        if exit_price is not None:
                            exit_fee = kalshi_fee_per_contract(exit_price, is_maker=True)
                            mtm_per_ct = (exit_price - entry) - exit_fee
                            unrealized_pnl = float(p.get("stake", 0)) * mtm_per_ct
        except Exception:
            pass

        pos_copy["unrealized_pnl"] = unrealized_pnl
        result.append(pos_copy)

    return result


def get_game_ticks_for_api(game_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    import copy

    ticks = main_state._game_ticks_history.get(game_id, [])
    if not ticks:
        for key, value in main_state._game_ticks_history.items():
            if key == game_id:
                ticks = value
                break

    return copy.deepcopy(ticks[-limit:]) if ticks else []
