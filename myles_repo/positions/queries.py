from app import state
from utils.tickers import event_key, normalize_event_ticker


def event_is_neutralized(evt: str) -> bool:
    evt_key = event_key(evt)
    mkts = {
        p["market_ticker"]
        for p in state.positions
        if event_key(p.get("event_ticker")) == evt_key
        and p.get("side") == "yes"
        and p.get("stake", 0) > 0
    }
    return len(mkts) >= 2


def is_neutralized(market_ticker):
    sides = set(p["side"] for p in state.positions if p.get("market_ticker") == market_ticker)
    return len(sides) > 1


def both_sides_open_and_active(evt: str, get_kalshi_markets):
    evt_norm = normalize_event_ticker(evt)
    yes_positions = [
        p for p in state.positions
        if normalize_event_ticker(p.get("event_ticker", "")) == evt_norm
        and p.get("side") == "yes"
        and p.get("stake", 0) > 0
    ]
    mkts = list({p["market_ticker"] for p in yes_positions})
    if len(mkts) < 2:
        return False

    kalshi = get_kalshi_markets(evt, force_live=True) or []
    active_tickers = {
        m.get("ticker") for m in kalshi
        if m.get("status") == "active" and (m.get("yes_bid") or m.get("yes_ask"))
    }
    return all(t in active_tickers for t in mkts[:2])
