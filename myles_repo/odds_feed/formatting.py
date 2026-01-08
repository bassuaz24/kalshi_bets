from datetime import datetime
from typing import Dict, Any, Optional
from core.time import UTC

STATUS_MAP = {
    "0": "Not started",
    "1": "Live",
    "2": "Finished",
    "3": "Postponed",
    "4": "Cancelled",
    "5": "Walkover",
    "6": "Interrupted",
    "7": "Abandoned",
    "8": "Retired",
}


def _format_status(event: Dict[str, Any]) -> str:
    code = str(event.get("time_status", ""))
    base = STATUS_MAP.get(code, f"status={code}" if code else "Unknown")
    timer = event.get("timer") or {}
    quarter = timer.get("q")
    minutes = timer.get("tm")
    seconds = timer.get("ts")
    if quarter:
        clock = ""
        if minutes not in (None, "") and seconds not in (None, ""):
            sec_str = str(seconds).zfill(2) if isinstance(seconds, (int, float)) else str(seconds).zfill(2)
            clock = f" {minutes}:{sec_str}"
        base = f"{base} | Q{quarter}{clock}"
    return base


def _format_score(event: Dict[str, Any], score_snapshot: Optional[str] = None) -> str:
    if score_snapshot:
        return str(score_snapshot)
    if event.get("ss"):
        return str(event["ss"])
    scores = event.get("scores") or {}
    total = scores.get("7") or {}
    home = total.get("home")
    away = total.get("away")
    if home is None or away is None:
        return "0-0"
    return f"{away}-{home}"


def _parse_period_clock(period_clock: Optional[str]) -> Optional[tuple]:
    if not period_clock:
        return None

    try:
        parts = period_clock.strip().split(" - ")
        if len(parts) != 2:
            return None

        period_str = parts[0].strip()
        time_str = parts[1].strip()

        period = None
        for char in period_str:
            if char.isdigit():
                period = int(char)
                break

        if period is None:
            return None

        time_parts = time_str.split(":")
        if len(time_parts) != 2:
            return None

        minutes = int(time_parts[0])
        seconds = int(time_parts[1])
        total_minutes = minutes + (seconds / 60.0)

        return (period, total_minutes)
    except Exception:
        return None


def _format_epoch(value: Optional[str]) -> str:
    if not value:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(int(value), tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return "unknown"


def _normalize_start_ts(start_at: Optional[str]) -> str:
    if not start_at:
        return datetime.utcnow().isoformat() + "Z"
    start_at = start_at.strip().replace(" ", "T")
    if start_at.endswith("Z") or "+" in start_at:
        return start_at
    return f"{start_at}Z"


def _format_start_time(evt: Dict[str, Any]) -> str:
    start_time = evt.get("time") or evt.get("starts") or evt.get("start_at") or evt.get("starts_at")
    if isinstance(start_time, (int, float)):
        try:
            return datetime.fromtimestamp(start_time, tz=UTC).isoformat()
        except Exception:
            return str(start_time)
    return str(start_time) if start_time else "unknown"
