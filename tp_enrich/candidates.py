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
    
    cand = {
        "source": "google",
        "name": raw_name,
        "name_norm": normalize_company_name(raw_name),
        "state": (
            google_place.get("state_region")
            or google_place.get("state")
            or row.get("business_state_region")
        ),
        "city": google_place.get("city") or row.get("business_city"),
        "address": google_place.get("address") or row.get("business_address"),
        "domain": _norm_domain(
            google_place.get("website")
            or google_place.get("domain")
            or row.get("company_domain")
        ),
        "phone": (
            google_place.get("phone")
            or row.get("primary_phone_display")
            or row.get("business_phone")
        ),
        "website": google_place.get("website"),
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
    
    cand = {
        "source": "yelp",
        "name": raw_name,
        "name_norm": normalize_company_name(raw_name),
        "state": (
            yelp_place.get("state_region")
            or yelp_place.get("state")
            or row.get("business_state_region")
        ),
        "city": yelp_place.get("city") or row.get("business_city"),
        "address": yelp_place.get("address") or row.get("business_address"),
        "domain": _norm_domain(
            yelp_place.get("website")
            or yelp_place.get("domain")
            or row.get("company_domain")
        ),
        "phone": (
            yelp_place.get("phone")
            or row.get("primary_phone_display")
            or row.get("business_phone")
        ),
        "website": yelp_place.get("website"),
        "rating": yelp_place.get("rating"),
        "review_count": yelp_place.get("review_count"),
    }
    return cand
