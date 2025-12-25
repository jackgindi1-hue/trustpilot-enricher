# ============================================================
# PHASE 2 HOTFIX v2: FIX HUNTER + FIX YP URL + EXTRACT DATA
#
# WHAT THIS FIXES (based on 05:36 logs):
# 1) Hunter: HUNTER_KEY exists but code still logs "missing key"
#    => Centralized key lookup to support both HUNTER_KEY and HUNTER_API_KEY
#
# 2) YellowPages: Getting category page URLs (useless):
#       https://www.yellowpages.com/parkersburg-wv/roofing-contractors
#    => Only accept BUSINESS listing URLs (contains /mip/ or /biz/)
#
# 3) BBB + YP: Extract DATA (phone/email/website/names) not just URLs
# ============================================================

import os
import re
import html
import json
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
# NORMALIZATION / EXTRACTION
# -----------------------------
_US_PHONE_RE = re.compile(r"(?:\+?1[\s\-\.])?(?:\(?([2-9]\d{2})\)?[\s\-\.]?)([2-9]\d{2})[\s\-\.]?(\d{4})")
_EMAIL_RE_GLOBAL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

def normalize_us_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = _US_PHONE_RE.search(str(raw))
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    return f"({a}) {b}-{c}"

def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if not x:
            continue
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out

def _extract_emails(text: str) -> List[str]:
    if not text:
        return []
    text = html.unescape(text)
    emails = [e.lower() for e in _EMAIL_RE_GLOBAL.findall(text)]
    return _dedupe_keep_order(emails)

def _extract_phones(text: str) -> List[str]:
    if not text:
        return []
    text = html.unescape(text)
    phones = []
    for m in _US_PHONE_RE.finditer(text):
        phones.append(f"({m.group(1)}) {m.group(2)}-{m.group(3)}")
    return _dedupe_keep_order(phones)

# Very conservative "name candidate" filter
_BAD_NAME_PHRASES = {
    "business profile", "accredited since", "customer reviews", "bbb", "better business bureau",
    "roofing contractors", "reviews", "complaints", "years in business", "profile",
}

def _pick_contact_name(candidates: List[str]) -> Optional[str]:
    cleaned = []
    for c in candidates:
        if not c:
            continue
        s = " ".join(str(c).strip().split())
        s_low = s.lower()
        if len(s) < 6 or len(s) > 50:
            continue
        if any(p in s_low for p in _BAD_NAME_PHRASES):
            continue
        # require at least 2 words, mostly letters
        parts = s.split()
        if len(parts) < 2:
            continue
        if sum(ch.isalpha() for ch in s) < (len(s) * 0.6):
            continue
        cleaned.append(s)
    cleaned = _dedupe_keep_order(cleaned)
    return cleaned[0] if cleaned else None

def _looks_like_person_name(s: str) -> bool:
    """Very light filter to avoid capturing junk like 'Accredited Since'"""
    if not s:
        return False
    t = s.strip()
    if len(t) < 4 or len(t) > 60:
        return False
    bad = ["accredited", "business profile", "reviews", "complaints", "bbb", "since", "hours", "directions"]
    if any(b in t.lower() for b in bad):
        return False
    # require at least 2 words OR a typical role label
    if len(t.split()) >= 2:
        return True
    return False

# -----------------------------
# HUNTER (DOMAIN SEARCH) - FIXED KEY DETECTION
# -----------------------------
_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
_GENERIC_PREFIXES = ("info@", "support@", "sales@", "hello@", "contact@", "admin@", "office@", "team@")

def _clean_email(s: Any) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    return s.lower() if _EMAIL_RE.match(s) else None

