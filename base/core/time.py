"""
Time utilities for UTC datetime handling.
"""

from datetime import datetime

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc


def now_utc():
    """Get current UTC datetime."""
    return datetime.now(UTC)


def parse_iso_utc(s: str):
    """Parse ISO format string to UTC datetime."""
    dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)