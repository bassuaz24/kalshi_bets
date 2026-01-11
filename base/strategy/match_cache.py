"""
Match caching system for market discovery.
Caches successful matches to avoid re-fetching on every loop.
"""

import time
import threading
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field


@dataclass
class CachedMatch:
    """Cached match information."""
    event_ticker: str
    markets: list
    timestamp: float
    expires_at: float = field(init=False)
    
    def __post_init__(self):
        # Cache expires after 30 minutes
        self.expires_at = self.timestamp + (30 * 60)


class MatchCache:
    """Thread-safe cache for market matches."""
    
    def __init__(self):
        self._cache: Dict[str, CachedMatch] = {}
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[CachedMatch]:
        """Get cached match if valid."""
        with self._lock:
            match = self._cache.get(key)
            if match and time.time() < match.expires_at:
                return match
            elif match:
                # Expired - remove it
                del self._cache[key]
            return None
    
    def set(self, key: str, event_ticker: str, markets: list):
        """Cache a match."""
        with self._lock:
            self._cache[key] = CachedMatch(
                event_ticker=event_ticker,
                markets=markets,
                timestamp=time.time()
            )
    
    def clear_expired(self):
        """Remove expired entries."""
        with self._lock:
            now = time.time()
            expired_keys = [
                key for key, match in self._cache.items()
                if now >= match.expires_at
            ]
            for key in expired_keys:
                del self._cache[key]
    
    def clear_all(self):
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()


# Global cache instance
_match_cache = MatchCache()


def get_match_cache() -> MatchCache:
    """Get the global match cache instance."""
    return _match_cache
