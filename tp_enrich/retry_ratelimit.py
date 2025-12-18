# ============================================================
# RETRY + RATE LIMITING: Simple per-provider controls
# ============================================================
import time
from typing import Callable, Any, Dict, Optional

class SimpleRateLimiter:
    """
    Very small per-provider limiter to prevent bursts.
    Not perfect, but safe and additive.
    """
    def __init__(self, min_interval_s: float = 0.25):
        self.min_interval_s = float(min_interval_s)
        self._last: Dict[str, float] = {}

    def wait(self, key: str):
        now = time.time()
        last = self._last.get(key, 0.0)
        delta = now - last
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
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
