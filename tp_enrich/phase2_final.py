"""
PHASE 2 LOCK PATCH - Drop-in Module (ANTI-BRICK + MAX COVERAGE)

Purpose:
- Email waterfall: stop ONLY on winner; if Hunter=empty -> continue (Snov/Apollo/FullEnrich) for MAX coverage
- Phase 2 pulls actual DATA from BBB/YP/OC (phone/email/website/company_number/status)
- YP = NO HTML FETCH (uses Serp snippets only to avoid bot blocks)
- Safe schema: append-only columns (no overwriting/syntax mess)

Usage:
- Import functions in pipeline.py and io_utils.py
- Use 3 wiring edits to integrate
"""

import os
import re
import json
import time
import requests
from typing import Any, Dict, Optional, List, Tuple

# -----------------------------
# ENV HELPERS
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

def env_check_hotfix(logger=None) -> None:
    hunter = _env_first("HUNTER_KEY", "HUNTER_API_KEY")
    serp   = _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")
    yelp   = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    if logger:
        logger.info(
            "ENV CHECK | "
            f"Hunter={bool(hunter)}({_mask(hunter)}) | "
            f"SerpApi={bool(serp)}({_mask(serp)}) | "
            f"Yelp={bool(yelp)}({_mask(yelp)})"
        )

# -----------------------------
# NORMALIZATION HELPERS
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

