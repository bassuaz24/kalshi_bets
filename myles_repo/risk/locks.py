import json
import os
from app import state
from config import settings
from kalshi.markets import get_kalshi_markets
from positions.queries import event_is_neutralized
from utils.tickers import event_key


def persist_event_locks():
    try:
        event_locks_path = os.path.join(settings.BASE_DIR, "event_locks.json")
        with open(event_locks_path, "w") as f:
            json.dump(list(settings.EVENT_LOCKED_TILL_HEDGE), f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not persist event locks: {e}")


def persist_7pct_exited_events():
    try:
        event_7pct_exited_path = os.path.join(settings.BASE_DIR, "event_7pct_exited.json")
        with open(event_7pct_exited_path, "w") as f:
            json.dump(list(settings.EVENT_7PCT_EXITED), f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not persist 7% exited events: {e}")


def mark_event_7pct_exited(event_ticker: str):
    key = event_key(event_ticker)
    settings.EVENT_7PCT_EXITED.add(key)
    persist_7pct_exited_events()
    print(f"🚫 Event {event_ticker} marked as 7% exited - no new entries allowed")


def prune_event_locks():
    valid_keys = {
        event_key(p.get("event_ticker"))
        for p in state.positions
        if p.get("stake", 0) > 0 and not p.get("settled", False)
    }
    stale = {key for key in settings.EVENT_LOCKED_TILL_HEDGE if key not in valid_keys}
    if stale:
        settings.EVENT_LOCKED_TILL_HEDGE.difference_update(stale)
        persist_event_locks()
        print(f"🔓 Cleared {len(stale)} stale event locks.")


def update_event_lock(event_ticker: str):
    key = event_key(event_ticker)
    if both_sides_open_and_active(event_ticker):
        settings.EVENT_LOCKED_TILL_HEDGE.discard(key)
    else:
        settings.EVENT_LOCKED_TILL_HEDGE.add(key)
    persist_event_locks()


def set_event_neutralization_flags(evt: str):
    is_neut = event_is_neutralized(evt)
    evt_key = event_key(evt)
    for p in state.positions:
        if event_key(p.get("event_ticker")) == evt_key:
            p["neutralized"] = is_neut


def both_sides_open_and_active(evt: str) -> bool:
    evt_norm = event_key(evt)
    yes_positions = [
        p for p in state.positions
        if event_key(p.get("event_ticker", "")) == evt_norm
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
