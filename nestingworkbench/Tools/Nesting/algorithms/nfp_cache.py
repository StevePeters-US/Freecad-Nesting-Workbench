"""NFP (No-Fit Polygon) cache for the nesting algorithm.

Provides a thread-safe cache for storing pre-computed No-Fit Polygons so that
identical pairwise NFP calculations are not repeated across nesting runs.
"""

import threading


class NFPCache:
    """Thread-safe cache for No-Fit Polygon computation results.

    Each entry maps a composite key (typically a tuple of part labels,
    relative angle, spacing, deflection, and simplification) to the
    computed NFP data dictionary.
    """

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, key):
        """Return the cached value for *key*, or ``None`` if not present."""
        with self._lock:
            return self._cache.get(key)

    def set(self, key, value):
        """Store *value* under *key* in the cache."""
        with self._lock:
            self._cache[key] = value

    def contains(self, key):
        """Return ``True`` if *key* is present in the cache."""
        with self._lock:
            return key in self._cache

    def invalidate(self, key):
        """Remove a single *key* from the cache, if it exists."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        """Remove all entries from the cache."""
        with self._lock:
            self._cache.clear()

    def __len__(self):
        """Return the number of entries currently cached."""
        with self._lock:
            return len(self._cache)
