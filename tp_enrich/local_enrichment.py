"""
Local business enrichment - Google Places + Yelp
"""
import os
from typing import Dict, Optional
from .logging_utils import setup_logger
logger = setup_logger(__name__)

# Read env vars ON IMPORT
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
YELP_API_KEY = os.getenv("YELP_API_KEY")

logger.info(
    "Env check (local_enrichment): GOOGLE_PLACES_API_KEY present=%s (len=%s), "
    "YELP_API_KEY present=%s (len=%s)",
    bool(GOOGLE_PLACES_API_KEY),
    len(GOOGLE_PLACES_API_KEY) if GOOGLE_PLACES_API_KEY else 0,
    bool(YELP_API_KEY),
    len(YELP_API_KEY) if YELP_API_KEY else 0,
)


def enrich_local_business(name: str, region: Optional[str] = None) -> Dict:
    """
    Perform local enrichment (Google Places + Yelp).
    Returns a unified dict with phone/address/website information.
    """
    result: Dict = {
        "phone": None,
        "phone_source": None,
        "address": None,
        "city": None,
        "state_region": None,
        "postal_code": None,
        "country": None,
        "website": None,
        "provider": None,
    }

    # Re-read env inside function just in case this module was imported
    # before Railway injected env vars (defensive).
    google_key = GOOGLE_PLACES_API_KEY or os.getenv("GOOGLE_PLACES_API_KEY")
    yelp_key = YELP_API_KEY or os.getenv("YELP_API_KEY")

    logger.info(
        "Local enrichment start for '%s' (region=%s). google_key_present=%s, yelp_key_present=%s",
        name,
        region,
        bool(google_key),
        bool(yelp_key),
    )

    # 1) Try Google Places first
    gp_data = None
    if google_key and name:
        try:
            # REST call to Places API: Find Place From Text
            import requests
            find_url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
            params = {
                "input": name if not region else f"{name} {region}",
                "inputtype": "textquery",
                "fields": "place_id",
                "key": google_key,
            }
            resp = requests.get(find_url, params=params, timeout=10)
            logger.info("Google Places findplace status=%s for '%s'", resp.status_code, name)

            data = resp.json()
            candidates = data.get("candidates") or []
            if candidates:
                place_id = candidates[0].get("place_id")
                if place_id:
                    details_url = "https://maps.googleapis.com/maps/api/place/details/json"
                    details_params = {
                        "place_id": place_id,
                        "fields": "name,formatted_phone_number,formatted_address,website,address_components",  # PHASE 4.6: Added 'name'
                        "key": google_key,
                    }
                    d_resp = requests.get(details_url, params=details_params, timeout=10)
                    logger.info(
                        "Google Places details status=%s for '%s'", d_resp.status_code, name
                    )

                    d_data = d_resp.json()
                    result_data = d_data.get("result") or {}

                    name_from_google = result_data.get("name")  # PHASE 4.6: Extract name
                    phone = result_data.get("formatted_phone_number")
                    address = result_data.get("formatted_address")
                    website = result_data.get("website")

                    city = None
                    state_region = None
                    postal_code = None
                    country = None
                    for comp in result_data.get("address_components") or []:
                        types = comp.get("types") or []
                        if "locality" in types:
                            city = comp.get("long_name")
                        if "administrative_area_level_1" in types:
                            state_region = comp.get("short_name")
                        if "postal_code" in types:
                            postal_code = comp.get("long_name")
                        if "country" in types:
                            country = comp.get("short_name")

                    if phone or address or website:
                        gp_data = {
                            "name": name_from_google,  # PHASE 4.6: Include name for canonical matching
                            "phone": phone,
                            "address": address,
                            "city": city,
                            "state_region": state_region,
                            "postal_code": postal_code,
                            "country": country,
                            "website": website,
                        }
        except Exception as e:
            logger.error("Google Places lookup error for '%s': %s", name, e, exc_info=True)

    if gp_data:
        logger.info("Google Places HIT for '%s': %s", name, gp_data)
        result.update(
            {
                "name": gp_data.get("name"),  # PHASE 4.6: Include name
                "phone": gp_data.get("phone"),
                "phone_source": "google_places",
                "address": gp_data.get("address"),
                "city": gp_data.get("city"),
                "state_region": gp_data.get("state_region"),
                "postal_code": gp_data.get("postal_code"),
                "country": gp_data.get("country"),
                "website": gp_data.get("website"),
                "provider": "google_places",
            }
        )
        return result

    # 2) Fallback to Yelp if Places failed or returned nothing
    yelp_data = None
    if yelp_key and name:
        try:
            import requests
            headers = {"Authorization": f"Bearer {yelp_key}"}
            search_url = "https://api.yelp.com/v3/businesses/search"
            params = {
                "term": name,
                "location": region or "United States",
                "limit": 1,
            }
            y_resp = requests.get(search_url, headers=headers, params=params, timeout=10)
            logger.info("Yelp search status=%s for '%s'", y_resp.status_code, name)

            y_data = y_resp.json()
            businesses = y_data.get("businesses") or []
            if businesses:
                b = businesses[0]
                name_from_yelp = b.get("name")  # PHASE 4.6: Extract name
                phone = b.get("phone") or b.get("display_phone")
                address_parts = b.get("location") or {}
                address = ", ".join(
                    [part for part in address_parts.get("display_address", []) if part]
                )
                city = address_parts.get("city")
                state_region = address_parts.get("state")
                postal_code = address_parts.get("zip_code")
                country = address_parts.get("country")
                website = b.get("url")

                yelp_data = {
                    "name": name_from_yelp,  # PHASE 4.6: Include name for canonical matching
                    "phone": phone,
                    "address": address,
                    "city": city,
                    "state_region": state_region,
                    "postal_code": postal_code,
                    "country": country,
                    "website": website,
                }
        except Exception as e:
            logger.error("Yelp lookup error for '%s': %s", name, e, exc_info=True)

    if yelp_data:
        logger.info("Yelp HIT for '%s': %s", name, yelp_data)
        result.update(
            {
                "name": yelp_data.get("name"),  # PHASE 4.6: Include name
                "phone": yelp_data.get("phone"),
                "phone_source": "yelp",
                "address": yelp_data.get("address"),
                "city": yelp_data.get("city"),
                "state_region": yelp_data.get("state_region"),
                "postal_code": yelp_data.get("postal_code"),
                "country": yelp_data.get("country"),
                "website": yelp_data.get("website"),
                "provider": "yelp",
            }
        )
        return result

    logger.warning("No local enrichment data found for '%s'", name)
    return result