def clean_email(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", s, re.I):
        return s.lower()
    return None

def _build_location_from_google_payload(g: Dict[str, Any]) -> Optional[str]:
    city = g.get("city")
    state = g.get("state_region") or g.get("state")
    postal = g.get("postal_code")
    country = g.get("country") or "US"
    parts = [p for p in [city, state, postal, country] if p]
    return ", ".join(parts) if parts else None

# -----------------------------
# YELP FIX400 (PHONE WATERFALL)
# -----------------------------

def yelp_fix400_search_phone(
    business_name: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    """
    Yelp Fusion v3 business search REQUIRES:
      - location OR (latitude + longitude)
    This prevents status=400.
    """
    key = _env_first("YELP_API_KEY", "YELP_FUSION_API_KEY", "YELP_KEY")
    if not key:
        return {"attempted": False, "notes": "missing_yelp_key"}

    term = (business_name or "").strip()[:80]
    if not term:
        return {"attempted": False, "notes": "missing_term"}

    lat = google_payload.get("lat")
    lon = google_payload.get("lng") or google_payload.get("lon")

    params = {"term": term, "limit": 1}
    if lat and lon:
        params["latitude"] = lat
        params["longitude"] = lon
    else:
        loc = _build_location_from_google_payload(google_payload)
        if not loc:
            return {"attempted": False, "notes": "no_latlon_no_location"}
        params["location"] = loc

    url = "https://api.yelp.com/v3/businesses/search"
    headers = {"Authorization": f"Bearer {key}"}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            if logger:
                logger.warning(f"Yelp FIX400 failed: status={r.status_code} body={r.text[:200]}")
            return {"attempted": True, "notes": f"http_{r.status_code}"}

        js = r.json() or {}
        biz = (js.get("businesses") or [None])[0] or {}
        phone = normalize_us_phone(biz.get("display_phone") or biz.get("phone"))
        return {"attempted": True, "notes": "ok", "phone": phone}
    except Exception as e:
        return {"attempted": True, "notes": f"exception_{repr(e)}"}

# -----------------------------
# EMAIL WATERFALL (STOP ON WINNER)
# -----------------------------

def hunter_domain_search(domain: str, company: str, logger=None) -> Dict[str, Any]:
    key = _env_first("HUNTER_KEY", "HUNTER_API_KEY")
    if logger:
        logger.info(
            "HUNTER ENV CHECK (FINAL PATCH): "
            f"HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} (len={len(os.getenv('HUNTER_KEY') or '')}) | "
            f"HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} (len={len(os.getenv('HUNTER_API_KEY') or '')}) | "
            f"chosen={_mask(key)}"
        )
    if not key:
        return {"attempted": False, "notes": "missing_hunter_key"}

    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": key}
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return {"attempted": True, "notes": f"http_{r.status_code}", "generic": [], "person": []}

        data = r.json() or {}
        emails = []
        for item in (data.get("data", {}).get("emails") or []):
            e = clean_email(item.get("value"))
            if e:
                emails.append(e)

        generic = [e for e in emails if any(p in e for p in ["info@", "support@", "sales@", "hello@", "contact@", "admin@"])]
        person  = [e for e in emails if e not in generic]
        return {"attempted": True, "notes": "ok", "generic": generic, "person": person}
    except Exception as e:
        return {"attempted": True, "notes": f"exception_{repr(e)}", "generic": [], "person": []}

def score_email_confidence(source: str, email_type: str) -> str:
    src = (source or "").lower().strip()
    typ = (email_type or "").lower().strip()
    if src == "hunter" and typ == "generic":
        return "high"
    if src == "hunter" and typ == "person":
        return "medium"
    return "low"

def email_waterfall_enrich(company: str, domain: Optional[str], person_name: Optional[str] = None, logger=None) -> Dict[str, Any]:
    """
    LOCK PATCH: Stop ONLY on winner; if Hunter returns nothing -> continue for MAX coverage
    - Try Hunter first
    - If Hunter returns ANY email -> stop and return it
    - If Hunter returns NOTHING -> continue: Snov -> Apollo -> FullEnrich (if person_name)
    """
    tried: List[str] = []

    def _done(email: Optional[str], source: Optional[str], email_type: Optional[str]) -> Dict[str, Any]:
        return {
            "primary_email": email,
            "email_source": source,
            "email_type": email_type,
            "email_confidence": (score_email_confidence(source, email_type) if email else None),
            "email_tried": ",".join(tried),
        }

    if not domain:
        if logger:
            logger.info("EMAIL WATERFALL: skipped (missing domain)")
        return _done(None, None, None)

    # ---- HUNTER (uses HUNTER_KEY or HUNTER_API_KEY)
    tried.append("hunter")
    hunter_res = hunter_domain_search(company=company, domain=domain, logger=logger)
    hunter_generic = (hunter_res.get("generic") or [])
    hunter_person = (hunter_res.get("person") or [])

    if hunter_generic:
        if logger:
            logger.info("EMAIL WATERFALL: Hunter found generic email -> STOPPING")
        return _done(hunter_generic[0], "hunter", "generic")
    if hunter_person:
        if logger:
            logger.info("EMAIL WATERFALL: Hunter found person email -> STOPPING")
        return _done(hunter_person[0], "hunter", "person")

    if logger:
        logger.info("EMAIL WATERFALL: Hunter returned 0 emails -> continuing to Snov/Apollo/FullEnrich for MAX coverage")

    # ---- SNOV (skip fast if not wired - Diff1)
    tried.append("snov")
    try:
        from .email_enrichment import snov_domain_search
        if not callable(snov_domain_search):
            raise ImportError("snov_domain_search not callable")
        snov_res = snov_domain_search(domain=domain, logger=logger)
        snov_emails = (snov_res.get("emails") or [])
        if snov_emails:
            if logger:
                logger.info("EMAIL WATERFALL: Snov found email -> STOPPING")
            return _done(snov_emails[0], "snov", "generic")
    except (ImportError, AttributeError) as e:
        if logger:
            logger.warning(f"EMAIL WATERFALL: Snov not wired -> skipping")
    except Exception as e:
        if logger:
            logger.warning(f"EMAIL WATERFALL: Snov failed: {repr(e)}")

    # ---- APOLLO (skip fast if not wired - Diff1)
    tried.append("apollo")
    try:
        from .email_enrichment import apollo_domain_search
        if not callable(apollo_domain_search):
            raise ImportError("apollo_domain_search not callable")
        apollo_res = apollo_domain_search(domain=domain, logger=logger)
        apollo_emails = (apollo_res.get("emails") or [])
        if apollo_emails:
            if logger:
                logger.info("EMAIL WATERFALL: Apollo found email -> STOPPING")
            return _done(apollo_emails[0], "apollo", "generic")
    except Exception as e:
        if logger:
            logger.warning(f"EMAIL WATERFALL: Apollo failed: {repr(e)}")

    # ---- FULLENRICH (only if person_name + domain)
    if person_name and domain:
        tried.append("fullenrich")
        try:
            from .email_enrichment import fullenrich_person_enrich
            fe_res = fullenrich_person_enrich(person_name=person_name, domain=domain, logger=logger)
            fe_email = clean_email(fe_res.get("email"))
            if fe_email:
                if logger:
                    logger.info("EMAIL WATERFALL: FullEnrich found email -> STOPPING")
                return _done(fe_email, "fullenrich", "person")
        except Exception as e:
            if logger:
                logger.warning(f"EMAIL WATERFALL: FullEnrich failed: {repr(e)}")
    else:
        if logger:
            logger.info("EMAIL WATERFALL: FullEnrich skipped (needs person_name + domain)")

    if logger:
        logger.info(f"EMAIL WATERFALL: No email found after trying: {','.join(tried)}")
    return _done(None, None, None)

# Backward compatibility alias
true_email_waterfall = email_waterfall_enrich

# -----------------------------
# SERPAPI GOOGLE SEARCH (for BBB/YP/OC URL discovery)
# -----------------------------

def serpapi_google_search(query: str, logger=None) -> Dict[str, Any]:
    """DiffA: Pooled + cached + limited SerpApi calls"""
    key = _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")
    if not key:
        return {"attempted": False, "notes": "missing_serp_key", "json": None}

    qn = (query or "").strip()

    # Try cache first
    try:
        from tp_enrich.fast_cache import cache_get_ttl, cache_set_ttl
        ck = "serp:" + qn.lower()
        hit = cache_get_ttl(ck, ttl_s=86400)
        if hit:
            return {"attempted": True, "notes": "ok_cache", "json": hit}
    except ImportError:
        pass  # cache not available, continue without it

    # Use pooled session + semaphore if available
    try:
        from tp_enrich.http_pool import get_session
        from tp_enrich.provider_limits import sem
        from tp_enrich.net_guard import request_with_retry

        s = get_session()
        limiter = sem("serpapi")

        def _do():
            if limiter:
                with limiter:
                    return s.get("https://serpapi.com/search.json",
                                 params={"engine": "google", "q": qn, "api_key": key},
                                 timeout=20)
            return s.get("https://serpapi.com/search.json",
                         params={"engine": "google", "q": qn, "api_key": key},
                         timeout=20)

        r = request_with_retry(_do, logger=logger, tries=4, base_sleep=0.4, max_sleep=4.0)
        if not r or r.status_code != 200:
            if logger:
                logger.warning(f"SerpApi google failed: status={getattr(r,'status_code',None)}")
            return {"attempted": True, "notes": f"http_{getattr(r,'status_code',None)}", "json": None}

        js = (r.json() or {})

        # Cache result
        try:
            from tp_enrich.fast_cache import cache_set_ttl
            cache_set_ttl(ck, js)
        except ImportError:
            pass

        return {"attempted": True, "notes": "ok", "json": js}

    except ImportError:
        # Fallback to basic requests if Phase 3 modules not available
        url = "https://serpapi.com/search.json"
        params = {"engine": "google", "q": qn, "api_key": key}
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code != 200:
                if logger:
                    logger.warning(f"SerpApi google failed: status={r.status_code} body={r.text[:200]}")
                return {"attempted": True, "notes": f"http_{r.status_code}", "json": None}
            return {"attempted": True, "notes": "ok", "json": (r.json() or {})}
        except Exception as e:
            return {"attempted": True, "notes": f"exception_{repr(e)}", "json": None}

# -----------------------------
# URL PICKERS (BBB / YP / OC)
# -----------------------------

def _is_bbb_profile_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ("bbb.org/us/" in u) and ("/profile/" in u)

def _is_yellowpages_profile_url(url: str) -> bool:
    # Avoid category pages like /roofing-contractors
    # Prefer business profile pattern: /mip/ or /biz/ or /<business-name>-<id>
    if not url:
        return False
    u = url.lower()
    if "yellowpages.com" not in u:
        return False
    if "/search?" in u:
        return False
    if re.search(r"/[a-z0-9\-]+/\d{6,}", u):
        return True
    if "/mip/" in u or "/biz/" in u:
        return True
    # reject obvious category pages
    if re.search(r"/[a-z\-]+$", u) and ("-contractors" in u or "-services" in u):
        return False
    return False

def find_bbb_url(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    q = f'site:bbb.org/us "{company}"' + (f' "{city} {state}"' if (city and state) else "")
    s = serpapi_google_search(q, logger=logger)
    js = s.get("json") or {}
    organic = js.get("organic_results") or []
    candidates = [r.get("link") for r in organic if _is_bbb_profile_url((r.get("link") or "").strip())]
    url = candidates[0] if candidates else None
    if logger:
        logger.info(f"PHASE2 BBB URL PICK | found={bool(url)} url={url}")
    return {"attempted": bool(s.get("attempted")), "notes": s.get("notes"), "url": url, "organic": organic}

def find_yp_url(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    q1 = f'site:yellowpages.com "{company}"' + (f' "{city} {state}"' if (city and state) else "")
    s1 = serpapi_google_search(q1, logger=logger)
    js1 = s1.get("json") or {}
    organic1 = js1.get("organic_results") or []
    candidates1 = [r.get("link") for r in organic1 if _is_yellowpages_profile_url((r.get("link") or "").strip())]
    url = candidates1[0] if candidates1 else None

    organic = organic1
    notes = s1.get("notes")

    if not url:
        q2 = f'site:yellowpages.com (mip OR biz) "{company}"' + (f' "{city} {state}"' if (city and state) else "")
        s2 = serpapi_google_search(q2, logger=logger)
        js2 = s2.get("json") or {}
        organic2 = js2.get("organic_results") or []
        candidates2 = [r.get("link") for r in organic2 if _is_yellowpages_profile_url((r.get("link") or "").strip())]
        url = candidates2[0] if candidates2 else None
        organic = organic2
        notes = f"{notes}->retry({s2.get('notes')})"

    if logger:
        logger.info(f"PHASE2 YP URL PICK | found={bool(url)} url={url}")
    return {"attempted": True, "notes": notes, "url": url, "organic": organic}

def find_oc_url(company: str, state: Optional[str], logger=None) -> Dict[str, Any]:
    # OC is optional; use it only to discover a likely company page.
    q = f'site:opencorporates.com/companies "{company}"' + (f' "{state}"' if state else "")
    s = serpapi_google_search(q, logger=logger)
    js = s.get("json") or {}
    organic = js.get("organic_results") or []
    # accept direct OC company pages only
    url = None
    for r in organic:
        link = (r.get("link") or "").strip()
        if "opencorporates.com/companies/" in link:
            url = link
            break
    if logger:
        logger.info(f"PHASE2 OC URL PICK | found={bool(url)} url={url}")
    return {"attempted": bool(s.get("attempted")), "notes": s.get("notes"), "url": url, "organic": organic}

# -----------------------------
# LIGHT HTML EXTRACTION (BBB / YP / OC)
# (keep super conservative: phone/email/website + a few names)
# -----------------------------

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE_RE = re.compile(r"(\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}")

def _fetch_html(url: str, timeout: int = 20) -> Tuple[int, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    return r.status_code, (r.text or "")

def _extract_from_text(text: str) -> Dict[str, Any]:
    """Extract contact data from any text (HTML, Serp snippets, etc.)"""
    emails = [clean_email(e) for e in _EMAIL_RE.findall(text or "")]
    emails = [e for e in emails if e]
    email = emails[0] if emails else None

    phones = []
    for m in _PHONE_RE.finditer(text or ""):
        phones.append(normalize_us_phone(m.group(0)))
    phones = [p for p in phones if p]
    phone = phones[0] if phones else None

    urls = re.findall(r"https?://[^\s\"'<>]+", text or "", flags=re.I)
    urls = [u for u in urls if u and all(bad not in u.lower() for bad in ["bbb.org", "yellowpages.com", "opencorporates.com", "google.com"])]
    website = urls[0] if urls else None

    return {"phone": phone, "email": email, "website": website}

def _extract_from_serp_organic(organic_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract contact data from Serp organic results (titles + snippets + highlighted words)"""
    blob = ""
    for r in (organic_results or [])[:8]:
        blob += " " + (r.get("title") or "")
        blob += " " + (r.get("snippet") or "")
        blob += " " + (r.get("link") or "")
        blob += " " + " ".join((r.get("snippet_highlighted_words") or [])[:8])
    return _extract_from_text(blob)

def _extract_bbb_from_html(html: str) -> Dict[str, Any]:
    """Extract BBB contact data from HTML (better extraction with JSON-LD support)"""
    out = {"phone": None, "email": None, "website": None}

    # Try tel: links
    tel = re.search(r'href=["\']tel:([^"\']+)["\']', html or "", re.I)
    if tel:
        out["phone"] = normalize_us_phone(tel.group(1))

    # Try JSON-LD structured data
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html or "", re.I | re.S):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        try:
            js = json.loads(raw)
        except Exception:
            continue
        candidates = js if isinstance(js, list) else [js]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            tel2 = item.get("telephone") or item.get("phone")
            url2 = item.get("url") or item.get("sameAs")
            email2 = item.get("email")
            if not out["phone"] and tel2:
                out["phone"] = normalize_us_phone(tel2)
            if not out["email"] and email2:
                out["email"] = clean_email(email2)
            if not out["website"] and url2:
                if isinstance(url2, list):
                    for u in url2:
                        if isinstance(u, str) and "bbb.org" not in u.lower():
                            out["website"] = u
                            break
                elif isinstance(url2, str) and "bbb.org" not in url2.lower():
                    out["website"] = url2

    # Fallback to text extraction
    fb = _extract_from_text(html or "")
    out["phone"] = out["phone"] or fb.get("phone")
    out["email"] = out["email"] or fb.get("email")
    out["website"] = out["website"] or fb.get("website")
    return out

# -----------------------------
# PHASE 2 ENRICH (DATA, NOT URLS)
# -----------------------------

def phase2_enrich(company: str, google_payload: Dict[str, Any], logger=None) -> Dict[str, Any]:
    """
    LOCK PATCH: Produces actual enrichment fields with Serp snippet extraction for YP
    - phase2_bbb_phone/email/website (from Serp snippets + HTML)
    - phase2_yp_phone/email/website (from Serp snippets ONLY - no HTML fetch to avoid bot blocks)
    - phase2_oc_company_number/status (from HTML if URL found)
    - phase2_notes
    """
    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")

    out: Dict[str, Any] = {
        "phase2_bbb_phone": None, "phase2_bbb_email": None, "phase2_bbb_website": None,
        "phase2_yp_phone": None,  "phase2_yp_email": None,  "phase2_yp_website": None,
        "phase2_oc_company_number": None, "phase2_oc_status": None,
        "phase2_notes": ""
    }
    notes = []

    # BBB: Serp snippet -> then fetch BBB HTML (usually allowed)
    bbb = find_bbb_url(company, city, state, logger=logger)
    if bbb.get("organic"):
        sdat = _extract_from_serp_organic(bbb["organic"])
        out["phase2_bbb_phone"] = out["phase2_bbb_phone"] or sdat.get("phone")
        out["phase2_bbb_email"] = out["phase2_bbb_email"] or sdat.get("email")
        out["phase2_bbb_website"] = out["phase2_bbb_website"] or sdat.get("website")

    if bbb.get("url"):
        st, html = _fetch_html(bbb["url"])
        if logger:
            logger.info(f"PHASE2 BBB: fetch status={st} html_len={len(html)}")
        if st == 200 and html:
            hdat = _extract_bbb_from_html(html)
            out["phase2_bbb_phone"] = out["phase2_bbb_phone"] or hdat.get("phone")
            out["phase2_bbb_email"] = out["phase2_bbb_email"] or hdat.get("email")
            out["phase2_bbb_website"] = out["phase2_bbb_website"] or hdat.get("website")
        else:
            notes.append(f"bbb_fetch_http_{st}")
    else:
        notes.append(f"bbb_no_url_{bbb.get('notes')}")

    # YP: NEVER fetch HTML (blocks). Serp snippet only.
    yp = find_yp_url(company, city, state, logger=logger)
    if yp.get("organic"):
        if logger:
            logger.info(f"PHASE2 YP: extracting from Serp snippets (NO HTML FETCH to avoid bot blocks)")
        sdat = _extract_from_serp_organic(yp["organic"])
        out["phase2_yp_phone"] = out["phase2_yp_phone"] or sdat.get("phone")
        out["phase2_yp_email"] = out["phase2_yp_email"] or sdat.get("email")
        out["phase2_yp_website"] = out["phase2_yp_website"] or sdat.get("website")
    if not yp.get("url"):
        notes.append(f"yp_no_url_{yp.get('notes')}")

    # OC: optional (fetch often allowed); extract company number/status
    oc = find_oc_url(company, state, logger=logger)
    if oc.get("url"):
        st, html = _fetch_html(oc["url"])
        if logger:
            logger.info(f"PHASE2 OC: fetch status={st} html_len={len(html)}")
        if st == 200 and html:
            m_num = re.search(r"Company Number</dt>\s*<dd[^>]*>\s*([^<]+)\s*<", html, re.I)
            m_stat = re.search(r"Status</dt>\s*<dd[^>]*>\s*([^<]+)\s*<", html, re.I)
            if m_num:
                out["phase2_oc_company_number"] = (m_num.group(1) or "").strip()
            if m_stat:
                out["phase2_oc_status"] = (m_stat.group(1) or "").strip()
        else:
            notes.append(f"oc_fetch_http_{st}")
    else:
        notes.append(f"oc_no_url_{oc.get('notes')}")

    out["phase2_notes"] = ";".join([n for n in notes if n])[:500]
    return out

# ============================================================
# Phase 3: Sanitizer for Excel control chars (DiffD support)
# ============================================================
import re as _re_sanitize
import json as _json_sanitize
_CTRL = _re_sanitize.compile(r"[\x00-\x1f\x7f-\x9f]")

def safe_json_cell(v):
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode("utf-8", errors="ignore")
        except Exception:
            v = str(v)
    if isinstance(v, (dict, list)):
        s = _json_sanitize.dumps(v, ensure_ascii=False)
    else:
        s = str(v)
    s = _CTRL.sub("", s)
    return s

# ============================================================
# Phase 3: DiffA - extract names from Serp organic
# ============================================================
def _extract_names_from_serp_organic(organic_results):
    names = []
    for r in (organic_results or [])[:8]:
        t = (r.get("title") or "").strip()
        if t:
            names.append(t[:120])
    out = []
    seen = set()
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out[:5]
