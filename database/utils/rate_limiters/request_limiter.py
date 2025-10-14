import asyncio
import time
from contextlib import asynccontextmanager


class RequestRateLimiter:
    """Async request rate limiter with concurrency safety"""

    def __init__(self, max_requests: int, period: int = 60):
        self.max_requests = max_requests
        self.period = period
        self.requests = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self):
        """Acquire permission to make a request"""
        async with self._lock:
            now = time.time()
            # Remove old requests outside the period
            self.requests = [req_time for req_time in self.requests if now - req_time < self.period]

            # Wait until we're within limits
            while len(self.requests) >= self.max_requests:
                sleep_time = self.period - (now - self.requests[0])
                if sleep_time > 0:
                    # Release lock during sleep, then re-acquire and check again
                    self._lock.release()
                    await asyncio.sleep(sleep_time)
                    await self._lock.acquire()
                    # Clean up old requests again after sleeping
                    now = time.time()
                    self.requests = [req_time for req_time in self.requests if now - req_time < self.period]
                else:
                    break

            self.requests.append(now)

        yield

    def context(self):
        """Async context manager for rate limiting"""
        return self.acquire()
