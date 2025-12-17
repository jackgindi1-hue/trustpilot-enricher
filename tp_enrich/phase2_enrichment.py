# ============================================================
# PHASE 2 "FIX ALL" PATCH (single paste)
# Fixes:
#  1) Yelp 400 in phone_enrichment.py (root cause: missing location/lat/lon)
#  2) YellowPages: force Serp to return *business listing* URLs + avoid 403 pain
#  3) BBB: extract REAL contact names only (Principal / Owner / President / etc.)
#  4) OpenCorporates: tighten Serp query + only run AFTER we have state
#  5) Logs: always show WHY something ran / skipped
#
# REQUIRED Railway Vars:
#   - YELP_API_KEY   = <Yelp Fusion key>
#   - SERP_API_KEY   = <SerpApi key>  (aliases supported)
#
# OPTIONAL:
#   - OPENCORPORATES_API_TOKEN = <token>  (ONLY if you later want OC API; scraping uses Serp here)
#
# WHERE TO PASTE
#   1) Paste this whole block into: tp_enrich/phase2_fixes.py  (new file is fine)
#   2) Then apply the 2 small integration edits at bottom:
#        A) phone_enrichment.py (Yelp call)
#        B) pipeline/local_enrichment.py (call apply_phase2_fallbacks_v2)
# ============================================================

import os, re, time, requests
from typing import Any, Dict, Optional, List, Tuple

# -----------------------------
# ENV helpers (anti "Hunter key" fiasco)
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

# -----------------------------
# Phone normalization
# -----------------------------
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
# (1) YELP FIX (guaranteed no 400 as long as Google gave city/state OR lat/lon)
# ============================================================

