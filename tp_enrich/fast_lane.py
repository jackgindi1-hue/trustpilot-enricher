from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Callable, List, Optional
from tp_enrich.fast_cache import cache_get_ttl, cache_set_ttl

def _safe_key(*parts: Optional[str]) -> str:
    return "|".join([(p or "").strip().lower() for p in parts])

def parallel_enrich_businesses(
    businesses: List[Dict[str, Any]],
    enrich_one_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    concurrency: int = 10,
    log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    cache_ttl_s: int = 86400
) -> List[Dict[str, Any]]:
    n = len(businesses) or 0
    if progress:
        try: progress(0, n)
        except Exception: pass

    out: List[Dict[str, Any]] = [None] * n

    def _log(msg: str):
        if log:
            try: log(msg)
            except Exception: pass

    def _enrich_idx(i: int, b: Dict[str, Any]) -> Dict[str, Any]:
        company = str(b.get("company") or "")
        domain = str(b.get("domain") or "")
        person = str(b.get("person_name") or "")
        gp = b.get("google_payload") or {}
        state = str(gp.get("state_region") or gp.get("state") or "")
        city = str(gp.get("city") or "")

        ck = "enrich:" + _safe_key(company, domain, person, city, state)
        hit = cache_get_ttl(ck, ttl_s=cache_ttl_s)
        if hit:
            return dict(hit)

        res = enrich_one_fn(b) or {}
        cache_set_ttl(ck, dict(res))
        return res

    done = 0
    with ThreadPoolExecutor(max_workers=max(2, int(concurrency or 10))) as ex:
        futs = {ex.submit(_enrich_idx, i, b): i for i, b in enumerate(businesses)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                out[i] = fut.result() or {}
            except Exception as e:
                out[i] = {"phase3_fastlane_error": str(e)[:2000]}
            done += 1
            if progress:
                try: progress(done, n)
                except Exception: pass
            if done % 10 == 0:
                _log(f"FASTLANE: enriched {done}/{n}")
    return out
