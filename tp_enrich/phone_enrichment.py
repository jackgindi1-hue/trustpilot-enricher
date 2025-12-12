# =========================
# PHASE 2: PHONE WATERFALL
# Google -> Yelp -> Website scrape -> Apollo (secondary)
# =========================

import os
import re
import json
import requests
from typing import Dict, Optional
from .logging_utils import setup_logger

logger = setup_logger(__name__)

# -------------------------
# Helpers: phone normalize
# -------------------------
PHONE_RE = re.compile(r"""
    (?:
      (?:\+?1[\s\-\.]?)?            # optional country code
      (?:\(?\d{3}\)?[\s\-\.]?)      # area
      \d{3}[\s\-\.]?\d{4}           # local
    )
""", re.VERBOSE)

TEL_RE = re.compile(r'tel:\s*([+\d][\d\-\(\)\s\.]{7,})', re.IGNORECASE)

def normalize_phone(raw: str) -> Optional[str]:
    """Normalize phone to 10-digit US format."""
    if not raw:
        return None
    s = raw.strip()
    m = PHONE_RE.search(s)
    if not m:
        m = TEL_RE.search(s)
        if not m:
            return None
        s = m.group(1)
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits  # store normalized 10-digit US phone

def format_phone(digits10: str) -> Optional[str]:
    """Format 10-digit phone to (XXX) XXX-XXXX."""
    if not digits10 or len(digits10) != 10:
        return None
    return f"({digits10[0:3]}) {digits10[3:6]}-{digits10[6:10]}"

def same_phone(a: Optional[str], b: Optional[str]) -> bool:
    """Check if two phone numbers are the same after normalization."""
    if not a or not b:
        return False
    return normalize_phone(a) == normalize_phone(b)

