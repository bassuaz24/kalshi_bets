import json
import os
import time
from datetime import datetime
from typing import Optional
from app import state
from config import settings
from core.time import UTC
from utils.tickers import event_key


def persist_stop_lossed_events():
    try:
        event_stop_lossed_path = os.path.join(settings.BASE_DIR, "event_stop_lossed.json")
        data = {}
        for key, value in settings.EVENT_STOP_LOSSED.items():
            if isinstance(value, dict):
                entry_data = {}
                timestamp = value.get("timestamp")
                if isinstance(timestamp, (int, float)):
                    entry_data["timestamp"] = datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
                elif isinstance(timestamp, datetime):
                    entry_data["timestamp"] = timestamp.isoformat()
                else:
                    entry_data["timestamp"] = timestamp
                entry_data["entry_price"] = value.get("entry_price")
                data[key] = entry_data
            elif isinstance(value, (int, float)):
                data[key] = {
                    "timestamp": datetime.fromtimestamp(value, tz=UTC).isoformat(),
                    "entry_price": None,
                }
            elif isinstance(value, datetime):
                data[key] = {
                    "timestamp": value.isoformat(),
                    "entry_price": None,
                }
            else:
                data[key] = value
        with open(event_stop_lossed_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not persist stop-lossed events: {e}")


def mark_event_stop_lossed(event_ticker: str, entry_price: Optional[float] = None):
    key = event_key(event_ticker)
    current_time = time.time()
    settings.EVENT_STOP_LOSSED[key] = {
        "timestamp": current_time,
        "entry_price": entry_price,
    }
    persist_stop_lossed_events()
    entry_price_str = f" (entry price: {entry_price:.2%})" if entry_price is not None else ""
    print(
        f"🚫 Event {event_ticker} marked as stop-lossed at "
        f"{datetime.fromtimestamp(current_time, tz=UTC).isoformat()}{entry_price_str} "
        "- cooldown active until price recovers past entry price"
    )


def is_event_in_stop_loss_cooldown(
    event_ticker: str,
    current_price: Optional[float] = None,
    cooldown_minutes: float = 180.0,
) -> bool:
    key = event_key(event_ticker)
    if key not in settings.EVENT_STOP_LOSSED:
        return False

    stop_loss_data = settings.EVENT_STOP_LOSSED[key]

    if isinstance(stop_loss_data, (int, float)):
        stop_loss_data = {"timestamp": stop_loss_data, "entry_price": None}
        settings.EVENT_STOP_LOSSED[key] = stop_loss_data
    elif isinstance(stop_loss_data, datetime):
        stop_loss_data = {"timestamp": stop_loss_data.timestamp(), "entry_price": None}
        settings.EVENT_STOP_LOSSED[key] = stop_loss_data
    elif not isinstance(stop_loss_data, dict):
        del settings.EVENT_STOP_LOSSED[key]
        return False

    stop_loss_time = stop_loss_data.get("timestamp")
    entry_price = stop_loss_data.get("entry_price")

    if isinstance(stop_loss_time, (int, float)):
        timestamp = stop_loss_time
    elif isinstance(stop_loss_time, datetime):
        timestamp = stop_loss_time.timestamp()
    elif isinstance(stop_loss_time, str):
        try:
            dt = datetime.fromisoformat(stop_loss_time.replace("Z", "+00:00"))
            timestamp = dt.timestamp()
        except Exception:
            return False
    else:
        return False

    if settings.ALLOW_STOP_LOSS_PRICE_RECOVERY and current_price is not None and entry_price is not None and entry_price > 0:
        if current_price >= entry_price:
            print(
                f"✅ Event {event_ticker} price recovered: {current_price:.2%} >= {entry_price:.2%} "
                "(original entry) - allowing re-entry"
            )
            del settings.EVENT_STOP_LOSSED[key]
            persist_stop_lossed_events()
            return False

    elapsed_minutes = (time.time() - timestamp) / 60.0
    in_cooldown = elapsed_minutes < cooldown_minutes

    if in_cooldown:
        remaining_seconds = int((cooldown_minutes - elapsed_minutes) * 60)
        entry_price_str = f" (entry: {entry_price:.2%})" if entry_price is not None else ""
        current_price_str = f", current: {current_price:.2%}" if current_price is not None else ""
        print(
            f"⏳ Event {event_ticker} in stop-loss cooldown: {remaining_seconds}s remaining"
            f"{entry_price_str}{current_price_str}"
        )

    return in_cooldown
