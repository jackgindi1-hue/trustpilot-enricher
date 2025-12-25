# ============================================================
# RETRY + RATE LIMITING: Simple per-provider controls
# PHASE 4.6.3: Enhanced with timing utils, thread-safe locking, jitter
# ============================================================
import time
import random
from threading import Lock
from typing import Callable, Any, Dict, Optional


# ============================================================
# PHASE 4.6.3: TIMING UTILITIES
# ============================================================
def timed(logger, step: str):
    """
    Simple timing context manager for performance logging.

    Usage:
        done = timed(logger, "GOOGLE_PLACES")
        # ... do work ...
        done("extra context")
    """
    start = time.time()
    def done(extra=""):
        dur = time.time() - start
        if logger:
            logger.info(f"TIMING: {step} {dur:.2f}s {extra}")
    return done


class SimpleRateLimiter:
    """
    PHASE 4.6.3: Enhanced process-wide rate limiter with jitter.
    Prevents 429 storms when concurrency > 1.
    """
    def __init__(self, min_interval_s: float = 0.25):
        self.min_interval_s = float(min_interval_s)
        self._last: Dict[str, float] = {}
        self._lock = Lock()  # PHASE 4.6.3: Thread-safe for concurrency

    def wait(self, key: str, min_interval_s: Optional[float] = None):
        """
        Wait to ensure minimum interval between calls to same provider.

        Args:
            key: Provider key (e.g., "serpapi", "google_places")
            min_interval_s: Override minimum interval (default uses constructor value)
        """
        interval = min_interval_s if min_interval_s is not None else self.min_interval_s
        now = time.time()

        with self._lock:
            last = self._last.get(key, 0.0)
            wait_s = (last + interval) - now
            if wait_s > 0:
                time.sleep(wait_s)

            # PHASE 4.6.3: Add jitter so concurrent calls don't align
            time.sleep(random.uniform(0.05, 0.20))

            self._last[key] = time.time()


def with_retry(fn: Callable[[], Any], tries: int = 3, base_sleep_s: float = 0.4, logger=None, tag: str = ""):
    last_err = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if logger:
                logger.warning(f"RETRY {tag}: attempt {i+1}/{tries} failed: {repr(e)}")
            time.sleep(base_sleep_s * (2 ** i))
    if logger:
        logger.warning(f"RETRY {tag}: giving up after {tries} attempts: {repr(last_err)}")
    raise last_err