def yelp_fusion_search_business(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    """
    Yelp Fusion v3 businesses/search:
    MUST send either:
      - location=<string>  OR
      - latitude + longitude
    """
    key = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    if not key:
        return {"_attempted": False, "notes": "missing YELP_API_KEY"}

    term = (business_name or "").strip()[:80]
    if not term:
        return {"_attempted": False, "notes": "missing term"}

    lat = google_payload.get("lat")
    lon = google_payload.get("lng") or google_payload.get("lon")

    params = {"term": term, "limit": 1}
    if lat and lon:
        params["latitude"] = lat
        params["longitude"] = lon
    else:
        loc = _build_location_from_google_payload(google_payload)
        if not loc:
            return {"_attempted": False, "notes": "no lat/lon and no location fields from Google"}
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
        yelp_url = biz.get("url")
        return {
            "_attempted": True,
            "notes": "ok" if (phone or yelp_url) else "ok_no_fields",
            "phone": phone,
            "yelp_url": yelp_url,
        }
    except Exception as e:
        return {"_attempted": True, "notes": f"exception={repr(e)}"}

# ============================================================
# (2) SERPAPI helpers (we'll use Serp for BBB/YP/OC "scrape now")
# ============================================================

def _serp_key() -> Optional[str]:
    # accept aliases so you can't get burned
    return _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")

def _serp_search(q: str, engine: str = "google", logger=None) -> Dict[str, Any]:
    key = _serp_key()
    if not key:
        return {"_attempted": False, "notes": "missing SERP_API_KEY"}
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

def _pick_best_link(js: Dict[str, Any], domain_contains: str) -> Optional[str]:
    # Prefer organic_results links
    for item in (js.get("organic_results") or []):
        link = item.get("link")
        if link and domain_contains in link:
            return link
    # Fallback: inline "knowledge_graph" style
    kg = js.get("knowledge_graph") or {}
    if isinstance(kg, dict):
        for k in ("website", "source", "link"):
            if kg.get(k) and domain_contains in str(kg.get(k)):
                return str(kg.get(k))
    return None

def _fetch_html(url: str, logger=None) -> Tuple[int, str]:
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code, (r.text or "")
    except Exception as e:
        if logger:
            logger.warning(f"fetch exception url={url} ex={repr(e)}")
        return 0, ""

# ============================================================
# (3) BBB "SCRAPE NOW" (good: you already get 200)
# Extract ONLY real-looking person names from specific BBB sections.
# ============================================================

# tight patterns: names next to "Principal", "Owner", "President", etc.
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
    # reject obvious garbage
    bad = ["Business Profile", "Better Business Bureau", "Accredited", "Reviews", "Roofing Contractors", "Gihon", "Profile"]
    if any(b.lower() in s.lower() for b in bad):
        return None
    # require at least 2 tokens and mostly letters
    toks = s.split()
    if len(toks) < 2 or len(toks) > 5:
        return None
    if not re.search(r"[A-Za-z]", s):
        return None
    return s

def bbb_scrape_contacts(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    city = google_payload.get("city") or ""
    state = (google_payload.get("state_region") or google_payload.get("state") or "").strip()
    q = f'site:bbb.org "{business_name}" {city} {state}'.strip()

    sr = _serp_search(q, engine="google", logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB: serp attempted={sr.get('_attempted')} notes={sr.get('notes')}")
    if not sr.get("_attempted") or sr.get("notes") != "ok":
        return {"_attempted": sr.get("_attempted", False), "notes": f"serp_{sr.get('notes')}"}

    js = sr.get("json") or {}
    link = _pick_best_link(js, "bbb.org/")
    if logger:
        logger.info(f"PHASE2 BBB: link={link}")

    if not link:
        return {"_attempted": True, "notes": "no_link"}

    status, html = _fetch_html(link, logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB: fetch status={status} html_len={len(html)}")
    if status != 200 or not html:
        return {"_attempted": True, "notes": f"fetch_http_{status}", "link": link}

    names: List[str] = []
    for pat in _BBB_ROLE_PATTERNS:
        for m in re.finditer(pat, html, flags=re.I):
            cand = _clean_person_candidate(m.group(1))
            if cand and cand not in names:
                names.append(cand)

    return {"_attempted": True, "notes": "ok" if names else "ok_no_names", "link": link, "names": names}

# ============================================================
# (4) YellowPages "SCRAPE NOW"
# Fix: query must force a business listing URL, not a category page.
# Also: YP often 403s server-side. We still try fetch, but also capture link.
# ============================================================

def yellowpages_scrape_contacts(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    city = google_payload.get("city") or ""
    state = (google_payload.get("state_region") or google_payload.get("state") or "").strip()

    # Force listing URLs (YP often uses /mip/ or /biz/). This avoids category pages.
    q = f'site:yellowpages.com ("{business_name}") ({city} {state}) (mip OR biz OR "phone")'.strip()

    sr = _serp_search(q, engine="google", logger=logger)
    if logger:
        logger.info(f"PHASE2 YP: serp attempted={sr.get('_attempted')} notes={sr.get('notes')}")
    if not sr.get("_attempted") or sr.get("notes") != "ok":
        return {"_attempted": sr.get("_attempted", False), "notes": f"serp_{sr.get('notes')}"}

    js = sr.get("json") or {}
    link = _pick_best_link(js, "yellowpages.com/")
    if logger:
        logger.info(f"PHASE2 YP: link={link}")

    if not link:
        return {"_attempted": True, "notes": "no_link"}

    status, html = _fetch_html(link, logger=logger)
    if logger:
        logger.info(f"PHASE2 YP: fetch status={status} html_len={len(html)}")

    # Minimal extraction: try to pull a phone from HTML even if page loads.
    phone = None
    if status == 200 and html:
        # YP pages usually include tel: or obvious phone patterns
        m = re.search(r"tel:\+?1?(\d{10})", html)
        if m:
            phone = normalize_us_phone(m.group(1))
        if not phone:
            m2 = re.search(r"(\(\d{3}\)\s*\d{3}[-\s]\d{4})", html)
            if m2:
                phone = normalize_us_phone(m2.group(1))

    # Contact name is rare on YP; keep it empty unless we see a "Contact" label.
    contact_names: List[str] = []
    if status == 200 and html:
        # Extremely conservative: only if explicit "Contact:" appears.
        for m in re.finditer(r"Contact[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})", html):
            cand = _clean_person_candidate(m.group(1))
            if cand and cand not in contact_names:
                contact_names.append(cand)

    return {
        "_attempted": True,
        "notes": "ok" if (status == 200) else f"fetch_http_{status}",
        "link": link,
        "phone": phone,
        "names": contact_names,
    }

# ============================================================
# (5) OpenCorporates "SCRAPE NOW via Serp" (no token needed)
# Your ask: no paid token. So we DO NOT use OC API here.
# We use Serp to find OpenCorporates page & scrape minimal info.
# ============================================================

def opencorporates_serp_scrape(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    st = (google_payload.get("state_region") or google_payload.get("state") or "").strip()
    if not st or len(st) != 2:
        return {"_attempted": False, "notes": "missing_state"}

    city = google_payload.get("city") or ""
    q = f'site:opencorporates.com "{business_name}" {city} {st}'.strip()

    sr = _serp_search(q, engine="google", logger=logger)
    if logger:
        logger.info(f"PHASE2 OC: serp attempted={sr.get('_attempted')} notes={sr.get('notes')}")
    if not sr.get("_attempted") or sr.get("notes") != "ok":
        return {"_attempted": sr.get("_attempted", False), "notes": f"serp_{sr.get('notes')}"}

    js = sr.get("json") or {}
    link = _pick_best_link(js, "opencorporates.com/")
    if logger:
        logger.info(f"PHASE2 OC: link={link}")

    if not link:
        return {"_attempted": True, "notes": "ok_no_results", "link": None}

    status, html = _fetch_html(link, logger=logger)
    if logger:
        logger.info(f"PHASE2 OC: fetch status={status} html_len={len(html)}")
    if status != 200 or not html:
        return {"_attempted": True, "notes": f"fetch_http_{status}", "link": link}

    # Very light extraction (safe)
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
# (6) ONE ENTRYPOINT: apply_phase2_fallbacks_v2
# - Runs ALWAYS (but cheaply) and LOGS clearly.
# - Does NOT add "useless columns"; it tries to increase coverage.
# ============================================================

def apply_phase2_fallbacks_v2(
    business_name: str,
    google_payload: Dict[str, Any],
    current_phone: Optional[str],
    current_website: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    """
    Output keys (minimal):
      - phase2_bbb_link, phase2_bbb_names[]
      - phase2_yp_link,  phase2_yp_phone, phase2_yp_names[]
      - phase2_oc_link,  phase2_oc_company_number, phase2_oc_status
      - phase2_notes (summary)
    """
    phase2_env_debug(logger)

    has_phone = bool(normalize_us_phone(current_phone) or current_phone)
    has_website = bool(current_website)

    if logger:
        logger.info(f"PHASE2 START | has_phone={has_phone} has_website={has_website}")

    out: Dict[str, Any] = {"phase2_notes": []}

    # BBB (always worth attempting for names)
    bbb = bbb_scrape_contacts(business_name, google_payload, logger=logger)
    out["phase2_bbb_link"] = bbb.get("link")
    out["phase2_bbb_names"] = bbb.get("names") or []
    out["phase2_notes"].append(f"bbb:{bbb.get('notes')}")

    # YellowPages (attempt; may 403 but Serp link still useful)
    yp = yellowpages_scrape_contacts(business_name, google_payload, logger=logger)
    out["phase2_yp_link"] = yp.get("link")
    out["phase2_yp_names"] = yp.get("names") or []
    out["phase2_yp_phone"] = yp.get("phone")
    out["phase2_notes"].append(f"yp:{yp.get('notes')}")

    # OpenCorporates via Serp scrape (no token)
    oc = opencorporates_serp_scrape(business_name, google_payload, logger=logger)
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

    # Flatten notes
    out["phase2_notes"] = "|".join(out["phase2_notes"])
    return out

# Backward compatibility aliases
apply_phase2_fallbacks = apply_phase2_fallbacks_v2
apply_phase2_fallbacks_logged = apply_phase2_fallbacks_v2

# Hunter key helpers for backward compatibility
def get_hunter_key() -> Optional[str]:
    return _env_first("HUNTER_API_KEY", "HUNTER_KEY")

def hunter_env_debug(logger=None) -> None:
    k = get_hunter_key()
    if logger:
        logger.info(
            f"HUNTER ENV CHECK (FIXED): HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} "
            f"| HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} "
            f"| chosen={_mask(k)}"
        )
