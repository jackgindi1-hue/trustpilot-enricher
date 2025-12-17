# ============================================================
# PHASE 2 HOTFIX + SCRAPERS (ANTI-DEBUG-HELL, REAL COVERAGE NOW)
#
# Adds NOW:
#   - Yelp Fusion FIX 400 (requires location or lat/lon)
#   - SerpApi Google Maps fallback
#   - BBB SCRAPE fallback (tries to extract phone + contact/owner-ish names if present)
#   - YellowPages SCRAPE fallback (phone + possible contact/owner-ish names if present)
#   - OpenCorporates SCRAPE fallback (no token) AFTER we have state (best effort)
#
# Goals:
#   - More phones, more emails only where possible, more contact names
#   - Minimal new output fields (no useless CSV bloat)
#   - Very clear logs so you SEE it running
#   - Hard caps so it won't hang on 10k rows
#
# ENV VARS (Railway Variables):
#   REQUIRED for Yelp API:
#     YELP_API_KEY=<your yelp fusion key>
#
#   REQUIRED for SerpApi:
#     SERP_API_KEY=<your serpapi key>
#     (also accepts SERPAPI_API_KEY or SERPAPI_KEY)
#
#   NO KEYS NEEDED for BBB/YP/OpenCorporates scraping in this patch.
# ============================================================

import os
import re
import time
import html
import requests
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import quote_plus

# -----------------------------
# shared helpers
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
    # NANP sanity
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

def _safe_get(url: str, *, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None,
              timeout: int = 15) -> Tuple[int, str]:
    """
    Safe HTTP GET returning (status_code, text). Never raises.
    """
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        return r.status_code, r.text or ""
    except Exception as e:
        return 0, f"EXCEPTION: {repr(e)}"

def _extract_possible_person_names(text: str, limit: int = 3) -> List[str]:
    """
    Heuristic: extract "First Last" patterns, filter obvious junk.
    Not perfect; best-effort for BBB/YP pages.
    """
    if not text:
        return []
    t = html.unescape(text)
    # Typical "John Doe" pattern, capitalized
    candidates = re.findall(r"\b([A-Z][a-z]{2,20})\s+([A-Z][a-z]{2,20})\b", t)
    names = []
    for a, b in candidates:
        full = f"{a} {b}"
        # filter common non-names
        if full.lower() in ("united states", "better business", "business bureau"):
            continue
        if full not in names:
            names.append(full)
        if len(names) >= limit:
            break
    return names

# ============================================================
# (A) Yelp Fusion FIX 400 (used in BOTH phone_enrichment + Phase2)
# ============================================================

