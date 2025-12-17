# ============================================================
# PHASE 2 PATCH v2 (ANTI-DEBUG-HELL, REAL COVERAGE)
#
# Fixes:
# 1) Yelp Fusion 400 (phone_enrichment) by ALWAYS providing location OR lat/lon.
# 2) BBB/YP/OC "scrape" returning HTTP 202 in Railway:
#      - Use SerpApi first to find the correct listing URL (site search)
#      - Try fetch with browser headers
#      - If still blocked, extract phone/address/names from SerpApi title/snippet
# 3) Hunter "missing key" even when HUNTER_KEY exists:
#      - remove any precheck that looks only for HUNTER_API_KEY
#      - always use env-first helper: HUNTER_API_KEY OR HUNTER_KEY
#
# REQUIRED ENV VARS (Railway Variables):
#   - YELP_API_KEY
#   - SERP_API_KEY   (we also accept SERPAPI_API_KEY / SERPAPI_KEY)
#   - HUNTER_KEY (or HUNTER_API_KEY)  <-- either works
#
# NO KEYS REQUIRED:
#   - BBB / YellowPages / OpenCorporates (we do via SerpApi + best-effort fetch)
# ============================================================

import os
import re
import time
import requests
from typing import Any, Dict, Optional, List, Tuple

# -----------------------------
# Shared helpers
# -----------------------------

def _env_first(*names: str) -> Optional[str]:
    for n in names:
        v = os.getenv(n)
        if v and str(v).strip():
            return str(v).strip()
    return None

def _mask(s: Optional[str]) -> str:
    if not s:
        return "None"
    s = str(s)
    return "***" if len(s) <= 8 else s[:3] + "***" + s[-3:]

_US_PHONE_RE = re.compile(r"\+?1?\s*[\(\-\.]?\s*(\d{3})\s*[\)\-\.]?\s*(\d{3})\s*[\-\.]?\s*(\d{4})")
def normalize_us_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = _US_PHONE_RE.search(str(raw))
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    if a[0] in ("0", "1") or b[0] in ("0", "1"):
        return None
    return f"({a}) {b}-{c}"

def _build_location_from_google_payload(g: Dict[str, Any]) -> Optional[str]:
    city = g.get("city")
    state = g.get("state_region") or g.get("state")
    postal = g.get("postal_code")
    country = g.get("country") or "US"
    parts = [p for p in [city, state, postal, country] if p]
    return ", ".join(parts) if parts else None

def _browser_headers() -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

def _extract_possible_names(text: str) -> List[str]:
    # extremely conservative: pulls 2-4 word capitalized sequences
    if not text:
        return []
    candidates = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", text)
    # de-dupe while preserving order
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:3]

# ============================================================
# (1) Yelp Fusion FIX (USE THIS in tp_enrich/phone_enrichment.py)
# ============================================================

