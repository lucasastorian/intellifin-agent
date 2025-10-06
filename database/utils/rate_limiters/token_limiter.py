import time
from contextlib import contextmanager


class TokenRateLimiter:
    """Simple token rate limiter"""

    def __init__(self, max_tokens: int, period: int = 60):
        self.max_tokens = max_tokens
        self.period = period
        self.token_usage = []

    @contextmanager
    def acquire(self, tokens: int):
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
                    time.sleep(sleep_time)
        
        self.token_usage.append((now, tokens))
        yield

    def context(self, estimated_tokens: int):
        """Context manager for token rate limiting"""

        class _Context:
            def __init__(self, limiter, tokens):
                self.limiter = limiter
                self.tokens = tokens
                self.actual_tokens = tokens
                self.token_context = None

            def __enter__(self):
                # Acquire tokens using the limiter
                self.token_context = self.limiter.acquire(self.tokens)
                self.token_context.__enter__()

                # Return update function for actual token adjustment
                def update_actual_tokens(actual):
                    self.actual_tokens = actual

                return update_actual_tokens

            def __exit__(self, exc_type, exc_val, exc_tb):
                # Exit the limiter context
                if self.token_context:
                    return self.token_context.__exit__(exc_type, exc_val, exc_tb)
                return False

        return _Context(self, estimated_tokens)
