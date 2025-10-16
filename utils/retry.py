import asyncio
import random
import time
from functools import wraps
from typing import Callable, Iterable, Optional, Tuple, Type, TypeVar, Union, Any

T = TypeVar("T")

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover - optional
    httpx = None  # type: ignore

try:
    import httpcore  # type: ignore
except Exception:  # pragma: no cover - optional
    httpcore = None  # type: ignore


def _default_httpx_exceptions() -> Tuple[Type[BaseException], ...]:
    excs: Tuple[Type[BaseException], ...] = ()
    if httpx is not None:
        excs += (
            getattr(httpx, "ReadError", Exception),
            getattr(httpx, "ReadTimeout", Exception),
            getattr(httpx, "ConnectTimeout", Exception),
            getattr(httpx, "RemoteProtocolError", Exception),
        )
    if httpcore is not None:
        excs += (
            getattr(httpcore, "ReadError", Exception),
            getattr(httpcore, "WriteError", Exception),
            getattr(httpcore, "ConnectError", Exception),
        )
    # Always include ConnectionError/TimeoutError fallbacks
    excs += (ConnectionError, TimeoutError)
    # Filter out generic Exception placeholders
    excs = tuple(e for e in excs if e is not Exception)
    return excs or (ConnectionError, TimeoutError)


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    jitter: Union[float, Tuple[float, float]] = (0.0, 0.5),
    retry_on: Optional[Iterable[Type[BaseException]]] = None,
    before_sleep: Optional[Callable[[int, BaseException, float], None]] = None,
    reraise: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry decorator supporting sync and async callables.

    Args:
        attempts: Max attempts (>=1). attempts=1 means no retries.
        base_delay: Initial delay seconds before first retry.
        max_delay: Upper bound for delay.
        backoff: Multiplier for exponential backoff.
        jitter: Fixed jitter or (min, max) random jitter added to delay.
        retry_on: Exception types to retry on. Defaults to common httpx/httpcore + connection/timeouts.
        before_sleep: Optional hook called as (attempt_index, exception, sleep_seconds) before sleeping.
        reraise: Whether to re-raise after exhausting attempts.
    """

    if retry_on is None:
        retry_on = _default_httpx_exceptions()
    retry_on_tuple = tuple(retry_on)

    def compute_sleep(attempt_idx: int) -> float:
        delay = min(base_delay * (backoff ** max(0, attempt_idx - 1)), max_delay)
        if isinstance(jitter, tuple):
            j = random.uniform(jitter[0], jitter[1])
        else:
            j = float(jitter)
        return max(0.0, delay + j)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_coro = asyncio.iscoroutinefunction(func)

        if is_coro:
            @wraps(func)
            async def aw(*args: Any, **kwargs: Any):
                last_exc: Optional[BaseException] = None
                for attempt in range(1, max(1, attempts) + 1):
                    try:
                        return await func(*args, **kwargs)
                    except asyncio.CancelledError:
                        # Always propagate cancellations immediately
                        raise
                    except retry_on_tuple as e:
                        last_exc = e
                        if attempt >= attempts:
                            if reraise:
                                raise
                            return None  # type: ignore
                        sleep_s = compute_sleep(attempt)
                        if before_sleep:
                            try:
                                before_sleep(attempt, e, sleep_s)
                            except Exception:
                                pass
                        await asyncio.sleep(sleep_s)
                if reraise and last_exc is not None:
                    raise last_exc
                return None  # type: ignore
            return aw
        else:
            @wraps(func)
            def sw(*args: Any, **kwargs: Any):
                last_exc: Optional[BaseException] = None
                for attempt in range(1, max(1, attempts) + 1):
                    try:
                        return func(*args, **kwargs)
                    except retry_on_tuple as e:
                        last_exc = e
                        if attempt >= attempts:
                            if reraise:
                                raise
                            return None  # type: ignore
                        sleep_s = compute_sleep(attempt)
                        if before_sleep:
                            try:
                                before_sleep(attempt, e, sleep_s)
                            except Exception:
                                pass
                        time.sleep(sleep_s)
                if reraise and last_exc is not None:
                    raise last_exc
                return None  # type: ignore
            return sw

    return decorator


def retry_httpx(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    jitter: Union[float, Tuple[float, float]] = (0.0, 0.5),
    before_sleep: Optional[Callable[[int, BaseException, float], None]] = None,
    reraise: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Convenience wrapper for retry() with sensible httpx/httpcore defaults."""
    return retry(
        attempts=attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        backoff=backoff,
        jitter=jitter,
        retry_on=_default_httpx_exceptions(),
        before_sleep=before_sleep,
        reraise=reraise,
    )