def yelp_fusion_search_phone_fix400(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    key = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
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
            return {"_attempted": False, "_reason": "no lat/lon and no google location fields"}
        params["location"] = loc

    url = "https://api.yelp.com/v3/businesses/search"
    headers = {"Authorization": f"Bearer {key}"}

    sc, body = _safe_get(url, headers=headers, params=params, timeout=15)
    if sc != 200:
        if logger:
            logger.warning(f"PHASE2 Yelp: status={sc} body={body[:160]}")
        return {"_attempted": True, "_reason": f"HTTP {sc}"}

    try:
        import json
        js = json.loads(body)
    except Exception:
        js = {}

    biz = (js.get("businesses") or [None])[0] or {}
    phone = normalize_us_phone(biz.get("display_phone") or biz.get("phone"))
    return {"_attempted": True, "phone": phone, "_reason": "ok" if phone else "ok_no_phone"}

# ============================================================
# (B) SerpApi Google Maps fallback
# ============================================================

def phase2_env_debug(logger=None) -> None:
    yelp = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    serp = _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")
    if logger:
        logger.info(
            "PHASE2 ENV CHECK | "
            f"Yelp={bool(yelp)}({_mask(yelp)}) | "
            f"SerpApi={bool(serp)}({_mask(serp)})"
        )

def _serp_key() -> Optional[str]:
    # IMPORTANT: accept multiple to avoid env-var hell
    return _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")

def serpapi_maps_lookup(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None
) -> Dict[str, Any]:
    key = _serp_key()
    if not key:
        return {"_attempted": False, "serp_notes": "missing SERP key"}

    q_name = (business_name or "").strip()
    if not q_name:
        return {"_attempted": False, "serp_notes": "missing business name"}

    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")
    loc = " ".join([p for p in [city, state] if p]).strip()
    q = q_name if not loc else f"{q_name} {loc}"

    url = "https://serpapi.com/search.json"
    params = {"engine": "google_maps", "q": q, "api_key": key, "type": "search"}

    sc, body = _safe_get(url, params=params, timeout=20)
    if sc != 200:
        if logger:
            logger.warning(f"PHASE2 SerpApi: status={sc} body={body[:160]}")
        return {"_attempted": True, "serp_notes": f"HTTP {sc}"}

    try:
        import json
        js = json.loads(body)
    except Exception:
        js = {}

    results = js.get("local_results") or []
    top = results[0] if results else {}
    phone = normalize_us_phone(top.get("phone"))
    website = top.get("website")
    return {
        "_attempted": True,
        "serp_phone": phone,
        "serp_website": website,
        "serp_notes": "ok" if top else "ok_no_results",
    }

# ============================================================
# (C) BBB SCRAPE (NO KEY)
# ============================================================

def bbb_scrape(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    """
    Best-effort:
      - Find BBB profile page via search query (simple HTML scrape)
      - Extract phone + possible contact names if present in page text
    Hard-capped for speed.
    """
    city = google_payload.get("city") or ""
    state = (google_payload.get("state_region") or google_payload.get("state") or "")
    q = f"site:bbb.org {business_name} {city} {state}".strip()
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(q)}"

    sc, body = _safe_get(search_url, timeout=12)
    if sc != 200:
        if logger:
            logger.info(f"PHASE2 BBB: search failed status={sc}")
        return {"_attempted": True, "bbb_notes": f"search_http_{sc}"}

    # find first bbb.org link
    m = re.search(r'href="(https?://[^"]*bbb\.org/[^"]+)"', body)
    if not m:
        return {"_attempted": True, "bbb_notes": "no_bbb_link"}

    bbb_url = html.unescape(m.group(1))
    sc2, page = _safe_get(bbb_url, timeout=12)
    if sc2 != 200:
        return {"_attempted": True, "bbb_notes": f"profile_http_{sc2}", "bbb_url": bbb_url}

    phone = normalize_us_phone(page)
    names = _extract_possible_person_names(page, limit=3)
    return {
        "_attempted": True,
        "bbb_url": bbb_url,
        "bbb_phone": phone,
        "bbb_names": names,
        "bbb_notes": "ok" if (phone or names) else "ok_no_fields",
    }

# ============================================================
# (D) YELLOWPAGES SCRAPE (NO KEY)
# ============================================================

def yellowpages_scrape(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    city = google_payload.get("city") or ""
    state = (google_payload.get("state_region") or google_payload.get("state") or "")
    q = f"site:yellowpages.com {business_name} {city} {state}".strip()
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(q)}"

    sc, body = _safe_get(search_url, timeout=12)
    if sc != 200:
        return {"_attempted": True, "yp_notes": f"search_http_{sc}"}

    m = re.search(r'href="(https?://[^"]*yellowpages\.com/[^"]+)"', body)
    if not m:
        return {"_attempted": True, "yp_notes": "no_yp_link"}

    yp_url = html.unescape(m.group(1))
    sc2, page = _safe_get(yp_url, timeout=12)
    if sc2 != 200:
        return {"_attempted": True, "yp_notes": f"profile_http_{sc2}", "yp_url": yp_url}

    phone = normalize_us_phone(page)
    names = _extract_possible_person_names(page, limit=3)
    return {
        "_attempted": True,
        "yp_url": yp_url,
        "yp_phone": phone,
        "yp_names": names,
        "yp_notes": "ok" if (phone or names) else "ok_no_fields",
    }

# ============================================================
# (E) OPENCORPORATES SCRAPE (NO TOKEN) — best-effort after state
# ============================================================

def opencorporates_scrape(
    business_name: str,
    state_region: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    """
    No token: use site search via OpenCorporates pages.
    We scope by state if possible (us_<state>).
    """
    st = (state_region or "").strip().upper()
    if not st or len(st) != 2:
        return {"_attempted": False, "oc_notes": "missing_state"}

    # search OpenCorporates web pages
    q = f"site:opencorporates.com {business_name} us_{st.lower()}".strip()
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(q)}"

    sc, body = _safe_get(search_url, timeout=12)
    if sc != 200:
        return {"_attempted": True, "oc_notes": f"search_http_{sc}"}

    m = re.search(r'href="(https?://[^"]*opencorporates\.com/companies/[^"]+)"', body)
    if not m:
        return {"_attempted": True, "oc_notes": "no_company_link"}

    oc_url = html.unescape(m.group(1))
    sc2, page = _safe_get(oc_url, timeout=12)
    if sc2 != 200:
        return {"_attempted": True, "oc_notes": f"profile_http_{sc2}", "oc_url": oc_url}

    # try to extract company number / status (heuristics)
    company_number = None
    status = None
    mnum = re.search(r"Company Number</dt>\s*<dd[^>]*>\s*([^<]+)\s*<", page, re.I)
    if mnum:
        company_number = html.unescape(mnum.group(1)).strip()
    mstat = re.search(r"Status</dt>\s*<dd[^>]*>\s*([^<]+)\s*<", page, re.I)
    if mstat:
        status = html.unescape(mstat.group(1)).strip()

    names = _extract_possible_person_names(page, limit=3)
    return {
        "_attempted": True,
        "oc_url": oc_url,
        "oc_company_number": company_number,
        "oc_status": status,
        "oc_names": names,  # sometimes officers appear in text
        "oc_notes": "ok" if (company_number or status or names) else "ok_no_fields",
    }

# ============================================================
# (F) APPLY PHASE 2 FALLBACKS (NOW INCLUDES SCRAPERS)
# ============================================================

def apply_phase2_fallbacks_logged(
    business_name: str,
    google_payload: Dict[str, Any],
    current_phone: Optional[str],
    current_website: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    """
    Produces ONLY useful extras:
      - normalized_phone (if improved)
      - website_final (if improved)
      - contact_names (best-effort from BBB/YP/OC)
      - source notes for debugging
    """
    out: Dict[str, Any] = {}
    phase2_env_debug(logger)

    phone = normalize_us_phone(current_phone) or current_phone
    website = current_website

    # Always log starting state
    if logger:
        logger.info(f"PHASE2 START | has_phone={bool(phone)} has_website={bool(website)}")

    # 1) Yelp API fallback if phone missing
    if not phone:
        y = yelp_fusion_search_phone_fix400(business_name, google_payload, logger=logger)
        if logger:
            logger.info(f"PHASE2 Yelp attempted={y.get('_attempted')} reason={y.get('_reason')}")
        if y.get("phone"):
            phone = y["phone"]
            out["phone_source"] = "yelp"
            out["phone_confidence"] = "low"

    # 2) SerpApi fallback if phone or website missing
    if (not phone) or (not website):
        s = serpapi_maps_lookup(business_name, google_payload, logger=logger)
        if logger:
            logger.info(f"PHASE2 SerpApi attempted={s.get('_attempted')} notes={s.get('serp_notes')}")
        if not phone and s.get("serp_phone"):
            phone = s["serp_phone"]
            out["phone_source"] = "serpapi_maps"
            out["phone_confidence"] = "low"
        if not website and s.get("serp_website"):
            website = s["serp_website"]

    # 3) BBB scrape (always attempt, but fast-capped)
    b = bbb_scrape(business_name, google_payload, logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB attempted={b.get('_attempted')} notes={b.get('bbb_notes')}")
    # use BBB phone only if still missing
    if not phone and b.get("bbb_phone"):
        phone = b["bbb_phone"]
        out["phone_source"] = "bbb"
        out["phone_confidence"] = "low"

    # collect names
    contact_names: List[str] = []
    for n in (b.get("bbb_names") or []):
        if n not in contact_names:
            contact_names.append(n)

    # 4) YellowPages scrape (always attempt, fast-capped)
    yp = yellowpages_scrape(business_name, google_payload, logger=logger)
    if logger:
        logger.info(f"PHASE2 YP attempted={yp.get('_attempted')} notes={yp.get('yp_notes')}")
    if not phone and yp.get("yp_phone"):
        phone = yp["yp_phone"]
        out["phone_source"] = "yellowpages"
        out["phone_confidence"] = "low"
    for n in (yp.get("yp_names") or []):
        if n not in contact_names:
            contact_names.append(n)

    # 5) OpenCorporates scrape AFTER state (always attempt if state exists)
    st = google_payload.get("state_region") or google_payload.get("state")
    oc = opencorporates_scrape(business_name, st, logger=logger)
    if logger:
        logger.info(f"PHASE2 OC attempted={oc.get('_attempted')} notes={oc.get('oc_notes')}")
    for n in (oc.get("oc_names") or []):
        if n not in contact_names:
            contact_names.append(n)

    # output minimal useful fields
    out["normalized_phone"] = normalize_us_phone(phone) or phone
    out["website_final"] = website

    if contact_names:
        out["contact_names"] = contact_names[:5]  # cap

    # optional: include URLs if present (these are useful, not bloat)
    if b.get("bbb_url"):
        out["bbb_url"] = b["bbb_url"]
    if yp.get("yp_url"):
        out["yp_url"] = yp["yp_url"]
    if oc.get("oc_url"):
        out["oc_url"] = oc["oc_url"]

    # (optional) OC company metadata if found
    if oc.get("oc_company_number"):
        out["oc_company_number"] = oc["oc_company_number"]
    if oc.get("oc_status"):
        out["oc_status"] = oc["oc_status"]

    if logger:
        logger.info(
            f"PHASE2 END | phone={bool(out.get('normalized_phone'))} "
            f"website={bool(out.get('website_final'))} "
            f"names={len(out.get('contact_names') or [])}"
        )

    return out

# Backward compatibility alias
apply_phase2_fallbacks = apply_phase2_fallbacks_logged
