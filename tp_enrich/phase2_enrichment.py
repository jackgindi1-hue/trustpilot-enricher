# ============================================================
# PHASE 2 PATCH (SAFE, LOW-RISK, MORE COVERAGE)
#
# GOALS:
# 1) Fix Yelp Fusion 400 by ALWAYS sending either:
# 2) Use SerpApi for:
# 3) OpenCorporates lookup ONLY AFTER we have state (optional but recommended)
# 4) Phone normalization + validation
# 5) Email confidence scoring (kept simple)
# 6) Name normalization hardening
# 7) Env var names are robust (accept multiple aliases)
#
# IMPORTANT:
#     phone, website, and listing URLs / optional legal validation.
# ============================================================
import os
import re
import requests
from typing import Any, Dict, Optional
# ENV VAR GETTERS (robust, no more key-name hell)
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
    if len(s) <= 8:
        return "***"
    return s[:3] + "***" + s[-3:]
def phase2_env_debug(logger=None) -> None:
    yelp = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    serp = _env_first("SERPAPI_API_KEY", "SERP_API_KEY", "SERPAPI_KEY")
    oc   = _env_first("OPENCORPORATES_API_TOKEN", "OPENCORPORATES_TOKEN", "OPENCORPORATES_KEY")
    if logger:
        logger.info(
            "PHASE2 ENV CHECK | "
            f"Yelp={bool(yelp)}({_mask(yelp)}) | "
            f"SerpApi={bool(serp)}({_mask(serp)}) | "
            f"OpenCorporates={bool(oc)}({_mask(oc)})"
        )
# PHONE NORMALIZATION / VALIDATION
_US_PHONE_RE = re.compile(r"\+?1?\s*[\(\-\.]?\s*(\d{3})\s*[\)\-\.]?\s*(\d{3})\s*[\-\.]?\s*(\d{4})")
def normalize_us_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw)
    m = _US_PHONE_RE.search(s)
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    # basic NANP sanity
    if a[0] in ("0", "1") or b[0] in ("0", "1"):
        return None
    return f"({a}) {b}-{c}"
