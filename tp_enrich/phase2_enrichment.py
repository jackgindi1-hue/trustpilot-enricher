# ============================================================
# PHASE 2 — ONE BLOCK (REVIEWED + FEATURE-FLAGGED)
#
# Implements ALL THREE you requested + OpenCorporates:
#   1) SerpAPI fallback for YellowPages + BBB (API-driven discovery)
#   2) Fix Yelp Fusion Business Match (correct endpoint + Bearer auth)
#   3) FullEnrich person-email enrichment USING a "name" we extract AFTER BBB
#   4) OpenCorporates scraping fallback (no paid API required)
#
# Design goals ("think ahead"):
#   - NOTHING slow is enabled by default.
#   - We only do heavy work (SERP, BBB/YP fetch, OC scrape, FullEnrich) when needed.
#   - We log only summary lines unless DEBUG_PHASE2=true.
#   - Hooks are additive: you merge outputs into your existing row dicts.
#
# ---------------------------
# REQUIRED ENV (only if enabled)
# ---------------------------
# Yelp Fusion:
#   YELP_API_KEY=<yelp fusion token>
#   ENABLE_YELP_MATCH=true
#
# SerpAPI (for BBB + YellowPages discovery):
#   SERPAPI_API_KEY=<serpapi key>
#   ENABLE_SERP_FALLBACK=true
#
# FullEnrich (person-based; requires first+last OR LinkedIn):
#   FULLENRICH_API_KEY=<fullenrich key>
#   FULLENRICH_ENRICHMENT_NAME=<any string, e.g. trustpilot_enrichment>
#   ENABLE_FULLENRICH_PERSON=true
#
# OpenCorporates (scrape; no key):
#   ENABLE_OPENCORPORATES=true
#
# OPTIONAL:
#   DEBUG_PHASE2=true
#   SERP_COUNTRY=us
#   SERP_GL=us
#   SERP_HL=en
# ============================================================

import os
import re
import json
import requests
from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import quote_plus

# ---------------------------
# Flags / logging helpers
# ---------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if v in ("1", "true", "yes", "y", "on"): return True
    if v in ("0", "false", "no", "n", "off"): return False
    return default

DEBUG_PHASE2 = _env_bool("DEBUG_PHASE2", False)

ENABLE_YELP_MATCH = _env_bool("ENABLE_YELP_MATCH", False)
ENABLE_SERP_FALLBACK = _env_bool("ENABLE_SERP_FALLBACK", False)
ENABLE_FULLENRICH_PERSON = _env_bool("ENABLE_FULLENRICH_PERSON", False)
ENABLE_OPENCORPORATES = _env_bool("ENABLE_OPENCORPORATES", False)

SERP_COUNTRY = (os.getenv("SERP_COUNTRY") or "us").strip().lower()
SERP_GL = (os.getenv("SERP_GL") or "us").strip().lower()
SERP_HL = (os.getenv("SERP_HL") or "en").strip().lower()

# hard caps to prevent runaway time
SERP_TIMEOUT_SEC = 25
FETCH_TIMEOUT_SEC = 20
BBB_FETCH_MAX_BYTES = 450_000   # safety cap
YP_FETCH_MAX_BYTES = 450_000
OC_FETCH_MAX_BYTES = 450_000

def _log(logger: Any, msg: str):
    if logger:
        logger.info(msg)

def _dbg(logger: Any, msg: str):
    if DEBUG_PHASE2 and logger:
        logger.info(f"[PHASE2 DEBUG] {msg}")

# ---------------------------
# Phone normalize + validate (US/NANP oriented)
# ---------------------------

