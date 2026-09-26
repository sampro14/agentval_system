"""A token-bucket rate limiter for request throttling (not tied to accounts or the database)."""


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._tokens = float(capacity)
        self._last = 0.0

    def allow(self, now: float) -> bool:
        """Take one token if available. `now` is a monotonic time in seconds."""
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False