# COMPANY SEARCH NAME NORMALIZATION (hardens edge cases)
_STOPWORDS = {
    "llc","inc","corp","co","company","ltd","pllc","pc","the","and","of","at"
}
def normalize_company_search_name(name: str) -> str:
    s = (name or "").strip()
    s = s.replace("&", " and ")
    s = re.sub(r"[""\"']", "", s)
    s = re.sub(r"[\(\)\[\]\{\}]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = []
    for t in re.split(r"[\s,./|:;]+", s.lower()):
        t = t.strip()
        if not t or t in _STOPWORDS:
            continue
        tokens.append(t)
    cleaned = " ".join(tokens[:12]).strip()
    return cleaned if cleaned else s
# YELP FUSION BUSINESS SEARCH (FIX 400)
def _yelp_headers() -> Optional[Dict[str, str]]:
    key = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    if not key:
        return None
    # Yelp Fusion uses a private API key as Bearer auth.
    return {"Authorization": f"Bearer {key}"}
def yelp_business_search(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    """
    Returns: yelp_phone, yelp_url (listing), yelp_notes
    FIX: always send either lat/lon OR a valid location string.
    """
    hdr = _yelp_headers()
    if not hdr:
        return {"_attempted": False, "yelp_notes": "missing Yelp key"}
    term = (business_name or "").strip()[:80]
    if not term:
        return {"_attempted": False, "yelp_notes": "missing term"}
    params = {"term": term, "limit": 1}
    lat = google_payload.get("lat")
    lon = google_payload.get("lng") or google_payload.get("lon")
    if lat and lon:
        params["latitude"] = lat
        params["longitude"] = lon
    else:
        city = google_payload.get("city")
        state = google_payload.get("state_region") or google_payload.get("state")
        postal = google_payload.get("postal_code")
        parts = [p for p in [city, state, postal] if p]
        if not parts:
            return {"_attempted": False, "yelp_notes": "no lat/lon and no city/state/zip from Google"}
        params["location"] = ", ".join(parts)
    url = "https://api.yelp.com/v3/businesses/search"
    try:
        r = requests.get(url, headers=hdr, params=params, timeout=15)
        if r.status_code != 200:
            if logger:
                logger.warning(f"Yelp Fusion failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "yelp_notes": f"status={r.status_code}"}
        js = r.json() or {}
        biz = (js.get("businesses") or [None])[0] or {}
        phone = normalize_us_phone(biz.get("display_phone") or biz.get("phone"))
        yelp_url = biz.get("url")  # Yelp listing URL (still useful)
        return {
            "_attempted": True,
            "yelp_phone": phone,
            "yelp_url": yelp_url,
            "yelp_notes": "ok" if (phone or yelp_url) else "ok_no_fields",
        }
    except Exception as e:
        return {"_attempted": True, "yelp_notes": f"exception={repr(e)}"}
# SERPAPI (Maps + BBB + YellowPages discovery)
def _serp_key() -> Optional[str]:
    # Accept both to prevent another "Hunter key name" incident:
    return _env_first("SERPAPI_API_KEY", "SERP_API_KEY", "SERPAPI_KEY")
def _serp_request(params: Dict[str, Any], logger=None) -> Dict[str, Any]:
    key = _serp_key()
    if not key:
        return {"_attempted": False, "_notes": "missing SERPAPI_API_KEY (or SERP_API_KEY)"}
    url = "https://serpapi.com/search.json"
    params = dict(params)
    params["api_key"] = key
    try:
        r = requests.get(url, params=params, timeout=25)
        if r.status_code != 200:
            if logger:
                logger.warning(f"SerpApi failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "_notes": f"status={r.status_code}"}
        return {"_attempted": True, "_notes": "ok", "json": (r.json() or {})}
    except Exception as e:
        return {"_attempted": True, "_notes": f"exception={repr(e)}"}
def serpapi_maps_lookup(business_name: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")
    q_loc = " ".join([p for p in [city, state] if p]).strip()
    q = (business_name or "").strip()
    if q_loc:
        q = f"{q} {q_loc}"
    res = _serp_request({"engine": "google_maps", "q": q, "type": "search"}, logger=logger)
    if not res.get("_attempted") or "json" not in res:
        return {"_attempted": res.get("_attempted", False), "serp_notes": res.get("_notes")}
    js = res["json"]
    top = (js.get("local_results") or [{}])[0] or {}
    return {
        "_attempted": True,
        "serp_phone": normalize_us_phone(top.get("phone")),
        "serp_website": top.get("website"),
        "serp_address": top.get("address"),
        "serp_notes": "ok",
    }
def serpapi_site_discovery(business_name: str, google_payload: Dict[str, Any], site: str, logger=None) -> Dict[str, Any]:
    """
    Discovers likely listing URL on BBB/YellowPages using SerpApi Google engine.
    Also tries to extract phone from snippet if present.
    """
    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")
    q_loc = " ".join([p for p in [city, state] if p]).strip()
    q_name = (business_name or "").strip()
    q = f"site:{site} {q_name} {q_loc}".strip()
    res = _serp_request({"engine": "google", "q": q, "num": 5}, logger=logger)
    if not res.get("_attempted") or "json" not in res:
        return {"_attempted": res.get("_attempted", False), "notes": res.get("_notes")}
    js = res["json"]
    organic = js.get("organic_results") or []
    top = organic[0] if organic else {}
    link = top.get("link")
    snippet = (top.get("snippet") or "")
    phone = normalize_us_phone(snippet)
    return {
        "_attempted": True,
        "listing_url": link,
        "listing_phone_from_snippet": phone,
        "notes": "ok" if (link or phone) else "ok_no_fields",
    }
# OPENCORPORATES (optional; requires token for sane usage)
def opencorporates_lookup(business_name: str, state_region: Optional[str], logger=None) -> Dict[str, Any]:
    token = _env_first("OPENCORPORATES_API_TOKEN", "OPENCORPORATES_TOKEN", "OPENCORPORATES_KEY")
    if not token:
        return {"_attempted": False, "oc_notes": "missing OPENCORPORATES_API_TOKEN (recommended)"}
    st = (state_region or "").strip().upper()
    if not st or len(st) != 2:
        return {"_attempted": False, "oc_notes": "missing/invalid state (need 2-letter state)"}
    jur = f"us_{st.lower()}"
    q = normalize_company_search_name(business_name)[:80]
    if not q:
        return {"_attempted": False, "oc_notes": "missing business name"}
    url = "https://api.opencorporates.com/v0.4/companies/search"
    params = {"q": q, "jurisdiction_code": jur, "api_token": token}
    try:
        r = requests.get(url, params=params, timeout=25)
        if r.status_code != 200:
            if logger:
                logger.warning(f"OpenCorporates failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "oc_notes": f"status={r.status_code}"}
        js = r.json() or {}
        companies = (((js.get("results") or {}).get("companies")) or [])
        top = (companies[0] or {}).get("company") if companies else {}
        return {
            "_attempted": True,
            "oc_company_name": top.get("name"),
            "oc_company_number": top.get("company_number"),
            "oc_status": top.get("current_status"),
            "oc_notes": "ok" if top else "ok_no_match",
        }
    except Exception as e:
        return {"_attempted": True, "oc_notes": f"exception={repr(e)}"}
# EMAIL CONFIDENCE SCORING
def score_email_confidence(source: Optional[str], email_type: Optional[str]) -> str:
    src = (source or "").lower().strip()
    typ = (email_type or "").lower().strip()
    if src == "hunter" and typ == "generic":
        return "high"
    if src == "hunter" and typ in ("person", "generic"):
        return "medium"
    if src in ("snov", "apollo", "fullenrich") and typ in ("generic", "person"):
        return "medium"
    return "low"
# APPLY PHASE 2 FALLBACKS (minimal output fields)
def apply_phase2_fallbacks(
    business_name: str,
    google_payload: Dict[str, Any],
    current_phone: Optional[str],
    current_website: Optional[str],
    logger=None,
) -> Dict[str, Any]:
    """
    Only returns fields that improve coverage:
      - phone_final, phone_source, phone_confidence
      - website_final
      - bbb_url, yp_url (and optional phone from snippet)
      - oc_company_number / oc_status (optional)
      - short notes
    """
    out: Dict[str, Any] = {}
    phone = normalize_us_phone(current_phone) or current_phone
    website = current_website
    # (Optional) call once per job, not per record (but safe either way)
    # phase2_env_debug(logger)
    # 1) Yelp fallback if phone/website missing
    if (not phone) or (not website):
        y = yelp_business_search(business_name, google_payload, logger=logger)
        if not phone and y.get("yelp_phone"):
            phone = y["yelp_phone"]
            out["phone_source"] = "yelp"
            out["phone_confidence"] = "low"
        out["yelp_url"] = y.get("yelp_url")
        out["yelp_notes"] = y.get("yelp_notes")
    # 2) SerpApi Google Maps fallback if still missing phone/website
    if (not phone) or (not website):
        s = serpapi_maps_lookup(business_name, google_payload, logger=logger)
        if not phone and s.get("serp_phone"):
            phone = s["serp_phone"]
            out["phone_source"] = "serpapi_maps"
            out["phone_confidence"] = "low"
        if not website and s.get("serp_website"):
            website = s["serp_website"]
        out["serp_notes"] = s.get("serp_notes")
    # 3) BBB discovery (coverage boost)
    bbb = serpapi_site_discovery(business_name, google_payload, site="bbb.org", logger=logger)
    out["bbb_url"] = bbb.get("listing_url")
    if not phone and bbb.get("listing_phone_from_snippet"):
        phone = bbb["listing_phone_from_snippet"]
        out["phone_source"] = "bbb_snippet"
        out["phone_confidence"] = "low"
    # 4) YellowPages discovery (coverage boost)
    yp = serpapi_site_discovery(business_name, google_payload, site="yellowpages.com", logger=logger)
    out["yellowpages_url"] = yp.get("listing_url")
    if not phone and yp.get("listing_phone_from_snippet"):
        phone = yp["listing_phone_from_snippet"]
        out["phone_source"] = "yellowpages_snippet"
        out["phone_confidence"] = "low"
    # 5) OpenCorporates AFTER we have state (optional validation)
    st = google_payload.get("state_region") or google_payload.get("state")
    oc = opencorporates_lookup(business_name, st, logger=logger)
    if oc.get("oc_company_number"):
        out["oc_company_number"] = oc.get("oc_company_number")
        out["oc_status"] = oc.get("oc_status")
    out["oc_notes"] = oc.get("oc_notes")
    out["phone_final"] = normalize_us_phone(phone) or phone
    out["website_final"] = website
    return out
# ============================================================
# INTEGRATION (DO THIS ONCE WHERE YOU BUILD FINAL OUTPUT)
#
# In your existing enrichment flow, after Google Places gives you
# phone/website (or they're None), call:
#
#   p2 = apply_phase2_fallbacks(
#         business_name=business_name,
#         google_payload=google_payload,
#         current_phone=primary_phone,
#         current_website=website,
#         logger=logger
#       )
#
# Then set:
#   if not primary_phone and p2.get("phone_final"): primary_phone = p2["phone_final"]
#   if not website and p2.get("website_final"): website = p2["website_final"]
#
# OPTIONAL (but recommended columns):
#   bbb_url = p2.get("bbb_url")
#   yellowpages_url = p2.get("yellowpages_url")
#   oc_company_number = p2.get("oc_company_number")
#   oc_status = p2.get("oc_status")
# ============================================================