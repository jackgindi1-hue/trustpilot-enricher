"""
PHASE 2 FINAL PATCH - Drop-in Module

Purpose:
- Stop-on-winner email waterfall (no wasted providers)
- Phase 2 pulls actual DATA from BBB/YP/OC (not just URLs)
- Anti-crash with explicit logs
- Safe fallbacks everywhere

Usage:
- Import functions in pipeline.py
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

def true_email_waterfall(
    domain: Optional[str],
    company: str,
    logger=None,
) -> Dict[str, Any]:
    """
    STOP-ON-WINNER logic:
    - Try Hunter first
    - If Hunter returns ANY email -> stop. (No Snov/Apollo/FullEnrich)
    - If none -> then continue (you can re-enable providers later)
    """
    tried: List[str] = []
    if not domain:
        return {"primary_email": None, "email_source": None, "email_confidence": None, "email_type": None, "tried": []}

    # 1) HUNTER
    tried.append("hunter")
    h = hunter_domain_search(domain, company, logger=logger)
    if h.get("attempted") and (h.get("generic") or h.get("person")):
        # prefer generic for business contact
        if h.get("generic"):
            email = h["generic"][0]
            return {
                "primary_email": email,
                "email_source": "hunter",
                "email_type": "generic",
                "email_confidence": score_email_confidence("hunter", "generic"),
                "tried": tried,
            }
        email = h["person"][0]
        return {
            "primary_email": email,
            "email_source": "hunter",
            "email_type": "person",
            "email_confidence": score_email_confidence("hunter", "person"),
            "tried": tried,
        }

    # If Hunter had no email, stop here for now to keep Phase 2 stable.
    # (You can add Snov/Apollo later behind a feature flag without risking Phase 2.)
    if logger:
        logger.info("EMAIL WATERFALL: Hunter produced no emails; skipping other providers for speed/stability.")
    return {
        "primary_email": None,
        "email_source": None,
        "email_type": None,
        "email_confidence": None,
        "tried": tried,
    }

# -----------------------------
# SERPAPI GOOGLE SEARCH (for BBB/YP/OC URL discovery)
# -----------------------------

def serpapi_google_search(query: str, logger=None) -> Dict[str, Any]:
    key = _env_first("SERP_API_KEY", "SERPAPI_API_KEY", "SERPAPI_KEY")
    if not key:
        return {"attempted": False, "notes": "missing_serp_key", "json": None}

    url = "https://serpapi.com/search.json"
    params = {"engine": "google", "q": query, "api_key": key}
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
    q1 = f'site:bbb.org/us "{company}"' + (f' "{city} {state}"' if (city and state) else "")
    s1 = serpapi_google_search(q1, logger=logger)
    js1 = s1.get("json") or {}
    organic1 = js1.get("organic_results") or []
    candidates1 = [r.get("link") for r in organic1 if _is_bbb_profile_url((r.get("link") or "").strip())]
    url = (candidates1[0] if candidates1 else None)

    if not url:
        q2 = f'site:bbb.org/us "{company}" profile' + (f' "{city} {state}"' if (city and state) else "")
        s2 = serpapi_google_search(q2, logger=logger)
        js2 = s2.get("json") or {}
        organic2 = js2.get("organic_results") or []
        candidates2 = [r.get("link") for r in organic2 if _is_bbb_profile_url((r.get("link") or "").strip())]
        url = (candidates2[0] if candidates2 else None)
        notes = f"{s1.get('notes')} -> retry({s2.get('notes')})"
    else:
        notes = s1.get("notes")

    if logger:
        logger.info(f"PHASE2 BBB URL PICK | found={bool(url)} url={url}")

    return {"attempted": bool(s1.get("attempted")), "notes": notes, "url": url}

def find_yp_url(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    q = f'site:yellowpages.com "{company}"' + (f' "{city} {state}"' if (city and state) else "")
    s = serpapi_google_search(q, logger=logger)
    js = s.get("json") or {}
    organic = js.get("organic_results") or []
    candidates = [r.get("link") for r in organic if _is_yellowpages_profile_url((r.get("link") or "").strip())]
    url = (candidates[0] if candidates else None)
    if logger:
        logger.info(f"PHASE2 YP URL PICK | found={bool(url)} url={url} (rejected category pages)")
    return {"attempted": bool(s.get("attempted")), "notes": s.get("notes"), "url": url}

def find_oc_url(company: str, city: Optional[str], state: Optional[str], logger=None) -> Dict[str, Any]:
    # OC is optional; use it only to discover a likely company page.
    q = f'site:opencorporates.com "{company}"' + (f' "{state}"' if state else "")
    s = serpapi_google_search(q, logger=logger)
    js = s.get("json") or {}
    organic = js.get("organic_results") or []
    # accept direct OC company pages only
    candidates = []
    for r in organic:
        link = (r.get("link") or "").strip()
        if "opencorporates.com/companies/" in link:
            candidates.append(link)
    url = candidates[0] if candidates else None
    if logger:
        logger.info(f"PHASE2 OC URL PICK | found={bool(url)} url={url}")
    return {"attempted": bool(s.get("attempted")), "notes": s.get("notes"), "url": url}

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

def _extract_contact_from_html(html: str) -> Dict[str, Any]:
    emails = [clean_email(e) for e in _EMAIL_RE.findall(html or "")]
    emails = [e for e in emails if e]
    phones = [normalize_us_phone(p) for p in _PHONE_RE.findall(html or "")]
    phones = [p for p in phones if p]

    # naive website extraction: look for "http" + not bbb/yp/oc domain
    sites = re.findall(r"https?://[^\s\"'<>]+", html or "", flags=re.I)
    sites = [s for s in sites if s and all(bad not in s.lower() for bad in ["bbb.org", "yellowpages.com", "opencorporates.com", "google.com"])]
    website = sites[0] if sites else None

    # names: keep very conservative (avoid trash words)
    # pull likely capitalized phrases (this is intentionally minimal)
    raw_names = re.findall(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\b", html or "")
    bad = {"Business", "Profile", "Accredited", "Since", "Reviews", "Better", "Bureau", "Contractors", "Road", "Street", "Avenue"}
    names = []
    for n in raw_names[:300]:
        if len(n) < 4:
            continue
        if any(w in bad for w in n.split()):
            continue
        if n not in names:
            names.append(n)
        if len(names) >= 8:
            break

    return {
        "phone": phones[0] if phones else None,
        "email": emails[0] if emails else None,
        "website": website,
        "names": names,
    }

# -----------------------------
# PHASE 2 ENRICH (DATA, NOT URLS)
# -----------------------------

def phase2_enrich(
    company: str,
    google_payload: Dict[str, Any],
    logger=None,
) -> Dict[str, Any]:
    """
    Produces actual enrichment fields:
    - phase2_bbb_phone/email/website/names_json
    - phase2_yp_phone/email/website/names_json
    - phase2_oc_company_number/status (best effort)
    - phase2_notes
    """
    city = google_payload.get("city")
    state = google_payload.get("state_region") or google_payload.get("state")

    out: Dict[str, Any] = {"phase2_notes": []}

    # BBB
    bbb = find_bbb_url(company, city, state, logger=logger)
    if bbb.get("url"):
        if logger:
            logger.info(f"PHASE2 BBB: link={bbb['url']}")
        st, html = _fetch_html(bbb["url"])
        if logger:
            logger.info(f"PHASE2 BBB: fetch status={st} html_len={len(html)}")
        if st == 200 and html:
            c = _extract_contact_from_html(html)
            out["phase2_bbb_phone"] = c.get("phone")
            out["phase2_bbb_email"] = c.get("email")
            out["phase2_bbb_website"] = c.get("website")
            out["phase2_bbb_names_json"] = json.dumps(c.get("names") or [])
        else:
            out["phase2_notes"].append(f"bbb_fetch_http_{st}")
    else:
        out["phase2_notes"].append(f"bbb_no_url_{bbb.get('notes')}")

    # YP (NOTE: YP blocks often; we only fetch if we have a likely profile URL)
    yp = find_yp_url(company, city, state, logger=logger)
    if yp.get("url"):
        if logger:
            logger.info(f"PHASE2 YP: link={yp['url']}")
        st, html = _fetch_html(yp["url"])
        if logger:
            logger.info(f"PHASE2 YP: fetch status={st} html_len={len(html)}")
        if st == 200 and html:
            c = _extract_contact_from_html(html)
            out["phase2_yp_phone"] = c.get("phone")
            out["phase2_yp_email"] = c.get("email")
            out["phase2_yp_website"] = c.get("website")
            out["phase2_yp_names_json"] = json.dumps(c.get("names") or [])
        else:
            out["phase2_notes"].append(f"yp_fetch_http_{st}")
    else:
        out["phase2_notes"].append(f"yp_no_url_{yp.get('notes')}")

    # OpenCorporates (best-effort: use SerpApi to discover, then parse minimal identifiers)
    oc = find_oc_url(company, city, state, logger=logger)
    if oc.get("url"):
        st, html = _fetch_html(oc["url"])
        if logger:
            logger.info(f"PHASE2 OC: fetch status={st} html_len={len(html)}")
        if st == 200 and html:
            # very light OC extraction
            m_num = re.search(r"Company Number</dt>\s*<dd[^>]*>\s*([^<]+)\s*<", html, re.I)
            m_stat = re.search(r"Status</dt>\s*<dd[^>]*>\s*([^<]+)\s*<", html, re.I)
            if m_num:
                out["phase2_oc_company_number"] = (m_num.group(1) or "").strip()
            if m_stat:
                out["phase2_oc_status"] = (m_stat.group(1) or "").strip()
        else:
            out["phase2_notes"].append(f"oc_fetch_http_{st}")
    else:
        out["phase2_notes"].append(f"oc_no_url_{oc.get('notes')}")

    out["phase2_notes"] = ";".join([n for n in out["phase2_notes"] if n])[:500]
    return out
