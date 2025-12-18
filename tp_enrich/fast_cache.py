import time as _t
import threading as _th2
from typing import Any as _Any, Optional as _Optional, Dict as _Dict, Tuple as _Tuple

_FC_LOCK = _th2.Lock()
_FC_STORE: _Dict[str, _Tuple[float, _Any]] = {}

def cache_get_ttl(key: str, ttl_s: int = 86400) -> _Optional[_Any]:
    now = _t.time()
    with _FC_LOCK:
        v = _FC_STORE.get(key)
        if not v:
            return None
        ts, val = v
        if ttl_s > 0 and (now - ts) > ttl_s:
            _FC_STORE.pop(key, None)
            return None
        return val

def cache_set_ttl(key: str, val: _Any, max_items: int = 100000):
    with _FC_LOCK:
        _FC_STORE[key] = (_t.time(), val)
        if len(_FC_STORE) > max_items:
            for k in list(_FC_STORE.keys())[:10000]:
                _FC_STORE.pop(k, None)