def normalize_us_phone(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (normalized_e164, notes).
      - 10 digits => +1XXXXXXXXXX
      - 11 digits starting with 1 => +1XXXXXXXXXX
      - reject NANP invalid area/exchange (0/1)
    """
    if not raw:
        return None, "empty"
    digits = re.sub(r"\D+", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None, f"invalid_length({len(digits)})"
    area, exch = digits[:3], digits[3:6]
    if area[0] in ("0", "1") or exch[0] in ("0", "1"):
        return None, "invalid_nanp(area/exchange starts 0/1)"
    return f"+1{digits}", None

# ---------------------------
# Email utils
# ---------------------------

def _clean_email(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", s, re.I):
        return s.lower()
    return None

def email_confidence_label(source: Optional[str], email_type: Optional[str]) -> str:
    if not source:
        return "none"
    if source == "hunter" and email_type == "generic":
        return "high"
    if source == "hunter" and email_type in ("person", "catchall"):
        return "medium"
    if source == "snov":
        return "medium"
    if source == "fullenrich":
        # FullEnrich can be strong when person is correct, but keep conservative:
        return "medium"
    if source == "apollo":
        return "low"
    return "low"

def email_confidence_score(label: str) -> int:
    return {"high": 90, "medium": 70, "low": 40, "none": 0}.get(label or "none", 0)

# ============================================================
# 1) YELP FUSION BUSINESS MATCH (FIXED)
# ============================================================

def yelp_business_match(
    business_name: str,
    address1: Optional[str],
    city: Optional[str],
    state: Optional[str],
    country: str = "US",
    logger: Any = None,
) -> Dict[str, Any]:
    """
    Yelp Fusion Business Match:
      GET https://api.yelp.com/v3/businesses/matches
      Authorization: Bearer <YELP_API_KEY>
    """
    api_key = (os.getenv("YELP_API_KEY") or "").strip()
    if not api_key:
        return {"_attempted": False, "_reason": "missing YELP_API_KEY"}

    if not business_name:
        return {"_attempted": False, "_reason": "missing business_name"}

    # Yelp match works better with address+city+state, but we allow city/state only
    if not (address1 or (city and state)):
        return {"_attempted": False, "_reason": "insufficient location fields for yelp match"}

    url = "https://api.yelp.com/v3/businesses/matches"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"name": business_name, "country": country}
    if address1: params["address1"] = address1
    if city: params["city"] = city
    if state: params["state"] = state

    try:
        r = requests.get(url, headers=headers, params=params, timeout=FETCH_TIMEOUT_SEC)
        if r.status_code != 200:
            return {"_attempted": True, "_reason": f"HTTP {r.status_code}: {r.text[:200]}"}

        js = r.json() or {}
        businesses = js.get("businesses") or []
        if not businesses:
            return {"_attempted": True, "_reason": "no yelp match"}

        b = businesses[0]
        phone_raw = b.get("phone") or b.get("display_phone")
        phone_e164, phone_notes = normalize_us_phone(phone_raw)

        return {
            "_attempted": True,
            "_reason": None,
            "source": "yelp_match",
            "yelp_id": b.get("id"),
            "yelp_url": b.get("url"),
            "yelp_name": b.get("name"),
            "yelp_rating": b.get("rating"),
            "yelp_review_count": b.get("review_count"),
            "yelp_phone_raw": phone_raw,
            "yelp_phone_e164": phone_e164,
            "yelp_phone_notes": phone_notes,
        }

    except Exception as ex:
        return {"_attempted": True, "_reason": f"exception: {repr(ex)}"}

# ============================================================
# 2) SERPAPI DISCOVERY (BBB + YELLOWPAGES)
#    (API-based; we then optionally fetch the found page to extract person name)
# ============================================================

def serpapi_search(query: str, logger: Any = None) -> Dict[str, Any]:
    """
    Uses SerpAPI (Google engine). Returns top organic result link/title/snippet.
    """
    key = (os.getenv("SERPAPI_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "reason": "missing SERPAPI_API_KEY"}

    try:
        r = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "api_key": key,
                "gl": SERP_GL,
                "hl": SERP_HL,
            },
            timeout=SERP_TIMEOUT_SEC,
        )
        if r.status_code != 200:
            return {"ok": False, "reason": f"HTTP {r.status_code}: {r.text[:200]}"}
        js = r.json() or {}
        organic = js.get("organic_results") or []
        top = organic[0] if organic else None
        if not top:
            return {"ok": True, "top": None}

        return {
            "ok": True,
            "top": {
                "title": top.get("title"),
                "link": top.get("link"),
                "snippet": top.get("snippet"),
            },
        }
    except Exception as ex:
        return {"ok": False, "reason": f"exception: {repr(ex)}"}

def discover_bbb_and_yp_links(
    business_name: str,
    city: Optional[str],
    state: Optional[str],
    logger: Any = None,
) -> Dict[str, Any]:
    """
    Uses SerpAPI to find likely BBB + YellowPages pages.
    """
    if not ENABLE_SERP_FALLBACK:
        return {"_attempted": False, "_reason": "ENABLE_SERP_FALLBACK=false"}

    loc = " ".join([p for p in [city, state] if p]).strip()
    base = f"{business_name} {loc}".strip()

    q_bbb = f'site:bbb.org "{business_name}" {loc}'.strip()
    q_yp = f'site:yellowpages.com "{business_name}" {loc}'.strip()

    bbb = serpapi_search(q_bbb, logger=logger)
    yp = serpapi_search(q_yp, logger=logger)

    return {
        "_attempted": True,
        "_reason": None,
        "bbb_query": q_bbb,
        "bbb_top": bbb.get("top") if bbb.get("ok") else None,
        "bbb_err": None if bbb.get("ok") else bbb.get("reason"),
        "yp_query": q_yp,
        "yp_top": yp.get("top") if yp.get("ok") else None,
        "yp_err": None if yp.get("ok") else yp.get("reason"),
    }

# ============================================================
# 3) PAGE FETCH + LIGHTWEIGHT PARSING
#    (Used to extract likely person name from BBB after SERP)
# ============================================================

def fetch_html(url: str, max_bytes: int, logger: Any = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetches HTML with safety cap. Returns (html, err).
    """
    if not url:
        return None, "missing_url"
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT_SEC, headers={
            "User-Agent": "Mozilla/5.0 (compatible; tp_enrich/1.0)"
        })
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        txt = r.text or ""
        if len(txt.encode("utf-8", errors="ignore")) > max_bytes:
            # truncate to cap
            txt = txt[: max_bytes]
        return txt, None
    except Exception as ex:
        return None, f"exception: {repr(ex)}"

