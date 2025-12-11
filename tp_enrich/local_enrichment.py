import os
import requests
from typing import Dict, Optional
from .logging_utils import setup_logger

logger = setup_logger(__name__)

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
YELP_API_KEY = os.getenv("YELP_API_KEY")


def _google_places_search(name: str) -> Optional[Dict]:
    """
    Call Google Places Text Search + Details to get phone/address/website.
    Returns a dict with normalized fields or None if nothing found.
    """
    if not GOOGLE_PLACES_API_KEY or not name:
        logger.warning(
            "Google Places disabled or no name. KEY set=%s, name='%s'",
            bool(GOOGLE_PLACES_API_KEY),
            name,
        )
        return None

    try:
        logger.info("Google Places: text search for '%s'", name)
        search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query": name,
            "key": GOOGLE_PLACES_API_KEY,
        }
        search_resp = requests.get(search_url, params=params, timeout=10)
        logger.info("Google Places: search HTTP %s", search_resp.status_code)

        if search_resp.status_code != 200:
            logger.warning(
                "Google Places search non-200 for '%s': %s %s",
                name,
                search_resp.status_code,
                search_resp.text[:200],
            )
            return None

        search_data = search_resp.json()
        results = search_data.get("results") or []
        if not results:
            logger.warning("Google Places: no results for '%s'", name)
            return None

        # Take top candidate
        top = results[0]
        place_id = top.get("place_id")
        if not place_id:
            logger.warning("Google Places: top result missing place_id for '%s'", name)
            return None

        # Call Details for phone/address/website
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {
            "place_id": place_id,
            "key": GOOGLE_PLACES_API_KEY,
            "fields": "formatted_phone_number,international_phone_number,formatted_address,address_components,website",
        }
        details_resp = requests.get(details_url, params=details_params, timeout=10)
        logger.info("Google Places: details HTTP %s", details_resp.status_code)

        if details_resp.status_code != 200:
            logger.warning(
                "Google Places details non-200 for '%s': %s %s",
                name,
                details_resp.status_code,
                details_resp.text[:200],
            )
            return None

        details = details_resp.json().get("result") or {}

        # Phone
        phone = (
            details.get("international_phone_number")
            or details.get("formatted_phone_number")
        )

        # Address & components
        formatted_address = details.get("formatted_address")
        address_components = details.get("address_components") or []

        city = state_region = postal_code = country = None
        for comp in address_components:
            types = comp.get("types") or []
            long_name = comp.get("long_name")
            short_name = comp.get("short_name")

            if "locality" in types:
                city = long_name
            elif "administrative_area_level_1" in types:
                state_region = short_name or long_name
            elif "postal_code" in types:
                postal_code = long_name
            elif "country" in types:
                country = short_name or long_name

        website = details.get("website")

        result = {
            "phone": phone,
            "phone_source": "google_places" if phone else None,
            "address": formatted_address,
            "city": city,
            "state_region": state_region,
            "postal_code": postal_code,
            "country": country,
            "website": website,
        }

        logger.info(
            "Google Places result for '%s': phone=%s, address=%s, website=%s",
            name,
            phone,
            formatted_address,
            website,
        )

        return result

    except Exception as e:
        logger.exception("Google Places error for '%s': %s", name, e)
        return None


def _yelp_search(name: str) -> Optional[Dict]:
    """
    Call Yelp Business Search to get phone/address/website.
    Returns a dict with normalized fields or None if nothing found.
    """
    if not YELP_API_KEY or not name:
        logger.warning(
            "Yelp disabled or no name. KEY set=%s, name='%s'",
            bool(YELP_API_KEY),
            name,
        )
        return None

    try:
        logger.info("Yelp: business search for '%s'", name)
        url = "https://api.yelp.com/v3/businesses/search"
        # No exact location info, so we use a broad location hint.
        params = {
            "term": name,
            "location": "United States",
            "limit": 1,
        }
        headers = {"Authorization": f"Bearer {YELP_API_KEY}"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        logger.info("Yelp: search HTTP %s", resp.status_code)

        if resp.status_code != 200:
            logger.warning(
                "Yelp search non-200 for '%s': %s %s",
                name,
                resp.status_code,
                resp.text[:200],
            )
            return None

        data = resp.json()
        businesses = data.get("businesses") or []
        if not businesses:
            logger.warning("Yelp: no results for '%s'", name)
            return None

        b = businesses[0]
        phone = b.get("display_phone") or b.get("phone")
        location = b.get("location") or {}

        # Build address
        address_parts = location.get("display_address") or []
        address = ", ".join([p for p in address_parts if p])

        city = location.get("city")
        state_region = location.get("state")
        postal_code = location.get("zip_code")
        country = location.get("country")

        website = b.get("url")

        result = {
            "phone": phone,
            "phone_source": "yelp" if phone else None,
            "address": address,
            "city": city,
            "state_region": state_region,
            "postal_code": postal_code,
            "country": country,
            "website": website,
        }

        logger.info(
            "Yelp result for '%s': phone=%s, address=%s, website=%s",
            name,
            phone,
            address,
            website,
        )

        return result

    except Exception as e:
        logger.exception("Yelp error for '%s': %s", name, e)
        return None


def enrich_local_business(name: str, region: Optional[str] = None) -> Dict:
    """
    Perform local enrichment with GOOGLE PLACES FIRST, then YELP as fallback.

    Returns a unified dict with phone/address/website and phone_source
    set according to which provider returned data.
    """
    logger.info("Local enrichment start for '%s'", name)

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

    if not name:
        logger.warning("Local enrichment: empty name, skipping.")
        return base

    # 1) Google Places first
    gp = _google_places_search(name)
    if gp and any(gp.get(k) for k in ("phone", "address", "website")):
        logger.info("Local enrichment: using Google Places for '%s'", name)
        base.update(gp)
        return base

    # 2) Yelp fallback
    yelp = _yelp_search(name)
    if yelp and any(yelp.get(k) for k in ("phone", "address", "website")):
        logger.info("Local enrichment: using Yelp for '%s'", name)
        base.update(yelp)
        return base

    logger.warning("No local enrichment data found for '%s'", name)
    return base
