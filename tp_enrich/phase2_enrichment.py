# ============================================================
# PHASE 2 FIX-ALL PATCH v3 (NO DEBUG HELL)
# Fixes confirmed from your logs:
#  - Yelp 400 STILL happening -> phone_enrichment.py is still calling OLD Yelp code
#    This patch gives you a drop-in function AND a safe wrapper you can call.
#  - YellowPages link=None -> Serp returned ok but our link picker was too strict
#    This patch makes link picking robust and adjusts the YP query to force listings.
#  - OpenCorporates link=None -> make link picking more forgiving (domain match)
#
# REQUIRED ENV VARS:
#   YELP_API_KEY=<yelp fusion key>
#   SERP_API_KEY=<serpapi key>   (aliases supported)
# ============================================================

import os, re, requests
from typing import Any, Dict, Optional, Tuple, List

# -----------------------------
# ENV + helpers
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

def phase2_env_debug(logger=None) -> None:
    yelp = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    serp = _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")
    if logger:
        logger.info(
            "PHASE2 ENV CHECK | "
            f"Yelp={bool(yelp)}({_mask(yelp)}) | "
            f"SerpApi={bool(serp)}({_mask(serp)})"
        )

_US_PHONE_RE = re.compile(r"\+?1?\s*[\(\-\.]?\s*(\d{3})\s*[\)\-\.]?\s*(\d{3})\s*[\-\.]?\s*(\d{4})")

def normalize_us_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = _US_PHONE_RE.search(str(raw))
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    if a[0] in ("0","1") or b[0] in ("0","1"):
        return None
    return f"({a}) {b}-{c}"

def _build_location_from_google_payload(g: Dict[str, Any]) -> Optional[str]:
    city = g.get("city")
    state = g.get("state_region") or g.get("state")
    postal = g.get("postal_code")
    country = g.get("country") or "US"
    parts = [p for p in [city, state, postal, country] if p]
    return ", ".join(parts) if parts else None

# ============================================================
# (A) YELP: GUARANTEED "location OR lat/lon" => NO MORE 400
# ============================================================

