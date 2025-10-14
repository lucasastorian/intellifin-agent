import asyncio
import time
from contextlib import asynccontextmanager


class TokenRateLimiter:
    """Async token rate limiter with concurrency safety"""

    def __init__(self, max_tokens: int, period: int = 60):
        self.max_tokens = max_tokens
        self.period = period
        self.token_usage = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, tokens: int):
        """Acquire tokens"""
        async with self._lock:
            now = time.time()
            # Remove old usage outside the period
            self.token_usage = [(usage_time, used_tokens) for usage_time, used_tokens in self.token_usage if now - usage_time < self.period]

            # Calculate current usage
            current_usage = sum(used_tokens for _, used_tokens in self.token_usage)

            # Wait until we have enough tokens available
            while current_usage + tokens > self.max_tokens:
                if self.token_usage:
                    sleep_time = self.period - (now - self.token_usage[0][0])
                    if sleep_time > 0:
                        # Release lock during sleep, then re-acquire and check again
                        self._lock.release()
                        await asyncio.sleep(sleep_time)
                        await self._lock.acquire()
                        # Clean up old usage again after sleeping
                        now = time.time()
                        self.token_usage = [(usage_time, used_tokens) for usage_time, used_tokens in self.token_usage if now - usage_time < self.period]
                        current_usage = sum(used_tokens for _, used_tokens in self.token_usage)
                    else:
                        break
                else:
                    break

            self.token_usage.append((now, tokens))

        yield

    def context(self, estimated_tokens: int):
        """Async context manager for token rate limiting"""

        class _AsyncContext:
            def __init__(self, limiter, tokens):
                self.limiter = limiter
                self.tokens = tokens
                self.actual_tokens = tokens
                self.token_context = None

            async def __aenter__(self):
                # Acquire tokens using the limiter
                self.token_context = self.limiter.acquire(self.tokens)
                await self.token_context.__aenter__()

                # Return update function for actual token adjustment
                def update_actual_tokens(actual):
                    self.actual_tokens = actual

                return update_actual_tokens

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                # Update ledger with actual token count if different from estimate
                if self.actual_tokens != self.tokens and self.limiter.token_usage:
                    # Replace the last entry (our estimated tokens) with actual
                    timestamp, _ = self.limiter.token_usage[-1]
                    self.limiter.token_usage[-1] = (timestamp, self.actual_tokens)

                # Exit the limiter context
                if self.token_context:
                    return await self.token_context.__aexit__(exc_type, exc_val, exc_tb)
                return False

        return _AsyncContext(self, estimated_tokens)