def yelp_fusion_search_phone_fix400(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    key = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    if logger:
        logger.info(f"PHASE2 Yelp ENV | present={bool(key)} key={_mask(key)}")
    if not key:
        return {"_attempted": False, "_reason": "missing YELP_API_KEY"}

    term = (business_name or "").strip()[:80]
    if not term:
        return {"_attempted": False, "_reason": "missing term"}

    lat = google_payload.get("lat")
    lon = google_payload.get("lng") or google_payload.get("lon")

    params = {"term": term, "limit": 1}
    if lat and lon:
        params["latitude"] = lat
        params["longitude"] = lon
    else:
        loc = _build_location_from_google_payload(google_payload)
        if not loc:
            return {"_attempted": False, "_reason": "no lat/lon and no location from Google"}
        params["location"] = loc

    url = "https://api.yelp.com/v3/businesses/search"
    headers = {"Authorization": f"Bearer {key}"}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            if logger:
                logger.warning(f"PHASE2 Yelp FIX400 failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "_reason": f"HTTP {r.status_code}"}

        js = r.json() or {}
        biz = (js.get("businesses") or [None])[0] or {}
        phone = normalize_us_phone(biz.get("display_phone") or biz.get("phone"))
        return {"_attempted": True, "phone": phone, "_reason": "ok" if phone else "ok_no_phone"}
    except Exception as e:
        return {"_attempted": True, "_reason": f"exception={repr(e)}"}

# ============================================================
# (2) SerpApi helpers (use for BBB/YP/OC discovery + fallback extraction)
# ============================================================

def _serp_key() -> Optional[str]:
    return _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")

def serpapi_google_site_search(
    query: str,
    logger=None,
) -> Dict[str, Any]:
    key = _serp_key()
    if not key:
        return {"_attempted": False, "notes": "missing SERP key"}

    url = "https://serpapi.com/search.json"
    params = {"engine": "google", "q": query, "api_key": key, "num": 5}

    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            if logger:
                logger.warning(f"SerpApi google failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "notes": f"HTTP {r.status_code}"}
        js = r.json() or {}
        organic = js.get("organic_results") or []
        top = organic[0] if organic else {}
        return {
            "_attempted": True,
            "notes": "ok" if top else "ok_no_results",
            "link": top.get("link"),
            "title": top.get("title") or "",
            "snippet": top.get("snippet") or "",
        }
    except Exception as e:
        return {"_attempted": True, "notes": f"exception={repr(e)}"}

def _best_effort_fetch_html(url: str, logger=None) -> Tuple[int, str]:
    try:
        r = requests.get(url, headers=_browser_headers(), timeout=20, allow_redirects=True)
        return r.status_code, (r.text or "")[:200000]
    except Exception as e:
        if logger:
            logger.warning(f"Fetch exception for {url}: {repr(e)}")
        return 0, ""

def _extract_phone_from_text(text: str) -> Optional[str]:
    return normalize_us_phone(text)

# ============================================================
# (3) BBB + YellowPages + OpenCorporates via SerpApi-first
# ============================================================

def phase2_bbb_yp_oc(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    city = google_payload.get("city")
    st = google_payload.get("state_region") or google_payload.get("state")
    loc = " ".join([p for p in [city, st] if p]).strip()

    out: Dict[str, Any] = {"contact_names": []}

    def _site_run(site: str, label: str) -> None:
        q = f'site:{site} "{business_name}" {loc}'.strip()
        res = serpapi_google_site_search(q, logger=logger)
        if logger:
            logger.info(f"PHASE2 {label}: serp attempted={res.get('_attempted')} notes={res.get('notes')} link={res.get('link')}")
        if not res.get("_attempted") or not res.get("link"):
            out[f"{label.lower()}_notes"] = f"serp_{res.get('notes')}"
            return

        link = res["link"]
        title = res.get("title","")
        snip = res.get("snippet","")
        out[f"{label.lower()}_url"] = link

        # Pull phone from snippet/title when available
        ph = _extract_phone_from_text(title + " " + snip)
        if ph:
            out.setdefault("phones_found", [])
            out["phones_found"].append({"source": f"{label}_serp", "phone": ph})

        # Names from title/snippet (best-effort)
        for nm in _extract_possible_names(title + " " + snip):
            if nm not in out["contact_names"]:
                out["contact_names"].append(nm)

        # Try fetch HTML; if blocked with 202/403/503, we still keep serp result
        status, html = _best_effort_fetch_html(link, logger=logger)
        if logger:
            logger.info(f"PHASE2 {label}: fetch status={status} html_len={len(html)}")
        if status in (202, 403, 503) or status == 0 or not html:
            out[f"{label.lower()}_notes"] = f"fetch_blocked_http_{status}"
            return

        # Best-effort: pull phone again from HTML
        ph2 = _extract_phone_from_text(html)
        if ph2:
            out.setdefault("phones_found", [])
            out["phones_found"].append({"source": f"{label}_html", "phone": ph2})

        # Best-effort: names from HTML
        for nm in _extract_possible_names(html):
            if nm not in out["contact_names"]:
                out["contact_names"].append(nm)

        out[f"{label.lower()}_notes"] = "ok"

    # Run them (these do NOT require paid BBB/YP/OC tokens)
    _site_run("bbb.org", "BBB")
    _site_run("yellowpages.com", "YP")
    _site_run("opencorporates.com", "OC")

    return out

# ============================================================
# (4) Hunter key logic fix (DO NOT precheck only HUNTER_API_KEY)
# ============================================================

def get_hunter_key() -> Optional[str]:
    # accept both, so you never get burned again
    return _env_first("HUNTER_API_KEY", "HUNTER_KEY")

def hunter_env_debug(logger=None) -> None:
    k = get_hunter_key()
    if logger:
        logger.info(
            f"HUNTER ENV CHECK (FIXED): HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} "
            f"| HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} "
            f"| chosen={_mask(k)}"
        )

# Backward compatibility - provide apply_phase2_fallbacks for pipeline integration
def apply_phase2_fallbacks(
    business_name: str,
    google_payload: Dict[str, Any],
    current_phone: Optional[str],
    current_website: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    """
    Wrapper for backward compatibility with existing pipeline integration.
    Calls phase2_bbb_yp_oc and formats output to match expected schema.
    """
    result = phase2_bbb_yp_oc(business_name, google_payload, logger=logger)
    
    # Format output to match expected schema
    out = {
        "normalized_phone": current_phone,  # Keep existing phone
        "website_final": current_website,    # Keep existing website
        "contact_names": result.get("contact_names", []),
        "bbb_url": result.get("bbb_url"),
        "yp_url": result.get("yp_url"),
        "oc_url": result.get("oc_url"),
    }
    
    # If we found phones and don't have one, use the first found
    if not current_phone and result.get("phones_found"):
        first_phone = result["phones_found"][0]
        out["normalized_phone"] = first_phone["phone"]
        out["phone_source"] = first_phone["source"]
        out["phone_confidence"] = "low"
    
    return out

apply_phase2_fallbacks_logged = apply_phase2_fallbacks  # Alias for compatibility