# -------------------------
# Yelp fetch (fallback/validate)
# -------------------------
def yelp_search_phone(term: str, city: Optional[str] = None, state: Optional[str] = None, address: Optional[str] = None) -> Dict:
    """
    Returns: {"phone": "...", "source": "yelp", "confidence": "low/medium/high", "notes": "..."}
    """
    api_key = os.getenv("YELP_API_KEY") or os.getenv("YELP_KEY")
    if not api_key:
        logger.debug("Yelp: API key not set")
        return {"phone": None, "source": "yelp", "confidence": "none", "notes": "YELP_API_KEY not set"}

    headers = {"Authorization": f"Bearer {api_key}"}
    url = "https://api.yelp.com/v3/businesses/search"

    # Prefer a real location string; Yelp needs something.
    location_parts = []
    if city: location_parts.append(city)
    if state: location_parts.append(state)
    if not location_parts and address:
        location_parts = [address]
    location = ", ".join(location_parts) if location_parts else "United States"

    params = {
        "term": term,
        "location": location,
        "limit": 5,
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        if r.status_code != 200:
            logger.warning(f"Yelp search failed for '{term}': status={r.status_code}")
            return {"phone": None, "source": "yelp", "confidence": "none", "notes": f"Yelp search status={r.status_code}"}
        data = r.json()
        biz = (data.get("businesses") or [])
        if not biz:
            logger.info(f"Yelp: no matches for '{term}'")
            return {"phone": None, "source": "yelp", "confidence": "none", "notes": "No Yelp matches"}

        # Pick best match by highest rating+review_count heuristic
        biz_sorted = sorted(
            biz,
            key=lambda x: (x.get("review_count") or 0, x.get("rating") or 0),
            reverse=True
        )
        top = biz_sorted[0]
        # Need business details for phone
        biz_id = top.get("id")
        if not biz_id:
            logger.warning(f"Yelp: no business id for '{term}'")
            return {"phone": None, "source": "yelp", "confidence": "none", "notes": "No Yelp biz id"}

        details_url = f"https://api.yelp.com/v3/businesses/{biz_id}"
        d = requests.get(details_url, headers=headers, timeout=12)
        if d.status_code != 200:
            logger.warning(f"Yelp details failed for '{term}': status={d.status_code}")
            return {"phone": None, "source": "yelp", "confidence": "none", "notes": f"Yelp details status={d.status_code}"}
        det = d.json()
        phone = det.get("display_phone") or det.get("phone")
        norm = normalize_phone(phone)
        if not norm:
            logger.info(f"Yelp: no usable phone for '{term}'")
            return {"phone": None, "source": "yelp", "confidence": "none", "notes": "Yelp returned no usable phone"}

        formatted = format_phone(norm)
        logger.info(f"Yelp: found phone '{formatted}' for '{term}'")
        # Yelp-alone phone is "medium" at best. We'll bump to high only if it matches Google.
        return {"phone": formatted, "source": "yelp", "confidence": "medium", "notes": "Yelp phone found"}
    except Exception as e:
        logger.error(f"Yelp error for '{term}': {repr(e)}")
        return {"phone": None, "source": "yelp", "confidence": "none", "notes": f"Yelp error: {repr(e)}"}

# -------------------------
# Website scrape (fallback)
# -------------------------
def scrape_phone_from_website(website_url: str) -> Dict:
    """
    Try to find a phone number from homepage HTML (tel: or visible phone patterns).
    Returns {"phone": "...", "source": "website", "confidence": "...", "notes": "..."}
    """
    if not website_url:
        return {"phone": None, "source": "website", "confidence": "none", "notes": "No website"}
    try:
        # Normalize URL
        if not website_url.startswith("http"):
            website_url = "https://" + website_url

        headers = {"User-Agent": "Mozilla/5.0 (compatible; TP-Enrich/1.0)"}
        r = requests.get(website_url, headers=headers, timeout=12, allow_redirects=True)
        if r.status_code >= 400:
            logger.warning(f"Website scrape failed for '{website_url}': status={r.status_code}")
            return {"phone": None, "source": "website", "confidence": "none", "notes": f"Website status={r.status_code}"}

        html = r.text or ""

        # Prefer tel: links
        tel = TEL_RE.search(html)
        if tel:
            norm = normalize_phone(tel.group(1))
            if norm:
                formatted = format_phone(norm)
                logger.info(f"Website: found tel: link '{formatted}' on '{website_url}'")
                return {"phone": formatted, "source": "website", "confidence": "medium", "notes": "Found tel: link"}

        # Fallback: any phone pattern
        m = PHONE_RE.search(html)
        if m:
            norm = normalize_phone(m.group(0))
            if norm:
                formatted = format_phone(norm)
                logger.info(f"Website: found phone pattern '{formatted}' on '{website_url}'")
                # Website scrape is less reliable (could be tracking/callrail)
                return {"phone": formatted, "source": "website", "confidence": "low", "notes": "Found phone pattern in HTML"}

        logger.info(f"Website: no phone found on '{website_url}'")
        return {"phone": None, "source": "website", "confidence": "none", "notes": "No phone found on site"}
    except Exception as e:
        logger.error(f"Website scrape error for '{website_url}': {repr(e)}")
        return {"phone": None, "source": "website", "confidence": "none", "notes": f"Website scrape error: {repr(e)}"}

# -------------------------
# Apollo org phone (secondary only)
# -------------------------
def apollo_org_phone(domain: Optional[str], company_name: Optional[str]) -> Dict:
    """
    Apollo is a *secondary* phone source. Only trust if domain match is strong.
    Requires APOLLO_API_KEY.
    Returns {"phone": "...", "source":"apollo", "confidence":"low/medium", "notes":"..."}
    """
    api_key = os.getenv("APOLLO_API_KEY") or os.getenv("APOLLO_KEY")
    if not api_key:
        logger.debug("Apollo: API key not set")
        return {"phone": None, "source": "apollo", "confidence": "none", "notes": "APOLLO_API_KEY not set"}

    # If no domain, Apollo is too risky for phone matching
    if not domain:
        logger.debug("Apollo: no domain provided, skipping")
        return {"phone": None, "source": "apollo", "confidence": "none", "notes": "No domain; skip Apollo phone"}

    try:
        url = "https://api.apollo.io/v1/organizations/search"
        payload = {
            "api_key": api_key,
            "q_organization_domains": domain,  # stronger than name-only
            "page": 1
        }
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            logger.warning(f"Apollo search failed for domain '{domain}': status={r.status_code}")
            return {"phone": None, "source": "apollo", "confidence": "none", "notes": f"Apollo status={r.status_code}"}
        data = r.json() or {}
        orgs = data.get("organizations") or []
        if not orgs:
            logger.info(f"Apollo: no org match for domain '{domain}'")
            return {"phone": None, "source": "apollo", "confidence": "none", "notes": "No Apollo org match"}

        org = orgs[0]
        phone = org.get("phone")
        norm = normalize_phone(phone)
        if not norm:
            logger.info(f"Apollo: no usable phone for domain '{domain}'")
            return {"phone": None, "source": "apollo", "confidence": "none", "notes": "Apollo returned no usable phone"}

        formatted = format_phone(norm)
        logger.info(f"Apollo: found phone '{formatted}' for domain '{domain}'")
        # Apollo phone is still secondary; mark low unless corroborated elsewhere
        return {"phone": formatted, "source": "apollo", "confidence": "low", "notes": "Apollo org phone found (domain matched)"}
    except Exception as e:
        logger.error(f"Apollo error for domain '{domain}': {repr(e)}")
        return {"phone": None, "source": "apollo", "confidence": "none", "notes": f"Apollo error: {repr(e)}"}

# -------------------------
# PRIMARY PHONE SELECTION
# -------------------------
def choose_primary_phone(
    google_phone: Optional[str],
    yelp_phone: Optional[str],
    website_phone: Optional[str],
    apollo_phone: Optional[str]
) -> Dict:
    """
    Decision rules:
      1) If Google exists => primary = Google
         - confidence HIGH if Yelp matches
         - else MEDIUM
      2) Else if Yelp exists => primary = Yelp (MEDIUM)
      3) Else if Website exists => primary = Website (LOW)
      4) Else if Apollo exists => primary = Apollo (LOW)
      5) Else none
    """
    if google_phone:
        conf = "high" if (yelp_phone and same_phone(google_phone, yelp_phone)) else "medium"
        notes = "Google primary" + ("; Yelp matched" if conf == "high" else "")
        logger.info(f"Primary phone: Google '{google_phone}' (confidence: {conf})")
        return {
            "primary_phone": google_phone,
            "primary_phone_source": "google",
            "primary_phone_confidence": conf,
            "enrichment_notes_phone": notes,
            "secondary_phone_yelp": yelp_phone,
            "secondary_phone_website": website_phone,
            "secondary_phone_apollo": apollo_phone
        }

    if yelp_phone:
        logger.info(f"Primary phone: Yelp '{yelp_phone}' (Google missing)")
        return {
            "primary_phone": yelp_phone,
            "primary_phone_source": "yelp",
            "primary_phone_confidence": "medium",
            "enrichment_notes_phone": "Yelp primary (Google missing)",
            "secondary_phone_yelp": yelp_phone,
            "secondary_phone_website": website_phone,
            "secondary_phone_apollo": apollo_phone
        }

    if website_phone:
        logger.info(f"Primary phone: Website '{website_phone}' (Google/Yelp missing)")
        return {
            "primary_phone": website_phone,
            "primary_phone_source": "website",
            "primary_phone_confidence": "low",
            "enrichment_notes_phone": "Website scraped phone (Google/Yelp missing)",
            "secondary_phone_yelp": yelp_phone,
            "secondary_phone_website": website_phone,
            "secondary_phone_apollo": apollo_phone
        }

    if apollo_phone:
        logger.info(f"Primary phone: Apollo '{apollo_phone}' (secondary source)")
        return {
            "primary_phone": apollo_phone,
            "primary_phone_source": "apollo",
            "primary_phone_confidence": "low",
            "enrichment_notes_phone": "Apollo phone (secondary source; use caution)",
            "secondary_phone_yelp": yelp_phone,
            "secondary_phone_website": website_phone,
            "secondary_phone_apollo": apollo_phone
        }

    logger.warning("No phone found from any source")
    return {
        "primary_phone": None,
        "primary_phone_source": None,
        "primary_phone_confidence": "none",
        "enrichment_notes_phone": "No phone found",
        "secondary_phone_yelp": None,
        "secondary_phone_website": None,
        "secondary_phone_apollo": None
    }

# -------------------------
# MAIN WATERFALL FUNCTION
# -------------------------
def enrich_business_phone_waterfall(biz_name: str, google_hit: Dict, domain: Optional[str]) -> Dict:
    """
    Phone enrichment waterfall: Google → Yelp → Website → Apollo

    Args:
        biz_name: Business name
        google_hit: Google Places result dict with phone, address, city, state_region, website
        domain: Extracted domain (if available)

    Returns:
        Dict with primary_phone, primary_phone_source, primary_phone_confidence,
        enrichment_notes_phone, secondary phones, and all_phones_json
    """
    logger.info(f"========== PHONE WATERFALL START: '{biz_name}' ==========")

    google_phone = google_hit.get("phone")
    website_url = google_hit.get("website")

    # Yelp uses city/state/address to reduce wrong matches
    yelp = yelp_search_phone(
        term=biz_name,
        city=google_hit.get("city"),
        state=google_hit.get("state_region"),
        address=google_hit.get("address")
    )
    yelp_phone = yelp.get("phone")

    site = scrape_phone_from_website(website_url) if website_url else {"phone": None, "notes": "No website"}
    website_phone = site.get("phone")

    ap = apollo_org_phone(domain=domain, company_name=biz_name)
    apollo_phone = ap.get("phone")

    decision = choose_primary_phone(
        google_phone=google_phone,
        yelp_phone=yelp_phone,
        website_phone=website_phone,
        apollo_phone=apollo_phone
    )

    # Keep a little structured debug info
    decision["all_phones_json"] = json.dumps({
        "google": google_phone,
        "yelp": yelp_phone,
        "website": website_phone,
        "apollo": apollo_phone,
        "yelp_notes": yelp.get("notes"),
        "website_notes": site.get("notes") if isinstance(site, dict) else None,
        "apollo_notes": ap.get("notes"),
    }, ensure_ascii=False)

    logger.info(f"========== PHONE WATERFALL END: primary={decision.get('primary_phone')} source={decision.get('primary_phone_source')} confidence={decision.get('primary_phone_confidence')} ==========")

    return decision
