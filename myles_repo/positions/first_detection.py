import csv
import json
import os
from datetime import datetime
from typing import Optional
from app import state
from config import settings
from core.time import now_utc, parse_iso_utc
from positions.io import resolve_first_detection_times_file
from utils.tickers import event_key


FIRST_DETECTION_TIMES_FILE = resolve_first_detection_times_file()


def load_first_detection_times():
    if state._FIRST_DETECTION_TIMES_LOADED:
        return state._FIRST_DETECTION_TIMES

    if not os.path.exists(FIRST_DETECTION_TIMES_FILE):
        state._FIRST_DETECTION_TIMES_LOADED = True
        return {}

    try:
        with open(FIRST_DETECTION_TIMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for ticker, iso_str in data.items():
                try:
                    state._FIRST_DETECTION_TIMES[ticker] = parse_iso_utc(iso_str)
                except Exception:
                    continue

        cleanup_old_first_detection_times(max_age_hours=48.0)

        state._FIRST_DETECTION_TIMES_LOADED = True
        return state._FIRST_DETECTION_TIMES
    except Exception as e:
        if settings.VERBOSE:
            print(f"⚠️ Error loading first detection times: {e}")
        state._FIRST_DETECTION_TIMES_LOADED = True
        return {}


def save_first_detection_times():
    try:
        data = {}
        for ticker, dt in state._FIRST_DETECTION_TIMES.items():
            if dt:
                data[ticker] = dt.isoformat()

        file_dir = os.path.dirname(FIRST_DETECTION_TIMES_FILE)
        if file_dir:
            os.makedirs(file_dir, exist_ok=True)

        with open(FIRST_DETECTION_TIMES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        if settings.VERBOSE:
            print(f"⚠️ Error saving first detection times: {e}")


def cleanup_old_first_detection_times(max_age_hours: float = 48.0):
    if not state._FIRST_DETECTION_TIMES:
        return 0

    current_time = now_utc()
    max_age_seconds = max_age_hours * 3600.0

    cleaned = {}
    removed_count = 0
    for ticker, detection_time in state._FIRST_DETECTION_TIMES.items():
        if detection_time:
            age_seconds = (current_time - detection_time).total_seconds()
            if age_seconds <= max_age_seconds:
                cleaned[ticker] = detection_time
            else:
                removed_count += 1

    if removed_count > 0:
        state._FIRST_DETECTION_TIMES = cleaned
        save_first_detection_times()
        if settings.VERBOSE:
            print(
                f"🧹 Cleaned up {removed_count} old first detection times (kept {len(cleaned)}, "
                f"removed entries older than {max_age_hours:.0f} hours)"
            )

    return removed_count


def record_first_detection_time(event_ticker: str, detection_time: Optional[datetime] = None):
    load_first_detection_times()

    ticker_key = event_key(event_ticker)

    if detection_time is None:
        detection_time = now_utc()

    if ticker_key not in state._FIRST_DETECTION_TIMES:
        state._FIRST_DETECTION_TIMES[ticker_key] = detection_time
        save_first_detection_times()
        if settings.VERBOSE:
            print(
                f"📝 First detection recorded: {event_ticker} at {detection_time.strftime('%H:%M:%S')}"
            )
        return True
    if detection_time < state._FIRST_DETECTION_TIMES[ticker_key]:
        state._FIRST_DETECTION_TIMES[ticker_key] = detection_time
        save_first_detection_times()
        if settings.VERBOSE:
            print(
                f"📝 First detection updated to earlier time: {event_ticker} at {detection_time.strftime('%H:%M:%S')}"
            )
        return True
    return False


def get_first_detection_time(event_ticker: str) -> Optional[datetime]:
    load_first_detection_times()

    ticker_key = event_key(event_ticker)

    if ticker_key in state._FIRST_DETECTION_TIMES and state._FIRST_DETECTION_TIMES[ticker_key]:
        return state._FIRST_DETECTION_TIMES[ticker_key]

    csv_paths = [
        os.path.join(settings.BASE_DIR, "market_snapshots_for_duke_basketball.csv"),
        os.path.join(os.path.dirname(settings.BASE_DIR), "market_snapshots_for_duke_basketball.csv"),
        "market_snapshots_for_duke_basketball.csv",
    ]

    first_ts = None
    csv_path = None
    for path in csv_paths:
        if os.path.exists(path):
            csv_path = path
            break

    if csv_path:
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ticker_match = (
                        row.get("ticker", "").upper() == event_ticker.upper()
                        or row.get("event_ticker", "").upper() == event_ticker.upper()
                    )
                    if ticker_match:
                        for ts_col in ["ts", "kalshi_fetch_ts"]:
                            ts_str = row.get(ts_col, "")
                            if ts_str:
                                try:
                                    ts = parse_iso_utc(ts_str)
                                    if first_ts is None or ts < first_ts:
                                        first_ts = ts
                                except Exception:
                                    continue
            if first_ts:
                if ticker_key in state._FIRST_DETECTION_TIMES:
                    if first_ts < state._FIRST_DETECTION_TIMES[ticker_key]:
                        state._FIRST_DETECTION_TIMES[ticker_key] = first_ts
                        save_first_detection_times()
                else:
                    state._FIRST_DETECTION_TIMES[ticker_key] = first_ts
                    save_first_detection_times()
        except Exception as e:
            if settings.VERBOSE:
                print(f"⚠️ Error reading first detection time from CSV: {e}")

    if first_ts is None:
        first_ts = now_utc()
        state._FIRST_DETECTION_TIMES[ticker_key] = first_ts
        save_first_detection_times()
        if settings.VERBOSE:
            print(
                f"📝 First detection recorded for {event_ticker}: {first_ts.strftime('%H:%M:%S')}"
            )

    return first_ts
