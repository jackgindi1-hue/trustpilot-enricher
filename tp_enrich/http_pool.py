import threading as _th
import requests as _rq
from requests.adapters import HTTPAdapter as _HTTPAdapter
from urllib3.util.retry import Retry as _Retry

_SESSION = None
_SESSION_LOCK = _th.Lock()

def get_session() -> _rq.Session:
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            return _SESSION
        s = _rq.Session()
        retry = _Retry(total=0, backoff_factor=0, status_forcelist=[], allowed_methods=False, raise_on_status=False)
        adapter = _HTTPAdapter(pool_connections=200, pool_maxsize=200, max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _SESSION = s
        return _SESSION
