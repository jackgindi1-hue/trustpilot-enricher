"""
Minimal local enrichment - Google Places ONLY
"""

import os
import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger(__name__)

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")


def _google_places_search(name: str) -> Optional[str]:
    """
    Simple Places Text Search by name.
    Returns place_id or None.
    """
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("Google Places API key not set.")
        return None

    if not name or not name.strip():
        return None

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": name,
        "key": GOOGLE_PLACES_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.exception("Google Places textsearch error for '%s': %s", name, e)
        return None

    results = data.get("results", [])
    if not results:
        logger.info("Google Places: no results for '%s'", name)
        return None

    place_id = results[0].get("place_id")
    logger.info("Google Places: found place_id '%s' for '%s'", place_id, name)
    return place_id


def _google_places_details(place_id: str) -> Dict[str, Any]:
    """
    Fetch basic details (phone, address, website) from Place Details.
    """
    if not GOOGLE_PLACES_API_KEY or not place_id:
        return {}

    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "formatted_phone_number,formatted_address,website,address_components",
        "key": GOOGLE_PLACES_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.exception("Google Places details error for '%s': %s", place_id, e)
        return {}

    result = data.get("result", {}) or {}
    return result


def enrich_local_business(name: str, region: Optional[str] = None) -> Dict[str, Any]:
    """
    MINIMAL local enrichment: Google Places ONLY.
    Returns phone, phone_source, address, city, state_region, postal_code, country, website.
    """
    base = {
        "phone": None,
        "phone_source": None,
        "address": None,
        "city": None,
        "state_region": None,
        "postal_code": None,
        "country": None,
        "website": None,
    }

    if not name or not name.strip():
        logger.warning("Local enrichment: empty name, skipping.")
        return base

    logger.info("Local enrichment: searching Google Places for '%s'", name)

    place_id = _google_places_search(name)
    if not place_id:
        logger.info("Local enrichment: no Google Places match for '%s'", name)
        return base

    details = _google_places_details(place_id)
    if not details:
        logger.info("Local enrichment: no details for place_id=%s", place_id)
        return base

    phone = details.get("formatted_phone_number")
    address = details.get("formatted_address")
    website = details.get("website")

    city = None
    state_region = None
    postal_code = None
    country = None

    components = details.get("address_components") or []
    for comp in components:
        types = comp.get("types", [])
        short_name = comp.get("short_name")
        long_name = comp.get("long_name")

        if "locality" in types and not city:
            city = long_name or short_name
        if "administrative_area_level_1" in types and not state_region:
            state_region = short_name or long_name
        if "postal_code" in types and not postal_code:
            postal_code = long_name or short_name
        if "country" in types and not country:
            country = short_name or long_name

    logger.info(
        "Google Places result for '%s': phone=%s, website=%s, address=%s",
        name, phone, website, address
    )

    return {
        "phone": phone,
        "phone_source": "google_places" if phone else None,
        "address": address,
        "city": city,
        "state_region": state_region,
        "postal_code": postal_code,
        "country": country,
        "website": website,
    }