def hunter_domain_search(domain: str, logger=None) -> Dict[str, Any]:
    """
    HOTFIX v2: Centralized Hunter key detection
    Supports both HUNTER_KEY (Railway) and HUNTER_API_KEY (fallback)
    """
    key = get_hunter_key()

    if logger:
        logger.info(
            "HUNTER KEY CHECK (HOTFIX v2) | "
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
        r = requests.get(url, params=params, timeout=15)
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

# ============================================================
# BBB: IMPROVED URL VALIDATION + DATA EXTRACTION
# ============================================================

def _is_bbb_profile_url(url: str) -> bool:
    """Only accept BBB business profile URLs, not search/category pages"""
    if not url:
        return False
    u = url.lower().strip()
    return ("bbb.org/us/" in u) and ("/profile/" in u)

def find_bbb_profile_url_v2(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    """Find BBB profile URL with strict validation"""
    q = f'site:bbb.org/us "{company}"' + (f' "{city} {state}"' if (city and state) else "")
    s = serpapi_google_search(q, logger=logger)
    js = s.get("json") or {}
    organic = js.get("organic_results") or []

    # Only accept profile URLs
    candidates = [r.get("link") for r in organic if _is_bbb_profile_url((r.get("link") or "").strip())]
    url = candidates[0] if candidates else None

    if logger:
        logger.info(f"PHASE2 BBB URL PICK (HOTFIX v2) | found={bool(url)} url={url}")
    return {"attempted": s.get("attempted", False), "notes": s.get("notes"), "url": url}

def _extract_from_html(html: str) -> Dict[str, Any]:
    """Extract phones, emails, and links from HTML"""
    # Phone: grab first valid-looking US number
    phones = [normalize_us_phone(m.group(0)) for m in _US_PHONE_RE.finditer(html or "")]
    phones = _dedupe_keep_order([p for p in phones if p])

    # Emails
    emails = _dedupe_keep_order([m.group(0).lower() for m in _EMAIL_RE_GLOBAL.finditer(html or "")])

    # Websites: naive https links (we'll filter later)
    links = re.findall(r'https?://[^\s"<>]+', html or "")
    links = _dedupe_keep_order(links)

    return {"phones": phones, "emails": emails, "links": links}

def phase2_bbb_enrich_v2(company: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    """Extract actual data from BBB profile (not just URL)"""
    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")

    found = find_bbb_profile_url_v2(company, city, state, logger=logger)
    url = found.get("url")
    if not url:
        return {"attempted": found.get("attempted", False), "notes": f"{found.get('notes')}_no_url"}

    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            if logger:
                logger.info(f"PHASE2 BBB fetch failed: status={r.status_code}")
            return {"attempted": True, "notes": f"fetch_http_{r.status_code}"}

        html_text = r.text or ""
        ex = _extract_from_html(html_text)

        # BBB page will include lots of non-business links; try to find a non-bbb website link
        website = None
        for lk in ex["links"]:
            if "bbb.org" in lk.lower():
                continue
            if "google.com" in lk.lower():
                continue
            if "yellowpages.com" in lk.lower():
                continue
            website = lk
            break

        # Contact names: lightweight extraction from visible text snippets
        name_hits = []
        for pat in [r"(Owner|Principal|President|CEO|Manager)\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})"]:
            for m in re.finditer(pat, html_text, re.I):
                nm = (m.group(2) or "").strip()
                if _looks_like_person_name(nm):
                    name_hits.append(nm)
        name_hits = _dedupe_keep_order(name_hits)

        return {
            "attempted": True,
            "notes": "ok",
            "bbb_phone": (ex["phones"][0] if ex["phones"] else None),
            "bbb_emails": ex["emails"][:5],
            "bbb_email": (ex["emails"][0] if ex["emails"] else None),
            "bbb_website": website,
            "bbb_names": name_hits[:5],
        }
    except Exception as e:
        return {"attempted": True, "notes": f"exception={repr(e)}"}

# ============================================================
# YELLOWPAGES: BUSINESS URL VALIDATION (NO MORE CATEGORY PAGES!)
# ============================================================

def _is_yp_business_url(url: str) -> bool:
    """
    Only accept YellowPages BUSINESS listing URLs.
    Category pages like /roofing-contractors are useless.
    Business listings have /mip/ or /biz/ in the URL.
    """
    if not url:
        return False
    u = url.lower().strip()
    if "yellowpages.com" not in u:
        return False
    # Business listings typically have /mip/ or /biz/ (category pages won't)
    return ("/mip/" in u) or ("/biz/" in u)

def find_yp_business_url_v2(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    """Find YellowPages business listing URL (NOT category page)"""
    q = f'site:yellowpages.com "{company}"' + (f' "{city} {state}"' if (city and state) else "")
    s = serpapi_google_search(q, logger=logger)
    js = s.get("json") or {}
    organic = js.get("organic_results") or []

    # Only accept business listing URLs
    candidates = []
    for r in organic:
        link = (r.get("link") or "").strip()
        if _is_yp_business_url(link):
            candidates.append(link)

    url = candidates[0] if candidates else None
    if logger:
        logger.info(f"PHASE2 YP URL PICK (HOTFIX v2) | found={bool(url)} url={url} (rejected category pages)")
    return {"attempted": s.get("attempted", False), "notes": s.get("notes"), "url": url}

def phase2_yp_enrich_v2(company: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    """Extract actual data from YellowPages business listing (not category page)"""
    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")

    found = find_yp_business_url_v2(company, city, state, logger=logger)
    url = found.get("url")
    if not url:
        return {"attempted": found.get("attempted", False), "notes": f"{found.get('notes')}_no_url"}

    # NOTE: YP may block. We do a single fetch with browser-ish UA.
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=25)
        if r.status_code != 200:
            if logger:
                logger.info(f"PHASE2 YP fetch blocked: status={r.status_code}")
            return {"attempted": True, "notes": f"fetch_http_{r.status_code}"}

        html_text = r.text or ""
        ex = _extract_from_html(html_text)

        # YP pages often include many links; choose something that matches the business domain if possible
        website = None
        gp_site = (google_payload.get("website") or "").lower()
        gp_domain = None
        if gp_site:
            try:
                gp_domain = re.sub(r"^https?://", "", gp_site).split("/")[0]
            except Exception:
                gp_domain = None

        if gp_domain:
            for lk in ex["links"]:
                if gp_domain in lk.lower():
                    website = lk
                    break

        # Name extraction from title
        names = []
        title_m = re.search(r"<title>(.*?)</title>", html_text, re.I | re.S)
        if title_m:
            t = re.sub(r"\s+", " ", title_m.group(1)).strip()
            # strip "Yellow Pages" suffixes
            t = re.sub(r"\s*\|\s*YellowPages.*$", "", t, flags=re.I).strip()
            if t and not _looks_like_person_name(t):
                # company title is fine to store as "listing_name"
                names.append(t)

        return {
            "attempted": True,
            "notes": "ok",
            "yp_phone": (ex["phones"][0] if ex["phones"] else None),
            "yp_emails": ex["emails"][:5],
            "yp_email": (ex["emails"][0] if ex["emails"] else None),
            "yp_website": website,
            "yp_names": _dedupe_keep_order(names)[:3],
        }
    except Exception as e:
        return {"attempted": True, "notes": f"exception={repr(e)}"}

# ============================================================
# PHASE 2 CONTACT DATA EXTRACTION (NEW)
# ============================================================

def serpapi_google_search(query: str, logger=None) -> Dict[str, Any]:
    key = get_serp_key()
    if not key:
        return {"_attempted": False, "notes": "missing SERP key"}

    url = "https://serpapi.com/search.json"
    params = {"engine": "google", "q": query, "api_key": key}

    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            if logger:
                logger.warning(f"PHASE2 SERP google failed: status={r.status_code} body={r.text[:200]}")
            return {"_attempted": True, "notes": f"http_{r.status_code}"}

        js = r.json() or {}
        return {"_attempted": True, "notes": "ok", "json": js}
    except Exception as e:
        return {"_attempted": True, "notes": f"exception={repr(e)}"}

def _first_matching_link(organic: List[Dict[str, Any]], must_contain: str) -> Optional[str]:
    must = (must_contain or "").lower()
    for r in organic or []:
        link = (r.get("link") or "").strip()
        if link and must in link.lower():
            return link
    return None

def find_bbb_profile_url(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    q_parts = [f"site:bbb.org", f'"{company}"']
    if city and state:
        q_parts.append(f'"{city} {state}"')
    q = " ".join(q_parts)

    s = serpapi_google_search(q, logger=logger)
    if not s.get("_attempted"):
        return {"attempted": False, "notes": s.get("notes")}

    js = s.get("json") or {}
    organic = js.get("organic_results") or []
    link = _first_matching_link(organic, "bbb.org/us/")
    return {"attempted": True, "notes": s.get("notes"), "url": link, "serp": s}

def find_yp_url(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    q_parts = [f"site:yellowpages.com", f'"{company}"']
    if city and state:
        q_parts.append(f'"{city} {state}"')
    q = " ".join(q_parts)

    s = serpapi_google_search(q, logger=logger)
    if not s.get("_attempted"):
        return {"attempted": False, "notes": s.get("notes")}

    js = s.get("json") or {}
    organic = js.get("organic_results") or []
    link = _first_matching_link(organic, "yellowpages.com/")
    return {"attempted": True, "notes": s.get("notes"), "url": link, "serp": s}

def find_oc_url(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    q_parts = [f"site:opencorporates.com", f'"{company}"']
    if city and state:
        q_parts.append(f'"{state}"')
    q = " ".join(q_parts)

    s = serpapi_google_search(q, logger=logger)
    if not s.get("_attempted"):
        return {"attempted": False, "notes": s.get("notes")}

    js = s.get("json") or {}
    organic = js.get("organic_results") or []
    link = _first_matching_link(organic, "opencorporates.com/companies/")
    return {"attempted": True, "notes": s.get("notes"), "url": link}

def fetch_html(url: str, logger=None, timeout: int = 25) -> Tuple[Optional[str], str]:
    if not url:
        return None, "no_url"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            if logger:
                logger.info(f"PHASE2 fetch status={r.status_code} url={url}")
            return None, f"http_{r.status_code}"
        return r.text or "", "ok"
    except Exception as e:
        return None, f"exception={repr(e)}"

def bbb_extract_contact_data(bbb_url: str, logger=None) -> Dict[str, Any]:
    html_text, status = fetch_html(bbb_url, logger=logger)
    if status != "ok" or not html_text:
        return {"attempted": True, "notes": status, "bbb_phones": [], "bbb_emails": [], "bbb_contact_name": None}

    phones = _extract_phones(html_text)
    emails = _extract_emails(html_text)

    # crude candidate list for names
    candidates = []
    for label in ["Principal", "Owner", "President", "CEO", "Manager", "Contact", "Business Management"]:
        idx = html_text.lower().find(label.lower())
        if idx != -1:
            window = html.unescape(html_text[idx:idx+400])
            maybe = re.findall(r">([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})<", window)
            candidates.extend(maybe)

    contact_name = _pick_contact_name(candidates)

    return {
        "attempted": True,
        "notes": "ok",
        "bbb_phones": phones,
        "bbb_emails": emails,
        "bbb_contact_name": contact_name,
    }

def yp_extract_from_serp(serp_json: Dict[str, Any]) -> Dict[str, Any]:
    organic = (serp_json or {}).get("organic_results") or []
    # Combine title + snippet for extraction
    blob = " ".join([(r.get("title") or "") + " " + (r.get("snippet") or "") for r in organic[:5]])
    phones = _extract_phones(blob)
    emails = _extract_emails(blob)
    # Name candidates from titles (weak, but better than nothing)
    titles = [r.get("title") for r in organic[:5] if r.get("title")]
    contact_name = _pick_contact_name(titles)
    return {"yp_phones": phones, "yp_emails": emails, "yp_contact_name": contact_name}

def apply_phase2_contact_boost(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None
) -> Dict[str, Any]:
    """
    Returns a minimal set of NEW enrichment fields:
      - phase2_bbb_phone, phase2_bbb_email, phase2_bbb_contact_name
      - phase2_yp_phone,  phase2_yp_email,  phase2_yp_contact_name
      - phase2_oc_match (bool-ish) + phase2_oc_company_url (optional)
    """
    out: Dict[str, Any] = {}

    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")

    if logger:
        logger.info(f"PHASE2 CONTACT BOOST START | business={business_name} city={city} state={state}")

    # 1) BBB
    bbb = find_bbb_profile_url(business_name, city, state, logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB: serp attempted={bbb.get('attempted')} notes={bbb.get('notes')} link={bbb.get('url')}")
    if bbb.get("url"):
        bdata = bbb_extract_contact_data(bbb["url"], logger=logger)
        if logger:
            logger.info(f"PHASE2 BBB extract: notes={bdata.get('notes')} phones={len(bdata.get('bbb_phones') or [])} emails={len(bdata.get('bbb_emails') or [])} name={bool(bdata.get('bbb_contact_name'))}")
        out["phase2_bbb_phone"] = (bdata.get("bbb_phones") or [None])[0]
        out["phase2_bbb_email"] = (bdata.get("bbb_emails") or [None])[0]
        out["phase2_bbb_contact_name"] = bdata.get("bbb_contact_name")

    # 2) YellowPages (Serp only)
    yp = find_yp_url(business_name, city, state, logger=logger)
    if logger:
        logger.info(f"PHASE2 YP: serp attempted={yp.get('attempted')} notes={yp.get('notes')} link={yp.get('url')}")
    serp_json = (yp.get("serp") or {}).get("json") if yp.get("serp") else None
    if serp_json:
        yp_data = yp_extract_from_serp(serp_json)
        out["phase2_yp_phone"] = (yp_data.get("yp_phones") or [None])[0]
        out["phase2_yp_email"] = (yp_data.get("yp_emails") or [None])[0]
        out["phase2_yp_contact_name"] = yp_data.get("yp_contact_name")

    # 3) OpenCorporates (verification only)
    oc = find_oc_url(business_name, city, state, logger=logger)
    if logger:
        logger.info(f"PHASE2 OC: attempted={oc.get('attempted')} notes={oc.get('notes')} link={oc.get('url')}")
    out["phase2_oc_match"] = bool(oc.get("url"))
    out["phase2_oc_company_url"] = oc.get("url")

    # final log summary
    if logger:
        logger.info(
            "PHASE2 CONTACT BOOST END | "
            f"bbb_phone={bool(out.get('phase2_bbb_phone'))} "
            f"bbb_email={bool(out.get('phase2_bbb_email'))} "
            f"bbb_name={bool(out.get('phase2_bbb_contact_name'))} "
            f"yp_phone={bool(out.get('phase2_yp_phone'))} "
            f"yp_email={bool(out.get('phase2_yp_email'))} "
            f"yp_name={bool(out.get('phase2_yp_contact_name'))} "
            f"oc_match={bool(out.get('phase2_oc_match'))}"
        )
    return out

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

# ============================================================
# PHASE 2 DATA ENRICHMENT - HOTFIX v2 (REPLACES OLD VERSION)
# Fixes: Hunter key, YP category pages, better data extraction
# ============================================================

def apply_phase2_data_enrichment(
    company: str,
    google_payload: Dict[str, Any],
    logger=None
) -> Dict[str, Any]:
    """
    HOTFIX v2: Phase 2 data enrichment with fixes for:
    1. Hunter key detection (supports both HUNTER_KEY and HUNTER_API_KEY)
    2. YellowPages business URL validation (no more category pages!)
    3. Actual data extraction (phones, emails, websites, names)

    Returns dict with DATA fields for CSV:
        - phase2_bbb_phone, phase2_bbb_email, phase2_bbb_website, phase2_bbb_names
        - phase2_yp_phone, phase2_yp_email, phase2_yp_website, phase2_yp_names
    """
    out: Dict[str, Any] = {}

    # Env check (visible in logs)
    if logger:
        logger.info(
            "ENV CHECK (HOTFIX v2) | "
            f"Hunter={bool(get_hunter_key())}({_mask(get_hunter_key())}) | "
            f"SerpApi={bool(get_serp_key())}({_mask(get_serp_key())}) | "
            f"Yelp={bool(get_yelp_key())}({_mask(get_yelp_key())})"
        )

    # BBB with improved URL validation
    bbb = phase2_bbb_enrich_v2(company, google_payload, logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB (HOTFIX v2): attempted={bbb.get('attempted')} notes={bbb.get('notes')}")
    out["phase2_bbb_phone"] = bbb.get("bbb_phone")
    out["phase2_bbb_email"] = bbb.get("bbb_email")
    out["phase2_bbb_website"] = bbb.get("bbb_website")
    out["phase2_bbb_names"] = bbb.get("bbb_names") or []

    # YellowPages with business URL validation (no more category pages!)
    yp = phase2_yp_enrich_v2(company, google_payload, logger=logger)
    if logger:
        logger.info(f"PHASE2 YP (HOTFIX v2): attempted={yp.get('attempted')} notes={yp.get('notes')}")
    out["phase2_yp_phone"] = yp.get("yp_phone")
    out["phase2_yp_email"] = yp.get("yp_email")
    out["phase2_yp_website"] = yp.get("yp_website")
    out["phase2_yp_names"] = yp.get("yp_names") or []

    return out

# ============================================================
# PHASE 2 CONTACT DATA EXTRACTION - CLEANER VERSION
# Purpose: Extract actual DATA (not just URLs) from scrapers
# ============================================================

def apply_phase2_contact_boost_DATA(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None
) -> Dict[str, Any]:
    """
    Cleaner Phase 2 contact data extraction.
    Returns actual contact data (phones, emails, names) from BBB, YP, OC.

    Returns dict with:
        - phase2_bbb_phone, phase2_bbb_email, phase2_bbb_contact_name
        - phase2_yp_phone, phase2_yp_email, phase2_yp_contact_name
        - phase2_oc_match (bool)
    """
    out: Dict[str, Any] = {}

    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")

    if logger:
        logger.info(f"PHASE2 CONTACT DATA START | business={business_name} city={city} state={state}")

    phase2_env_debug(logger)

    # BBB: Find URL via SerpApi, then fetch HTML and extract data
    bbb = find_bbb_profile_url(business_name, city, state, logger=logger)
    if logger:
        logger.info(f"PHASE2 BBB: serp attempted={bbb.get('attempted')} notes={bbb.get('notes')} link={bbb.get('url')}")

    if bbb.get("url"):
        bdata = bbb_extract_contact_data(bbb["url"], logger=logger)
        out["phase2_bbb_phone"] = (bdata.get("bbb_phones") or [None])[0]
        out["phase2_bbb_email"] = (bdata.get("bbb_emails") or [None])[0]
        out["phase2_bbb_contact_name"] = bdata.get("bbb_contact_name")
        if logger:
            logger.info(f"PHASE2 BBB extract: phones={len(bdata.get('bbb_phones') or [])} emails={len(bdata.get('bbb_emails') or [])} name={bool(bdata.get('bbb_contact_name'))}")
    else:
        out["phase2_bbb_phone"] = None
        out["phase2_bbb_email"] = None
        out["phase2_bbb_contact_name"] = None

    # YellowPages: Extract from SerpApi snippets only (NO HTML fetch to avoid bot blocks)
    yp = find_yp_url(business_name, city, state, logger=logger)
    if logger:
        logger.info(f"PHASE2 YP: serp attempted={yp.get('attempted')} notes={yp.get('notes')} link={yp.get('url')} (serp-only)")

    serp_json = (yp.get("serp") or {}).get("json") if yp.get("serp") else None
    if serp_json:
        ydata = yp_extract_from_serp(serp_json)
        out["phase2_yp_phone"] = (ydata.get("yp_phones") or [None])[0]
        out["phase2_yp_email"] = (ydata.get("yp_emails") or [None])[0]
        out["phase2_yp_contact_name"] = ydata.get("yp_contact_name")
        if logger:
            logger.info(f"PHASE2 YP extract: phones={len(ydata.get('yp_phones') or [])} emails={len(ydata.get('yp_emails') or [])} name={bool(ydata.get('yp_contact_name'))}")
    else:
        out["phase2_yp_phone"] = None
        out["phase2_yp_email"] = None
        out["phase2_yp_contact_name"] = None

    # OpenCorporates: Verification only (match yes/no)
    oc = find_oc_url(business_name, city, state, logger=logger)
    if logger:
        logger.info(f"PHASE2 OC: attempted={oc.get('attempted')} notes={oc.get('notes')} link={oc.get('url')}")
    out["phase2_oc_match"] = bool(oc.get("url"))
    out["phase2_oc_company_url"] = oc.get("url")

    # Final summary log
    if logger:
        logger.info(
            "PHASE2 CONTACT DATA END | "
            f"bbb_phone={bool(out.get('phase2_bbb_phone'))} "
            f"bbb_email={bool(out.get('phase2_bbb_email'))} "
            f"bbb_name={bool(out.get('phase2_bbb_contact_name'))} | "
            f"yp_phone={bool(out.get('phase2_yp_phone'))} "
            f"yp_email={bool(out.get('phase2_yp_email'))} "
            f"yp_name={bool(out.get('phase2_yp_contact_name'))} | "
            f"oc_match={bool(out.get('phase2_oc_match'))}"
        )

    return out

# ============================================================
# PHASE 2 ANTI-CRASH PATCH
# - Guarantees all phase2_* fields exist on every row
# - Converts list fields to JSON strings for CSV safety
# - Wraps Phase2 enrich so ANY exception becomes notes=exception_...
# ============================================================

def _safe_json_list(x):
    """Convert list to JSON string safely for CSV output"""
    try:
        if x is None:
            return "[]"
        if isinstance(x, list):
            return json.dumps(x[:10], ensure_ascii=False)
        # if it's already a string, keep it
        return json.dumps([str(x)], ensure_ascii=False)
    except Exception:
        return "[]"

def _phase2_defaults() -> Dict[str, Any]:
    """Default values for all Phase 2 fields"""
    return {
        "phase2_bbb_phone": None,
        "phase2_bbb_email": None,
        "phase2_bbb_website": None,
        "phase2_bbb_names": "[]",

        "phase2_yp_phone": None,
        "phase2_yp_email": None,
        "phase2_yp_website": None,
        "phase2_yp_names": "[]",

        "phase2_notes": None,
    }

def apply_phase2_data_enrichment_SAFE(company: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    """
    Drop-in replacement wrapper for apply_phase2_data_enrichment:
    - Calls your existing apply_phase2_data_enrichment(...)
    - Forces defaults for all fields
    - Serializes list fields to JSON strings for CSV safety
    - Never raises exceptions (catches all errors)

    This prevents CSV writer crashes from missing fields or malformed data.
    """
    out = _phase2_defaults()

    try:
        p2 = apply_phase2_data_enrichment(company, google_payload, logger=logger) or {}
        # merge results
        out.update(p2)

    except Exception as e:
        if logger:
            logger.error(f"PHASE2 CRASH GUARD: exception={repr(e)}", exc_info=True)
        out["phase2_notes"] = f"exception_{repr(e)}"
        return out

    # Normalize list-ish fields into JSON strings for CSV safety
    out["phase2_bbb_names"] = _safe_json_list(out.get("phase2_bbb_names"))
    out["phase2_yp_names"] = _safe_json_list(out.get("phase2_yp_names"))

    # Guarantee keys exist even if provider omitted them
    for k, v in _phase2_defaults().items():
        if k not in out:
            out[k] = v

    return out
