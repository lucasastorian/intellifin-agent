import asyncio
import time
from contextlib import asynccontextmanager


class RequestRateLimiter:
    """Async request rate limiter"""

    def __init__(self, max_requests: int, period: int = 60):
        self.max_requests = max_requests
        self.period = period
        self.requests = []

    @asynccontextmanager
    async def acquire(self):
        """Acquire permission to make a request"""
        now = time.time()
        # Remove old requests outside the period
        self.requests = [req_time for req_time in self.requests if now - req_time < self.period]

        # Check if we're within limits
        if len(self.requests) >= self.max_requests:
            sleep_time = self.period - (now - self.requests[0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        self.requests.append(now)
        yield

    def context(self):
        """Async context manager for rate limiting"""
        return self.acquire()
