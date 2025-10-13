import asyncio
import time
from contextlib import asynccontextmanager


class TokenRateLimiter:
    """Async token rate limiter"""

    def __init__(self, max_tokens: int, period: int = 60):
        self.max_tokens = max_tokens
        self.period = period
        self.token_usage = []

    @asynccontextmanager
    async def acquire(self, tokens: int):
        """Acquire tokens"""
        now = time.time()
        # Remove old usage outside the period
        self.token_usage = [(usage_time, used_tokens) for usage_time, used_tokens in self.token_usage if now - usage_time < self.period]

        # Calculate current usage
        current_usage = sum(used_tokens for _, used_tokens in self.token_usage)

        # Check if we're within limits
        if current_usage + tokens > self.max_tokens:
            if self.token_usage:
                sleep_time = self.period - (now - self.token_usage[0][0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

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
                # Exit the limiter context
                if self.token_context:
                    return await self.token_context.__aexit__(exc_type, exc_val, exc_tb)
                return False

        return _AsyncContext(self, estimated_tokens)
