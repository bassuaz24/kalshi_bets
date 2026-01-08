from datetime import datetime

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc


def now_utc():
    return datetime.now(UTC)


def parse_iso_utc(s: str):
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
