import time
from app import state
from config import settings
from core.time import now_utc
from bot_logging.csv_logger import log_trade, log_entry_row
from positions.io import save_positions


def upsert_position(new_pos):
    for p in state.positions:
        if p["market_ticker"] == new_pos["market_ticker"] and p["side"] == new_pos["side"]:
            total = p["stake"] + new_pos["stake"]
            if total > 0:
                p["entry_price"] = (
                    p["entry_price"] * p["stake"]
                    + new_pos["entry_price"] * new_pos["stake"]
                ) / total
            p["stake"] = total
            p["max_price"] = max(p.get("max_price", 0), new_pos["entry_price"])
            p["last_price"] = new_pos.get("last_price", p.get("last_price"))
            return
    state.positions.append(new_pos)


def normalize_loaded_positions():
    for p in state.positions:
        for k in ("event_ticker", "market_ticker", "match"):
            if k in p and isinstance(p[k], str):
                p[k] = p[k].strip()

        if "event_ticker" in p:
            p["event_ticker"] = p["event_ticker"].upper()

        if "entry_value" not in p:
            stake = p.get("stake", 0)
            entry_price = p.get("entry_price", 0)
            p["entry_value"] = stake * entry_price if stake > 0 and entry_price > 0 else 0

        if "stop_loss_triggered" not in p:
            p["stop_loss_triggered"] = False
        if "closing_in_progress" not in p:
            p["closing_in_progress"] = False
        if p.get("closing_in_progress") and p.get("closing_initiated_at"):
            age_seconds = time.time() - p.get("closing_initiated_at", 0)
            if age_seconds > 300:
                p["closing_in_progress"] = False
                p.pop("closing_initiated_at", None)
                p.pop("closing_check_result", None)
        elif p.get("closing_in_progress") and not p.get("closing_initiated_at"):
            p["closing_in_progress"] = False
        if "market_ticker" in p:
            p["market_ticker"] = p["market_ticker"].upper()

        if p.get("market_ticker"):
            current_event_ticker = p.get("event_ticker", "")
            market_ticker = p["market_ticker"]

            parts = market_ticker.split("-")
            if len(parts) > 2:
                correct_event_ticker = "-".join(parts[:2]).upper()

                if not current_event_ticker or current_event_ticker == market_ticker.upper():
                    p["event_ticker"] = correct_event_ticker
                elif len(current_event_ticker.split("-")) > 3:
                    p["event_ticker"] = correct_event_ticker


def deduplicate_positions():
    unique = {}
    for p in state.positions:
        key = (p["market_ticker"], p["side"])
        if key not in unique:
            unique[key] = p
        else:
            print(f"⚠️ Duplicate detected for {p['match']} {p['side']} — keeping first, discarding later.")
    state.positions = list(unique.values())

    events = {}
    for p in state.positions:
        events.setdefault(p["event_ticker"], []).append(p)
    for evt, ps in events.items():
        yes_count = sum(1 for x in ps if x["side"].lower() == "yes")
        if yes_count >= 2:
            print(f"🔁 Event {evt} has both sides active (neutralized candidate).")


def commit_trade_and_persist(position, order_id, filled_qty):
    existing = next((p for p in state.positions
                     if p["market_ticker"].upper() == position["market_ticker"].upper()
                     and p["side"] == position["side"]), None)
    if existing:
        total = existing["stake"] + filled_qty
        if total > 0:
            existing["entry_price"] = (
                existing["entry_price"] * existing["stake"]
                + position["entry_price"] * filled_qty
            ) / total
            existing_eff = existing.get("effective_entry", existing["entry_price"])
            new_eff = position.get("effective_entry", position["entry_price"])
            existing["effective_entry"] = (
                existing_eff * existing["stake"]
                + new_eff * filled_qty
            ) / total
            existing_entry_value = existing.get("entry_value", existing["stake"] * existing["entry_price"])
            new_entry_value = filled_qty * position["entry_price"]
            existing["entry_value"] = existing_entry_value + new_entry_value
        existing["stake"] = total
        existing["max_price"] = max(existing.get("max_price", 0.0), position["entry_price"])
    else:
        new_pos = dict(position)
        new_pos["stake"] = filled_qty
        new_pos["entry_value"] = filled_qty * position["entry_price"]
        new_pos["stop_loss_triggered"] = False
        state.positions.append(new_pos)

    log_trade({**position, "type": "live_filled", "order_id": order_id, "filled_qty": filled_qty})
    log_entry_row(position, position["event_ticker"])

    try:
        from positions.reconcile import reconcile_positions
        reconcile_positions()
    except Exception as e:
        print(f"⚠️ Post-fill reconcile failed: {e}")

    save_positions()