def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = re.sub(r"(?is)<.*?>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def extract_person_name_from_bbb(html: str) -> Optional[str]:
    """
    BBB pages vary a lot. We try a few conservative patterns.
    Returns a likely person full name or None.
    """
    if not html:
        return None
    text = _strip_html(html)

    # Common patterns/labels that might exist:
    # "Principal: John Doe", "Owner: Jane Smith", "President: John Doe"
    patterns = [
        r"(?:Principal|Owner|President|CEO|Contact|Manager)\s*:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
        r"(?:Principal|Owner|President|CEO|Contact|Manager)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            name = m.group(1).strip()
            # reject obvious business words
            if any(w in name.lower() for w in ["llc", "inc", "ltd", "company", "corp"]):
                continue
            return name

    return None

def split_first_last(full_name: str) -> Tuple[Optional[str], Optional[str]]:
    if not full_name:
        return None, None
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    if len(parts) < 2:
        return None, None
    # take first and last, ignore middle for safety
    return parts[0], parts[-1]

# ============================================================
# 4) FULLENRICH PERSON ENRICHMENT (AFTER BBB NAME)
# ============================================================

def fullenrich_person_email(
    firstname: str,
    lastname: str,
    domain: Optional[str],
    logger: Any = None,
) -> Dict[str, Any]:
    """
    FullEnrich requires enrichment name + datas with firstname/lastname + (domain or company).
    Endpoint (per their support): /api/v1/contact/enrich/bulk
    """
    if not ENABLE_FULLENRICH_PERSON:
        return {"_attempted": False, "_reason": "ENABLE_FULLENRICH_PERSON=false"}

    api_key = (os.getenv("FULLENRICH_API_KEY") or "").strip()
    enrich_name = (os.getenv("FULLENRICH_ENRICHMENT_NAME") or "").strip()

    if not api_key:
        return {"_attempted": False, "_reason": "missing FULLENRICH_API_KEY"}
    if not enrich_name:
        return {"_attempted": False, "_reason": "missing FULLENRICH_ENRICHMENT_NAME"}
    if not (firstname and lastname):
        return {"_attempted": False, "_reason": "missing firstname/lastname"}

    url = "https://app.fullenrich.com/api/v1/contact/enrich/bulk"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    data_obj: Dict[str, Any] = {
        "firstname": firstname,
        "lastname": lastname,
        "enrich_fields": ["contact.emails"],
    }
    if domain:
        data_obj["domain"] = domain

    payload = {
        "name": enrich_name,
        "datas": [data_obj],
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=FETCH_TIMEOUT_SEC)
        if r.status_code != 200:
            return {"_attempted": True, "_reason": f"HTTP {r.status_code}: {r.text[:200]}"}

        js = r.json() or {}

        # FullEnrich response shapes can vary; try common places for emails:
        emails: List[str] = []

        # 1) direct list
        for e in (js.get("emails") or []):
            ce = _clean_email(e)
            if ce:
                emails.append(ce)

        # 2) datas results (often)
        datas = js.get("datas") or js.get("data") or []
        if isinstance(datas, dict):
            datas = [datas]
        for item in datas:
            # try nested
            for e in (item.get("emails") or []):
                ce = _clean_email(e)
                if ce:
                    emails.append(ce)
            contact = item.get("contact") or {}
            for e in (contact.get("emails") or []):
                # sometimes it's list[dict]
                if isinstance(e, dict):
                    ce = _clean_email(e.get("email"))
                else:
                    ce = _clean_email(e)
                if ce:
                    emails.append(ce)

        # dedupe preserve order
        seen = set()
        emails2 = []
        for e in emails:
            if e not in seen:
                seen.add(e)
                emails2.append(e)

        return {
            "_attempted": True,
            "_reason": None,
            "source": "fullenrich",
            "person_name": f"{firstname} {lastname}".strip(),
            "emails": emails2,
        }

    except Exception as ex:
        return {"_attempted": True, "_reason": f"exception: {repr(ex)}"}

# ============================================================
# 5) OPENCORPORATES SCRAPE (NO PAID API)
# ============================================================

def opencorporates_lookup(company_name: str, logger: Any = None) -> Dict[str, Any]:
    """
    Scrapes OpenCorporates search results (lightweight, no key).
    Goal: get top result link + jurisdiction/company number when visible.
    """
    if not ENABLE_OPENCORPORATES:
        return {"_attempted": False, "_reason": "ENABLE_OPENCORPORATES=false"}

    if not company_name:
        return {"_attempted": False, "_reason": "missing company_name"}

    url = f"https://opencorporates.com/companies?q={quote_plus(company_name)}"
    html, err = fetch_html(url, max_bytes=OC_FETCH_MAX_BYTES, logger=logger)
    if err:
        return {"_attempted": True, "_reason": err}

    # Try to find first company link: /companies/<jurisdiction>/<company_number>
    m = re.search(r'href="(/companies/[^"/]+/[^"/]+)"', html)
    if not m:
        return {"_attempted": True, "_reason": "no result link found"}

    rel = m.group(1)
    full = f"https://opencorporates.com{rel}"

    # Extract jurisdiction + company number
    parts = rel.split("/")
    jurisdiction = parts[2] if len(parts) > 2 else None
    company_number = parts[3] if len(parts) > 3 else None

    return {
        "_attempted": True,
        "_reason": None,
        "oc_search_url": url,
        "oc_top_company_url": full,
        "oc_jurisdiction": jurisdiction,
        "oc_company_number": company_number,
    }

# ============================================================
# HOOK A: LOCAL FALLBACKS (call after Google Places HIT)
# ============================================================

def phase2_local_fallbacks(
    business_name: str,
    google_address: Optional[str],
    city: Optional[str],
    state_region: Optional[str],
    domain: Optional[str],
    logger: Any = None,
) -> Dict[str, Any]:
    """
    Additive outputs to merge into your row dict.
    Runs only what's enabled.
    """
    out: Dict[str, Any] = {}

    # Yelp Match (fixed)
    if ENABLE_YELP_MATCH:
        yelp = yelp_business_match(
            business_name=business_name,
            address1=google_address,
            city=city,
            state=state_region,
            country="US",
            logger=logger,
        )
        out["yelp_match_attempted"] = bool(yelp.get("_attempted"))
        out["yelp_match_reason"] = yelp.get("_reason")
        out["yelp_match_url"] = yelp.get("yelp_url")
        out["yelp_match_rating"] = yelp.get("yelp_rating")
        out["yelp_match_review_count"] = yelp.get("yelp_review_count")
        out["yelp_match_phone_e164"] = yelp.get("yelp_phone_e164")
        out["yelp_match_phone_raw"] = yelp.get("yelp_phone_raw")
        if DEBUG_PHASE2:
            _dbg(logger, f"Yelp match: attempted={yelp.get('_attempted')} reason={yelp.get('_reason')} url={yelp.get('yelp_url')}")

    # Serp discovery (BBB + YellowPages)
    bbb_name: Optional[str] = None
    if ENABLE_SERP_FALLBACK:
        disc = discover_bbb_and_yp_links(business_name, city, state_region, logger=logger)
        out["serp_attempted"] = bool(disc.get("_attempted"))
        out["bbb_top_link"] = (disc.get("bbb_top") or {}).get("link")
        out["bbb_top_title"] = (disc.get("bbb_top") or {}).get("title")
        out["yp_top_link"] = (disc.get("yp_top") or {}).get("link")
        out["yp_top_title"] = (disc.get("yp_top") or {}).get("title")
        out["serp_errors_json"] = json.dumps({"bbb_err": disc.get("bbb_err"), "yp_err": disc.get("yp_err")}, ensure_ascii=False)

        # BBB page fetch + extract person name (ONLY if we might use FullEnrich)
        if ENABLE_FULLENRICH_PERSON and out.get("bbb_top_link"):
            bbb_html, bbb_err = fetch_html(out["bbb_top_link"], max_bytes=BBB_FETCH_MAX_BYTES, logger=logger)
            out["bbb_fetch_err"] = bbb_err
            if bbb_html and not bbb_err:
                bbb_name = extract_person_name_from_bbb(bbb_html)
                out["bbb_contact_name"] = bbb_name
            if DEBUG_PHASE2:
                _dbg(logger, f"BBB fetch err={bbb_err} extracted_name={bbb_name}")

        # (Optional) You can later parse YP for phones if you want. For now we keep links only.
        if DEBUG_PHASE2:
            _dbg(logger, f"SERP BBB={out.get('bbb_top_link')} | YP={out.get('yp_top_link')}")

    # OpenCorporates lookup
    oc = opencorporates_lookup(business_name, logger=logger)
    out["oc_attempted"] = bool(oc.get("_attempted"))
    out["oc_reason"] = oc.get("_reason")
    out["oc_top_company_url"] = oc.get("oc_top_company_url")
    out["oc_jurisdiction"] = oc.get("oc_jurisdiction")
    out["oc_company_number"] = oc.get("oc_company_number")

    # FullEnrich person email (ONLY if we got a person name from BBB and we have domain)
    if ENABLE_FULLENRICH_PERSON and bbb_name:
        fn, ln = split_first_last(bbb_name)
        fe = fullenrich_person_email(firstname=fn or "", lastname=ln or "", domain=domain, logger=logger)
        out["fullenrich_attempted"] = bool(fe.get("_attempted"))
        out["fullenrich_reason"] = fe.get("_reason")
        out["fullenrich_person_name"] = fe.get("person_name")
        out["fullenrich_emails_json"] = json.dumps(fe.get("emails") or [], ensure_ascii=False)

        # If your email waterfall found nothing and FullEnrich has emails, you can promote later.
        if DEBUG_PHASE2:
            _dbg(logger, f"FullEnrich: attempted={fe.get('_attempted')} reason={fe.get('_reason')} emails={(fe.get('emails') or [])}")

    return out

# ============================================================
# HOOK B: normalize primary phone AFTER your existing phone waterfall
# ============================================================

def phase2_normalize_primary_phone(primary_phone: Optional[str]) -> Dict[str, Any]:
    e164, notes = normalize_us_phone(primary_phone)
    return {
        "primary_phone_e164": e164,
        "primary_phone_validation_notes": notes,
    }

# ============================================================
# HOOK C: email scoring AFTER your existing email waterfall
# ============================================================

def phase2_email_scoring(primary_email_source: Optional[str], primary_email_type: Optional[str]) -> Dict[str, Any]:
    label = email_confidence_label(primary_email_source, primary_email_type)
    return {
        "primary_email_confidence_label": label,
        "primary_email_confidence_score": email_confidence_score(label),
    }

# ============================================================
# HOOK D (OPTIONAL): promote FullEnrich emails ONLY when your waterfall is empty
# ============================================================

def phase2_promote_fullenrich_if_empty(
    primary_email: Optional[str],
    fullenrich_emails_json: Optional[str],
) -> Dict[str, Any]:
    """
    If pipeline has no primary_email and FullEnrich produced emails, choose first.
    Keeps your "Hunter -> Snov -> Apollo -> FullEnrich" intent WITHOUT slowing normal wins.
    """
    if primary_email:
        return {}

    emails = []
    try:
        if fullenrich_emails_json:
            emails = json.loads(fullenrich_emails_json)
    except Exception:
        emails = []

    for e in emails or []:
        ce = _clean_email(e)
        if ce:
            return {
                "primary_email": ce,
                "primary_email_type": "person",
                "primary_email_source": "fullenrich",
            }
    return {}
