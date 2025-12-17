# ============================================================
# HOTFIX: Fix Hunter using HUNTER_KEY + Fix Phase2 KeyError
# - Hunter: ALWAYS read HUNTER_KEY (fallback to HUNTER_API_KEY if ever added)
# - Phase2: ALWAYS define phase2_* keys so CSV writer never KeyErrors
# - Phase2 YP: link only (no HTML fetch)
# ============================================================

import os
import re
import requests
from typing import Any, Dict, Optional, List, Tuple

# -----------------------------
# ENV
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

def get_hunter_key() -> Optional[str]:
    # You said you only have HUNTER_KEY. This makes it the primary.
    return _env_first("HUNTER_KEY", "HUNTER_API_KEY")

def get_serp_key() -> Optional[str]:
    return _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")

def get_yelp_key() -> Optional[str]:
    return _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")

def phase2_env_debug(logger=None) -> None:
    hk = get_hunter_key()
    sk = get_serp_key()
    yk = get_yelp_key()
    if logger:
        logger.info(
            "ENV CHECK | "
            f"Hunter={bool(hk)}({_mask(hk)}) | "
            f"SerpApi={bool(sk)}({_mask(sk)}) | "
            f"Yelp={bool(yk)}({_mask(yk)})"
        )

# -----------------------------
# PHONE
# -----------------------------
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

# -----------------------------
# HUNTER (DOMAIN SEARCH) - MUST BE THE ONLY HUNTER CALL USED
# -----------------------------
_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
_GENERIC_PREFIXES = ("info@", "support@", "sales@", "hello@", "contact@", "admin@", "office@", "team@")

def _clean_email(s: Any) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    return s.lower() if _EMAIL_RE.match(s) else None

def hunter_domain_search(domain: str, logger=None) -> Dict[str, Any]:
    key = get_hunter_key()

    if logger:
        logger.info(
            "HUNTER KEY CHECK | "
            f"HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} "
            f"(len={len(os.getenv('HUNTER_KEY') or '')}) | "
            f"HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} "
            f"(len={len(os.getenv('HUNTER_API_KEY') or '')}) | "
            f"chosen={_mask(key)}"
        )

    if not key:
        return {"_attempted": False, "_reason": "missing HUNTER_KEY (or HUNTER_API_KEY)"}

    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": key}

    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return {"_attempted": True, "_reason": f"HTTP {r.status_code}: {r.text[:200]}"}

        js = r.json() or {}
        items = (js.get("data") or {}).get("emails") or []

        emails: List[str] = []
        for it in items:
            e = _clean_email((it or {}).get("value"))
            if e:
                emails.append(e)

        # de-dupe preserve order
        seen = set()
        emails = [e for e in emails if not (e in seen or seen.add(e))]

        generic = [e for e in emails if e.startswith(_GENERIC_PREFIXES)]
        person = [e for e in emails if e not in generic]

        primary = generic[0] if generic else (person[0] if person else None)
        email_type = "generic" if generic else ("person" if person else None)

        return {
            "_attempted": True,
            "_reason": "ok" if primary else "ok_no_emails",
            "primary_email": primary,
            "email_type": email_type,
            "generic_emails": generic,
            "person_emails": person,
            "source": "hunter" if primary else None,
        }
    except Exception as e:
        return {"_attempted": True, "_reason": f"exception={repr(e)}"}

# -----------------------------
# YELP (for phone_enrichment.py backward compatibility)
# -----------------------------
def _build_location_from_google_payload(g: Dict[str, Any]) -> Optional[str]:
    city = g.get("city")
    state = g.get("state_region") or g.get("state")
    postal = g.get("postal_code")
    country = g.get("country") or "US"
    parts = [p for p in [city, state, postal, country] if p]
    return ", ".join(parts) if parts else None

