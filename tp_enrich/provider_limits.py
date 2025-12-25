import threading as _th3

_SEMAPHORES = {
    "serpapi": _th3.BoundedSemaphore(6),
    "bbb_fetch": _th3.BoundedSemaphore(6),
    "oc_fetch": _th3.BoundedSemaphore(6),
    "hunter": _th3.BoundedSemaphore(6),
    "snov": _th3.BoundedSemaphore(6),
    "apollo": _th3.BoundedSemaphore(6),
    "fullenrich": _th3.BoundedSemaphore(4),
}

def sem(name: str):
    return _SEMAPHORES.get(name)