def yelp_fusion_search_business(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    key = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
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

# ============================================================
# (B) SERP: fix YP link=None by making link picking less strict
# ============================================================

def _serp_key() -> Optional[str]:
    return _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")

def _serp_search(q: str, engine: str = "google", logger=None) -> Dict[str, Any]:
    key = _serp_key()
    if not key:
        return {"_attempted": False, "notes": "missing_serp_key"}

    url = "https://serpapi.com/search.json"
    params = {"engine": engine, "q": q, "api_key": key}
    try:
        r = requests.get(url, params=params, timeout=25)
        if r.status_code != 200:
            if logger:
                logger.warning(f"SerpApi failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "notes": f"http_{r.status_code}"}
        return {"_attempted": True, "notes": "ok", "json": (r.json() or {})}
    except Exception as e:
        return {"_attempted": True, "notes": f"exception={repr(e)}"}

def _domain_match(link: str, domain: str) -> bool:
    if not link:
        return False
    try:
        return domain.lower().strip("/") in link.lower()
    except Exception:
        return False

def _pick_best_link_any(js: Dict[str, Any], domain: str) -> Optional[str]:
    # 1) organic_results
    for item in (js.get("organic_results") or []):
        link = item.get("link")
        if link and _domain_match(link, domain):
            return link

    # 2) sometimes Serp puts "inline_results" / "inline_images" etc — scan shallowly
    for k in ("inline_results", "top_stories", "related_results"):
        for item in (js.get(k) or []):
            link = item.get("link")
            if link and _domain_match(link, domain):
                return link

    # 3) fallback: search_metadata (rare)
    md = js.get("search_metadata") or {}
    if isinstance(md, dict):
        for v in md.values():
            if isinstance(v, str) and _domain_match(v, domain):
                return v
    return None

def _fetch_html(url: str, logger=None) -> Tuple[int, str]:
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code, (r.text or "")
    except Exception as e:
        if logger:
            logger.warning(f"fetch exception url={url} ex={repr(e)}")
        return 0, ""

# BBB name extraction patterns (tight)
_BBB_ROLE_PATTERNS = [
    r"Principal(?:\(s\))?\s*</[^>]+>\s*<[^>]+>\s*([^<]{2,80})<",
    r"Business Management\s*</[^>]+>\s*<[^>]+>\s*([^<]{2,80})<",
    r"President\s*</[^>]+>\s*<[^>]+>\s*([^<]{2,80})<",
    r"Owner\s*</[^>]+>\s*<[^>]+>\s*([^<]{2,80})<",
    r"CEO\s*</[^>]+>\s*<[^>]+>\s*([^<]{2,80})<",
]

def _clean_person_candidate(s: str) -> Optional[str]:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    bad = ["Business Profile", "Better Business Bureau", "Accredited", "Reviews", "Roofing Contractors", "Gihon", "Profile"]
    if any(b.lower() in s.lower() for b in bad):
        return None
    toks = s.split()
    if len(toks) < 2 or len(toks) > 5:
        return None
    if not re.search(r"[A-Za-z]", s):
        return None
    return s

def bbb_scrape_contacts(business_name: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    city = google_payload.get("city") or ""
    state = (google_payload.get("state_region") or google_payload.get("state") or "").strip()
    q = f'site:bbb.org "{business_name}" {city} {state}'.strip()

    sr = _serp_search(q, engine="google", logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB: serp attempted={sr.get('_attempted')} notes={sr.get('notes')}")
    if not sr.get("_attempted") or sr.get("notes") != "ok":
        return {"_attempted": sr.get("_attempted", False), "notes": f"serp_{sr.get('notes')}", "link": None, "names": []}

    js = sr.get("json") or {}
    link = _pick_best_link_any(js, "bbb.org")
    if logger:
        logger.info(f"PHASE2 BBB: link={link}")

    if not link:
        return {"_attempted": True, "notes": "no_link", "link": None, "names": []}

    status, html = _fetch_html(link, logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB: fetch status={status} html_len={len(html)}")
    if status != 200 or not html:
        return {"_attempted": True, "notes": f"fetch_http_{status}", "link": link, "names": []}

    names: List[str] = []
    for pat in _BBB_ROLE_PATTERNS:
        for m in re.finditer(pat, html, flags=re.I):
            cand = _clean_person_candidate(m.group(1))
            if cand and cand not in names:
                names.append(cand)

    return {"_attempted": True, "notes": "ok" if names else "ok_no_names", "link": link, "names": names}

def yellowpages_link_via_serp(business_name: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    """
    YellowPages link discovery via SerpApi ONLY (no HTML fetch to avoid 403/503).
    Returns the YP link if found, no phone/name extraction.
    """
    city = google_payload.get("city") or ""
    state = (google_payload.get("state_region") or google_payload.get("state") or "").strip()

    # Force BUSINESS listings (YP listing patterns vary; include several)
    q = f'site:yellowpages.com "{business_name}" {city} {state} (mip OR business OR "Get Directions" OR "Phone")'
    sr = _serp_search(q, engine="google", logger=logger)
    if logger:
        logger.info(f"PHASE2 YP: serp attempted={sr.get('_attempted')} notes={sr.get('notes')}")
    if not sr.get("_attempted") or sr.get("notes") != "ok":
        return {"_attempted": sr.get("_attempted", False), "notes": f"serp_{sr.get('notes')}", "link": None}

    js = sr.get("json") or {}
    link = _pick_best_link_any(js, "yellowpages.com")
    if logger:
        logger.info(f"PHASE2 YP: link={link} (no HTML fetch to avoid bot blocks)")

    if not link:
        return {"_attempted": True, "notes": "no_link", "link": None}

    # IMPORTANT: Do NOT fetch yellowpages.com HTML (causes 403/503 bot blocks)
    # Just return the link - it's valuable for coverage even without extraction
    return {
        "_attempted": True,
        "notes": "ok",
        "link": link,
    }

def opencorporates_link_via_serp(business_name: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    st = (google_payload.get("state_region") or google_payload.get("state") or "").strip()
    if not st or len(st) != 2:
        return {"_attempted": False, "notes": "missing_state", "link": None, "oc_company_number": None, "oc_status": None}

    city = google_payload.get("city") or ""
    q = f'site:opencorporates.com "{business_name}" {city} {st}'
    sr = _serp_search(q, engine="google", logger=logger)
    if logger:
        logger.info(f"PHASE2 OC: serp attempted={sr.get('_attempted')} notes={sr.get('notes')}")
    if not sr.get("_attempted") or sr.get("notes") != "ok":
        return {"_attempted": sr.get("_attempted", False), "notes": f"serp_{sr.get('notes')}", "link": None, "oc_company_number": None, "oc_status": None}

    js = sr.get("json") or {}
    link = _pick_best_link_any(js, "opencorporates.com")
    if logger:
        logger.info(f"PHASE2 OC: link={link}")

    if not link:
        return {"_attempted": True, "notes": "ok_no_results", "link": None, "oc_company_number": None, "oc_status": None}

    status, html = _fetch_html(link, logger=logger)
    if logger:
        logger.info(f"PHASE2 OC: fetch status={status} html_len={len(html)}")
    if status != 200 or not html:
        return {"_attempted": True, "notes": f"fetch_http_{status}", "link": link, "oc_company_number": None, "oc_status": None}

    company_number = None
    m = re.search(r"Company number</dt>\s*<dd[^>]*>\s*([^<]{2,50})<", html, flags=re.I)
    if m:
        company_number = m.group(1).strip()

    current_status = None
    m2 = re.search(r"Status</dt>\s*<dd[^>]*>\s*([^<]{2,50})<", html, flags=re.I)
    if m2:
        current_status = m2.group(1).strip()

    return {
        "_attempted": True,
        "notes": "ok",
        "link": link,
        "oc_company_number": company_number,
        "oc_status": current_status,
    }

# ============================================================
# Main entrypoint: apply_phase2_fallbacks_v2
# ============================================================

def apply_phase2_fallbacks_v2(
    business_name: str,
    google_payload: Dict[str, Any],
    current_phone: Optional[str],
    current_website: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    phase2_env_debug(logger)

    has_phone = bool(normalize_us_phone(current_phone) or current_phone)
    has_website = bool(current_website)

    if logger:
        logger.info(f"PHASE2 START | has_phone={has_phone} has_website={has_website}")

    out: Dict[str, Any] = {"phase2_notes": []}

    # BBB
    bbb = bbb_scrape_contacts(business_name, google_payload, logger=logger)
    out["phase2_bbb_link"] = bbb.get("link")
    out["phase2_bbb_names"] = bbb.get("names") or []
    out["phase2_notes"].append(f"bbb:{bbb.get('notes')}")

    # YellowPages (link only, no HTML fetch to avoid bot blocks)
    yp = yellowpages_link_via_serp(business_name, google_payload, logger=logger)
    out["phase2_yp_link"] = yp.get("link")
    out["phase2_notes"].append(f"yp:{yp.get('notes')}")

    # OpenCorporates
    oc = opencorporates_link_via_serp(business_name, google_payload, logger=logger)
    out["phase2_oc_link"] = oc.get("link")
    out["phase2_oc_company_number"] = oc.get("oc_company_number")
    out["phase2_oc_status"] = oc.get("oc_status")
    out["phase2_notes"].append(f"oc:{oc.get('notes')}")

    if logger:
        logger.info(
            f"PHASE2 END | "
            f"bbb_names={len(out['phase2_bbb_names'])} "
            f"yp_phone={bool(out.get('phase2_yp_phone'))} "
            f"yp_names={len(out['phase2_yp_names'])} "
            f"oc_match={bool(out.get('phase2_oc_company_number') or out.get('phase2_oc_link'))}"
        )

    out["phase2_notes"] = "|".join(out["phase2_notes"])
    return out

def apply_phase2_coverage(
    business_name: str,
    google_payload: Dict[str, Any],
    current_phone: Optional[str],
    current_website: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    """
    Simplified Phase 2 coverage boost (minimal, fast, no HTML fetching).
    Returns:
      - phone_final (may be improved)
      - website_final (may be improved)
      - bbb_url (if found)
      - yellowpages_url (if found)
      - yelp_url (if found)
      - phase2_notes (string)
    """
    out: Dict[str, Any] = {}
    phone = normalize_us_phone(current_phone) or current_phone
    website = current_website

    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")
    q_loc = " ".join([p for p in [city, state] if p]).strip()
    q = business_name if not q_loc else f"{business_name} {q_loc}"

    if logger:
        phase2_env_debug(logger)
        logger.info(f"PHASE2 COVERAGE | has_phone={bool(phone)} has_website={bool(website)}")

    # BBB link discovery
    bbb = _serp_search(f'{q} site:bbb.org', engine="google", logger=logger)
    if bbb.get("_attempted") and bbb.get("notes") == "ok":
        link = _pick_best_link_any(bbb.get("json") or {}, "bbb.org")
        if link:
            out["bbb_url"] = link

    # YellowPages link discovery (NO HTML fetch)
    yp = yellowpages_link_via_serp(business_name, google_payload, logger=logger)
    if yp.get("link"):
        out["yellowpages_url"] = yp["link"]

    # Yelp link discovery (if needed)
    if not phone or not website:
        yelp_res = yelp_fusion_search_business(business_name, google_payload, logger=logger)
        if yelp_res.get("_attempted") and yelp_res.get("yelp_url"):
            out["yelp_url"] = yelp_res["yelp_url"]
            if not phone and yelp_res.get("phone"):
                phone = yelp_res["phone"]

    out["phone_final"] = normalize_us_phone(phone) or phone
    out["website_final"] = website
    out["phase2_notes"] = "ok"
    return out

# Backward compatibility aliases
apply_phase2_fallbacks = apply_phase2_fallbacks_v2
apply_phase2_fallbacks_logged = apply_phase2_fallbacks_v2

# Hunter key helpers for backward compatibility
def get_hunter_key() -> Optional[str]:
    # CRITICAL: You only have HUNTER_KEY in Railway, so prioritize it!
    # Still accept HUNTER_API_KEY if ever added later
    return _env_first("HUNTER_KEY", "HUNTER_API_KEY", "HUNTER_IO_KEY")

def hunter_env_debug(logger=None) -> None:
    k = get_hunter_key()
    if logger:
        logger.info(
            f"HUNTER ENV CHECK (FIXED v2): HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} "
            f"(len={len(os.getenv('HUNTER_KEY') or '')}) | "
            f"HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} "
            f"(len={len(os.getenv('HUNTER_API_KEY') or '')}) | "
            f"chosen={_mask(k)}"
        )
