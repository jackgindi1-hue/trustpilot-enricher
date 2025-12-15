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

# ============================================================
# SAFE WEBSITE PHONE VALIDATION (US NUMBERS ONLY)
# ============================================================

VALID_US_AREA_CODES = {
    # major + commonly used area codes (not exhaustive but safe)
    "201","202","203","205","206","207","208","209",
    "210","212","213","214","215","216","217","218","219",
    "224","225","228","229","231","234","239",
    "240","248","251","252","253","254","256","260",
    "262","267","269",
    "270","272","274","276","281",
    "301","302","303","304","305","307","308","309",
    "310","312","313","314","315","316","317","318","319",
    "320","321","323","325","330","331","334","336","337",
    "339","346","347","351","352","360","361","364","380",
    "385","386","401","402","404","405","406","407","408","409",
    "410","412","413","414","415","417","419","423","424","425","430","432",
    "434","435","440","442","443","447","458","463","469",
    "470","475","478","479",
    "480","484","501","502","503","504","505","507","508","509",
    "510","512","513","515","516","517","518","520","530","539",
    "540","541","551","559","561","562","563","564","567",
    "570","571","573","574","575","580","585","586",
    "601","602","603","605","606","607","608","609",
    "610","612","614","615","616","617","618","619","620","623","626","628",
    "629","630","631","636","641","646","650","651","657","660","661","662",
    "667","669","678","681","682","701","702","703","704","706","707","708","712",
    "713","714","715","716","717","718","719","720","724","725","727","730","731",
    "732","734","737","740","743","747","754","757","760","762","763","765","769",
    "770","772","773","774","775","779","781","785","786","801","802","803","804",
    "805","806","808","810","812","813","814","815","816","817","818","828","830",
    "831","832","835","843","845","847","848","850","854","856","857","858","859",
    "860","862","863","864","865","870","872","878","901","903","904","906","907",
    "908","909","910","912","913","914","915","916","917","918","919","920","925",
    "928","929","930","931","934","936","937","938","940","941","947","949","951",
    "952","954","956","959","970","971","972","973","975","978","979","980","984",
    "985","989"
}

INVALID_EXCHANGES = {"000", "111", "123", "555", "999"}

def is_valid_us_phone(phone: str) -> bool:
    """
    Validate US phone number by area code and exchange.
    Filters out fake numbers like (555) 555-5555 and invalid area codes.
    """
    digits = re.sub(r"\D", "", phone)
    if len(digits) != 10:
        return False

    area = digits[:3]
    exchange = digits[3:6]

    if area not in VALID_US_AREA_CODES:
        return False

    if exchange in INVALID_EXCHANGES:
        return False

    return True

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
                # Validate before accepting
                if is_valid_us_phone(formatted):
                    logger.info(f"Website: found valid tel: link '{formatted}' on '{website_url}'")
                    return {"phone": formatted, "source": "website", "confidence": "medium", "notes": "Found valid tel: link"}
                else:
                    logger.warning(f"Website: rejected invalid tel: link '{formatted}' on '{website_url}'")

        # Fallback: any phone pattern
        m = PHONE_RE.search(html)
        if m:
            norm = normalize_phone(m.group(0))
            if norm:
                formatted = format_phone(norm)
                # Validate before accepting
                if is_valid_us_phone(formatted):
                    logger.info(f"Website: found valid phone pattern '{formatted}' on '{website_url}'")
                    # Website scrape is less reliable (could be tracking/callrail)
                    return {"phone": formatted, "source": "website", "confidence": "low", "notes": "Found valid phone pattern in HTML"}
                else:
                    logger.warning(f"Website: rejected invalid phone pattern '{formatted}' on '{website_url}'")

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
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Api-Key": api_key,
        }
        payload = {
            "q_organization_domains": domain,
            "page": 1
        }
        r = requests.post(url, headers=headers, json=payload, timeout=15)
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

    # Normalize Google phone if exists
    if google_phone:
        norm = normalize_phone(google_phone)
        if norm:
            google_phone = format_phone(norm)

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