def yelp_fusion_search_business(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    key = get_yelp_key()
    if not key:
        return {"_attempted": False, "notes": "missing_key"}

    term = (business_name or "").strip()[:80]
    if not term:
        return {"_attempted": False, "notes": "missing_term"}

    lat = google_payload.get("lat")
    lon = google_payload.get("lng") or google_payload.get("lon")

    params = {"term": term, "limit": 1}
    if lat and lon:
        params["latitude"] = lat
        params["longitude"] = lon
    else:
        loc = _build_location_from_google_payload(google_payload)
        if not loc:
            return {"_attempted": False, "notes": "missing_location_fields"}
        params["location"] = loc

    url = "https://api.yelp.com/v3/businesses/search"
    headers = {"Authorization": f"Bearer {key}"}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            if logger:
                logger.warning(f"Yelp Fusion failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "notes": f"http_{r.status_code}"}

        js = r.json() or {}
        biz = (js.get("businesses") or [None])[0] or {}
        phone = normalize_us_phone(biz.get("display_phone") or biz.get("phone"))
        return {"_attempted": True, "notes": "ok" if phone else "ok_no_phone", "phone": phone, "yelp_url": biz.get("url")}
    except Exception as e:
        return {"_attempted": True, "notes": f"exception={repr(e)}"}

def yelp_phone_lookup_safe(business_name: str, google_payload: Dict[str, Any], logger=None) -> Optional[str]:
    """
    Use THIS inside tp_enrich/phone_enrichment.py (PHONE WATERFALL).
    It will never call Yelp without location/latlon.
    """
    y = yelp_fusion_search_business(business_name, google_payload, logger=logger)
    if logger:
        logger.info(f"Yelp FIX400 attempted={y.get('_attempted')} notes={y.get('notes')}")
    return y.get("phone")

# -----------------------------
# SERPAPI helpers (BBB/YP/OC link discovery)
# -----------------------------
def serpapi_search(engine: str, q: str, logger=None) -> Dict[str, Any]:
    key = get_serp_key()
    if not key:
        return {"_attempted": False, "_reason": "missing SERP_API_KEY"}

    url = "https://serpapi.com/search.json"
    params = {"engine": engine, "q": q, "api_key": key}
    try:
        r = requests.get(url, params=params, timeout=25)
        if r.status_code != 200:
            if logger:
                logger.warning(f"SerpApi failed engine={engine}: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "_reason": f"http_{r.status_code}"}
        return {"_attempted": True, "_reason": "ok", "json": (r.json() or {})}
    except Exception as e:
        return {"_attempted": True, "_reason": f"exception={repr(e)}"}

def serpapi_find_domain_link(q: str, must_contain: str, logger=None) -> Dict[str, Any]:
    res = serpapi_search("google", q, logger=logger)
    if not res.get("_attempted") or res.get("_reason") != "ok":
        return {"_attempted": bool(res.get("_attempted")), "notes": res.get("_reason"), "link": None}

    js = res.get("json") or {}
    organic = js.get("organic_results") or []
    for it in organic[:10]:
        link = (it or {}).get("link") or ""
        if must_contain in link:
            return {"_attempted": True, "notes": "ok", "link": link}
    return {"_attempted": True, "notes": "ok_no_results", "link": None}

def serpapi_maps_top(q: str, logger=None) -> Dict[str, Any]:
    res = serpapi_search("google_maps", q, logger=logger)
    if not res.get("_attempted") or res.get("_reason") != "ok":
        return {"_attempted": bool(res.get("_attempted")), "notes": res.get("_reason")}

    js = res.get("json") or {}
    results = js.get("local_results") or []
    top = results[0] if results else {}
    return {
        "_attempted": True,
        "notes": "ok" if top else "ok_no_results",
        "phone": normalize_us_phone(top.get("phone")),
        "website": top.get("website"),
        "address": top.get("address"),
        "title": top.get("title"),
    }

# -----------------------------
# PHASE 2 APPLY (ALWAYS RETURNS phase2_* KEYS)
# -----------------------------
def apply_phase2_fallbacks(
    business_name: str,
    google_payload: Dict[str, Any],
    current_phone: Optional[str],
    current_website: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    """
    Phase 2 fallback enrichment with guaranteed keys (no KeyError).
    """
    # ALWAYS set defaults so CSV writer never KeyErrors
    out: Dict[str, Any] = {
        "phase2_bbb_url": None,
        "phase2_yp_url": None,
        "phase2_oc_url": None,
        "phase2_bbb_names": [],
        "phase2_yp_names": [],
        "phase2_oc_names": [],
        "phase2_notes": "",
    }

    phone = normalize_us_phone(current_phone) or current_phone
    website = current_website

    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")
    q_loc = " ".join([p for p in [city, state] if p]).strip()
    q = business_name if not q_loc else f"{business_name} {q_loc}"

    if logger:
        phase2_env_debug(logger)
        logger.info(f"PHASE2 START | has_phone={bool(phone)} has_website={bool(website)}")

    # (A) SerpApi maps — only if missing phone/website
    if (not phone) or (not website):
        m = serpapi_maps_top(q, logger=logger)
        if logger:
            logger.info(f"PHASE2 MAPS attempted={m.get('_attempted')} notes={m.get('notes')}")
        if not phone and m.get("phone"):
            phone = m["phone"]
        if not website and m.get("website"):
            website = m["website"]

    # (B) BBB link
    bbb = serpapi_find_domain_link(q + " site:bbb.org", "bbb.org", logger=logger)
    out["phase2_bbb_url"] = bbb.get("link")
    if logger:
        logger.info(f"PHASE2 BBB: serp attempted={bbb.get('_attempted')} notes={bbb.get('notes')} link={bbb.get('link')}")

    # (C) YellowPages link ONLY (NO HTML fetch)
    yp = serpapi_find_domain_link(q + " site:yellowpages.com", "yellowpages.com", logger=logger)
    out["phase2_yp_url"] = yp.get("link")
    if logger:
        logger.info(f"PHASE2 YP: serp attempted={yp.get('_attempted')} notes={yp.get('notes')} link={yp.get('link')} (no HTML fetch to avoid bot blocks)")

    # (D) OpenCorporates link via Serp (no token route)
    oc = serpapi_find_domain_link(q + " site:opencorporates.com", "opencorporates.com", logger=logger)
    out["phase2_oc_url"] = oc.get("link")
    if logger:
        logger.info(f"PHASE2 OC: serp attempted={oc.get('_attempted')} notes={oc.get('notes')} link={oc.get('link')}")

    out["phone_final"] = normalize_us_phone(phone) or phone
    out["website_final"] = website
    out["phase2_notes"] = "ok"
    return out

# Backward compatibility aliases
apply_phase2_fallbacks_v2 = apply_phase2_fallbacks
apply_phase2_fallbacks_logged = apply_phase2_fallbacks

# Hunter env debug alias for backward compatibility
def hunter_env_debug(logger=None) -> None:
    k = get_hunter_key()
    if logger:
        logger.info(
            f"HUNTER ENV CHECK (HOTFIX): HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} "
            f"(len={len(os.getenv('HUNTER_KEY') or '')}) | "
            f"HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} "
            f"(len={len(os.getenv('HUNTER_API_KEY') or '')}) | "
            f"chosen={_mask(k)}"
        )
