import time
import math
from typing import Any, Dict, List
from app import state
from config import settings
from core.time import now_utc
from kalshi.positions import get_live_positions
from positions.io import save_positions
from execution.positions import normalize_loaded_positions
from utils.tickers import event_key
from strategy.hedge import hedge_qty_bounds_target_roi

_LAST_RECONCILE_TS = 0.0


def reconcile_positions():
    global _LAST_RECONCILE_TS

    print("🔁 Internal reconcile — trusting local fills")

    try:
        live = get_live_positions()
    except Exception as e:
        print(f"⚠️ Could not fetch live positions ({e}); continuing with local state")
        live = []

    live_now = now_utc().isoformat()
    live_keys = {(lp["ticker"], (lp["side"] or "").lower()) for lp in live}

    for p in state.positions:
        key = (p.get("market_ticker"), (p.get("side") or "").lower())
        if key in live_keys:
            p["last_seen_live"] = live_now

    new_positions = list(state.positions)
    local_keys = {(p["market_ticker"], p["side"].lower()): i for i, p in enumerate(new_positions)}

    for lp in live:
        key = (lp["ticker"], lp["side"].lower())

        event_ticker_from_api = ""
        if lp["ticker"]:
            parts = lp["ticker"].split("-")
            if len(parts) > 2:
                event_ticker_from_api = "-".join(parts[:2]).upper()
            else:
                event_ticker_from_api = (lp.get("event_ticker") or "").upper()

        if key in local_keys:
            idx = local_keys[key]
            existing_pos = new_positions[idx]

            if existing_pos.get("settled", False) or existing_pos.get("closing_in_progress", False):
                if settings.VERBOSE:
                    print(
                        f"⏸️ Skipping update for {lp['ticker']} {lp['side']} - position is being closed "
                        f"(settled={existing_pos.get('settled')}, closing={existing_pos.get('closing_in_progress')})"
                    )
                existing_pos["last_seen_live"] = live_now
                continue

            existing_pos["entry_price"] = lp["avg_price"]
            existing_pos["effective_entry"] = lp["avg_price"]
            existing_pos["stake"] = lp["contracts"]
            existing_pos["last_seen_live"] = live_now

            if event_ticker_from_api:
                existing_pos["event_ticker"] = event_ticker_from_api
                if settings.VERBOSE:
                    print(f"   → Updated event_ticker to: {event_ticker_from_api}")

            if settings.VERBOSE:
                print(
                    f"🔄 Updated position from Kalshi: {lp['ticker']} {lp['side']} "
                    f"@ {lp['avg_price']:.2%} (qty: {lp['contracts']})"
                )
        else:
            print(f"✅ Added live fill from Kalshi: {lp['ticker']} {lp['side']}")
            new_positions.append({
                "match": lp["ticker"],
                "side": lp["side"].lower(),
                "event_ticker": event_ticker_from_api,
                "market_ticker": lp["ticker"],
                "entry_price": lp["avg_price"],
                "stake": lp["contracts"],
                "effective_entry": lp["avg_price"],
                "entry_time": now_utc().isoformat(),
                "odds_prob": 0.5,
            })

    for pos in new_positions:
        if pos.get("settled", False) or pos.get("closing_in_progress", False):
            continue

        key = (pos.get("market_ticker"), (pos.get("side") or "").lower())
        if key not in live_keys:
            pos["settled"] = True
            pos["stake"] = 0
            if settings.VERBOSE:
                print(
                    f"🗑️ Marking {pos.get('market_ticker')} {pos.get('side')} as settled "
                    "- no longer exists on Kalshi"
                )

    state.positions[:] = new_positions
    normalize_loaded_positions()

    events = {}
    for p in state.positions:
        if p.get("side") != "yes":
            continue
        evt = p.get("event_ticker", "")
        if not evt:
            continue
        events.setdefault(evt, set()).add(p.get("market_ticker", ""))

    for evt, mkts in events.items():
        is_neut = len(mkts) >= 2
        for p in state.positions:
            if p.get("event_ticker") == evt:
                p["neutralized"] = is_neut
        if is_neut:
            print(f"🟢 Event {evt} is NEUTRALIZED (both sides present).")
        else:
            print(f"🟡 Event {evt} has one side only.")

    pairs = {}
    for p in state.positions:
        pairs.setdefault(p.get("event_ticker", ""), []).append(p)

    for evt, sides in pairs.items():
        sides_yes = [s for s in sides if s.get("side") == "yes"]
        if len(sides_yes) == 2 and all(s.get("neutralized") for s in sides_yes):
            h, a = sides_yes
            qA, pA = float(h["stake"]), float(h["entry_price"])
            pB = float(a["entry_price"])

            q_low, q_high = hedge_qty_bounds_target_roi(qA, pA, pB, r=settings.MIN_HEDGE_RETURN)
            if q_low is None or q_high is None or q_high <= 0:
                for s in sides_yes:
                    s.pop("q_low", None)
                    s.pop("q_high", None)
                continue

            for s in sides_yes:
                s["q_low"], s["q_high"] = int(math.ceil(q_low)), int(math.floor(q_high))

    _LAST_RECONCILE_TS = time.time()
    save_positions()
