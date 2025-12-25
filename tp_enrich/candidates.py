"""
Candidate builders for Google Places and Yelp
Ensures all candidates have proper name fields (fixes None name bug)
"""
from typing import Dict, Any, Optional
from tp_enrich.normalize import normalize_company_name

def _norm_domain(url_or_domain: Optional[str]) -> str:
    """Normalize domain from URL or domain string"""
    if not url_or_domain:
        return ""
    d = url_or_domain.strip().lower()
    d = d.replace("http://", "").replace("https://", "").replace("www.", "")
    d = d.split("/")[0]
    return d


def _norm_phone(phone: Optional[str]) -> str:
    """
    PHASE 4.6.5: Normalize phone number to digits only.
    Removes formatting to ensure canonical matching works.
    """
    if not phone:
        return ""
    import re
    digits = re.sub(r'\D', '', phone)
    # Strip leading 1 for US numbers (11 digits -> 10 digits)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits

def build_google_candidate(row: dict, google_place: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Build Google Places candidate with guaranteed name field.

    Args:
        row: Business row dict
        google_place: Google Places API result

    Returns:
        Candidate dict or None if no valid data
    """
    if not google_place:
        return None

    # IMPORTANT: ensure name is present
    # Google Places detail commonly returns .get("name") — but if your upstream uses another key,
    # we fall back to row business name.
    raw_name = (
        google_place.get("name")
        or google_place.get("business_name")
        or google_place.get("title")
        or row.get("business_name")
        or row.get("company_search_name")
        or ""
    )
    if not raw_name:
        return None  # Can't match without a name

    # PHASE 4.6.5: Extract website and phone from ALL possible Google keys
    # Google Places Details returns: formatted_phone_number, website
    # Google Scout Mode returns: phone, website
    raw_website = (
        google_place.get("website")
        or google_place.get("domain")
        or google_place.get("url")
        or ""
    )

    raw_phone = (
        google_place.get("formatted_phone_number")  # Google Places Details key
        or google_place.get("phone")                 # Google Scout Mode key
        or google_place.get("display_phone")
        or ""
    )

    # Normalize fields for canonical matching
    normalized_domain = _norm_domain(raw_website)
    normalized_phone = _norm_phone(raw_phone)

    cand = {
        "source": "google",
        "name": raw_name,
        "name_norm": normalize_company_name(raw_name),
        "normalized_name": raw_name,  # PHASE 4.6.5: Matcher expects this

        # PHASE 4.6.5: Normalized fields for matcher
        "domain": normalized_domain,
        "phone": normalized_phone,

        # Location fields
        "state": (
            google_place.get("state_region")
            or google_place.get("state")
            or row.get("business_state_region")
        ),
        "city": google_place.get("city") or row.get("business_city"),
        "address": (
            google_place.get("address")
            or google_place.get("formatted_address")
            or row.get("business_address")
        ),

        # Keep originals for audit/debug
        "website": raw_website,
        "formatted_phone_number": raw_phone,
        "lat": google_place.get("lat"),
        "lng": google_place.get("lng") or google_place.get("lon"),
        "place_id": google_place.get("place_id"),
    }
    return cand

def build_yelp_candidate(row: dict, yelp_place: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Build Yelp candidate with guaranteed name field.

    Args:
        row: Business row dict
        yelp_place: Yelp API result

    Returns:
        Candidate dict or None if no valid data
    """
    if not yelp_place:
        return None

    raw_name = (
        yelp_place.get("name")
        or yelp_place.get("business_name")
        or row.get("business_name")
        or row.get("company_search_name")
        or ""
    )
    if not raw_name:
        return None  # Can't match without a name

    # PHASE 4.6.5: Extract website and phone from Yelp keys
    raw_website = (
        yelp_place.get("website")
        or yelp_place.get("url")
        or yelp_place.get("domain")
        or ""
    )

    raw_phone = (
        yelp_place.get("phone")
        or yelp_place.get("display_phone")
        or ""
    )

    # Normalize fields for canonical matching
    normalized_domain = _norm_domain(raw_website)
    normalized_phone = _norm_phone(raw_phone)

    cand = {
        "source": "yelp",
        "name": raw_name,
        "name_norm": normalize_company_name(raw_name),
        "normalized_name": raw_name,  # PHASE 4.6.5: Matcher expects this

        # PHASE 4.6.5: Normalized fields for matcher
        "domain": normalized_domain,
        "phone": normalized_phone,

        # Location fields
        "state": (
            yelp_place.get("state_region")
            or yelp_place.get("state")
            or row.get("business_state_region")
        ),
        "city": yelp_place.get("city") or row.get("business_city"),
        "address": yelp_place.get("address") or row.get("business_address"),

        # Keep originals for audit/debug
        "website": raw_website,
        "formatted_phone_number": raw_phone,
        "rating": yelp_place.get("rating"),
        "review_count": yelp_place.get("review_count"),
    }
    return cand


def apply_candidate_anchors_to_row(row: dict, candidate: Optional[Dict[str, Any]], logger=None, source="candidate") -> None:
    """
    PHASE 4.6.5: Apply candidate's normalized anchors to row (non-destructive).
    
    This ensures canonical matching can "see" the anchors even if they came from
    different field names (e.g., formatted_phone_number -> phone).
    
    Args:
        row: Business row dict
        candidate: Normalized candidate dict from build_google_candidate/build_yelp_candidate
        logger: Optional logger
        source: Source name for logging (e.g., "google", "yelp")
    """
    if not candidate:
        return
    
    # Extract normalized anchors from candidate
    domain = (candidate.get("domain") or "").strip()
    phone = (candidate.get("phone") or "").strip()
    
    # Apply to row (only if missing)
    if domain and not (row.get("company_domain") or "").strip():
        row["company_domain"] = domain
        if logger:
            logger.info(f"   -> ANCHOR: Set company_domain from {source} candidate: {domain}")
    
    if phone and not (row.get("primary_phone") or "").strip():
        row["primary_phone"] = phone
        if logger:
            logger.info(f"   -> ANCHOR: Set primary_phone from {source} candidate: {phone}")
