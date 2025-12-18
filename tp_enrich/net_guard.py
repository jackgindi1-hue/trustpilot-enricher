import time
import random
from typing import Callable, Any, Tuple

def request_with_retry(
    fn: Callable[[], Any],
    *,
    tries: int = 4,
    base_sleep: float = 0.6,
    max_sleep: float = 6.0,
    retry_on: Tuple[int, ...] = (429, 500, 502, 503, 504),
    logger=None
):
    for i in range(tries):
        try:
            r = fn()
            if r is None:
                raise RuntimeError("no_response")
            code = getattr(r, "status_code", None)
            if code in retry_on:
                raise RuntimeError(f"retry_http_{code}")
            return r
        except Exception as e:
            if logger:
                logger.warning(f"NET retry {i+1}/{tries} err={repr(e)}")
            if i == tries - 1:
                return None
            sleep = min(max_sleep, base_sleep * (2 ** i)) + random.uniform(0, 0.25)
            time.sleep(sleep)
    return None
