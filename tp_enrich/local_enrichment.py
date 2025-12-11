"""
Local business enrichment logic - Sections D, E, F
Google Maps, Yelp Fusion, YellowPages/BBB
"""

import os
import requests
from typing import Dict, Optional, Literal
from Levenshtein import ratio
from .logging_utils import setup_logger
from .domain_enrichment import extract_domain

logger = setup_logger(__name__)

MatchConfidence = Literal["none", "low", "medium", "high"]

# Read environment variables for local enrichment
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
YELP_API_KEY = os.getenv("YELP_API_KEY")


def calculate_name_similarity(name1: str, name2: str) -> float:
    """
    Calculate name similarity using Levenshtein ratio

    Args:
        name1: First name
        name2: Second name

    Returns:
        Similarity ratio (0.0 to 1.0)
    """
    if not name1 or not name2:
        return 0.0

    return ratio(name1.lower(), name2.lower())


def enrich_from_google_maps(company_name: str, context: Dict) -> Dict:
    """
    Section D: Google Maps / Places enrichment (REST API)

    TOP PRIORITY SMB PHONE SOURCE

    Args:
        company_name: Company search name
        context: Enrichment context with location

    Returns:
        Dict with Google Maps data
    """
    result = {
        'maps_phone_main': None,
        'maps_phone_international': None,
        'maps_website': None,
        'maps_address': None,
        'maps_types': None,
        'maps_match_confidence': 'none'
    }

    api_key = os.getenv('GOOGLE_PLACES_API_KEY')

    if not api_key:
        logger.debug("Google Places API key not provided, skipping")
        return result

    try:
        # Build query
        city = context.get('city', '')
        state = context.get('state', '')

        if city or state:
            query = f"{company_name} {city} {state}".strip()
        else:
            query = company_name

        logger.debug(f"Querying Google Maps for: {query}")

        # Text Search (New REST API)
        search_url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.internationalPhoneNumber,places.nationalPhoneNumber,places.websiteUri,places.types"
        }
        payload = {
            "textQuery": query,
            "languageCode": "en"
        }

        response = requests.post(search_url, json=payload, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()
            places = data.get('places', [])

            if places:
                # Choose best candidate by name similarity
                best_place = None
                best_similarity = 0.0

                for place in places[:5]:  # Check top 5
                    place_name = place.get('displayName', {}).get('text', '')
                    similarity = calculate_name_similarity(place_name, company_name)

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_place = place

                if best_place and best_similarity >= 0.6:
                    result['maps_phone_main'] = best_place.get('nationalPhoneNumber')
                    result['maps_phone_international'] = best_place.get('internationalPhoneNumber')
                    result['maps_website'] = best_place.get('websiteUri')
                    result['maps_address'] = best_place.get('formattedAddress')
                    result['maps_types'] = ','.join(best_place.get('types', []))

                    # Set confidence based on similarity
                    if best_similarity >= 0.9:
                        result['maps_match_confidence'] = 'high'
                    elif best_similarity >= 0.75:
                        result['maps_match_confidence'] = 'medium'
                    else:
                        result['maps_match_confidence'] = 'low'

                    logger.debug(f"Google Maps found match for {company_name} (similarity: {best_similarity:.2f})")
            else:
                logger.debug(f"Google Maps: No results for {query}")
        else:
            logger.warning(f"Google Maps API error: {response.status_code} - {response.text}")

    except Exception as e:
        logger.warning(f"Google Maps API error: {e}")

    return result


def enrich_from_yelp(company_name: str, context: Dict) -> Dict:
    """
    Section E: Yelp Fusion enrichment

    Args:
        company_name: Company search name
        context: Enrichment context with location

    Returns:
        Dict with Yelp data
    """
    result = {
        'yelp_phone_display': None,
        'yelp_phone_e164': None,
        'yelp_address': None,
        'yelp_url': None,
        'yelp_categories': None,
        'yelp_website': None,
        'yelp_match_confidence': 'none'
    }

    api_key = os.getenv('YELP_API_KEY')

    if not api_key:
        logger.debug("Yelp API key not provided, skipping")
        return result

    try:
        url = "https://api.yelp.com/v3/businesses/search"

        headers = {
            "Authorization": f"Bearer {api_key}"
        }

        params = {
            "term": company_name,
            "limit": 5
        }

        # Add location if available
        city = context.get('city', '')
        state = context.get('state', '')

        if city or state:
            params["location"] = f"{city}, {state}".strip(', ')

        logger.debug(f"Querying Yelp for: {company_name}")

        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            businesses = data.get('businesses', [])

            if businesses:
                # Choose best candidate by name similarity
                best_business = None
                best_similarity = 0.0

                for business in businesses:
                    business_name = business.get('name', '')
                    similarity = calculate_name_similarity(business_name, company_name)

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_business = business

                if best_business and best_similarity >= 0.6:
                    # Extract data
                    result['yelp_phone_display'] = best_business.get('display_phone')
                    result['yelp_phone_e164'] = best_business.get('phone')
                    result['yelp_url'] = best_business.get('url')

                    # Address
                    location = best_business.get('location', {})
                    if location.get('display_address'):
                        result['yelp_address'] = ', '.join(location['display_address'])

                    # Categories
                    categories = best_business.get('categories', [])
                    if categories:
                        result['yelp_categories'] = ', '.join([c.get('title', '') for c in categories])

                    # Set confidence
                    if best_similarity >= 0.9:
                        result['yelp_match_confidence'] = 'high'
                    elif best_similarity >= 0.75:
                        result['yelp_match_confidence'] = 'medium'
                    else:
                        result['yelp_match_confidence'] = 'low'

                    logger.debug(f"Yelp found match for {company_name} (similarity: {best_similarity:.2f})")
            else:
                logger.debug(f"Yelp: No results for {company_name}")

        elif response.status_code == 400:
            logger.debug(f"Yelp: Bad request for {company_name}")

    except Exception as e:
        logger.warning(f"Yelp API error: {e}")

    return result


def enrich_from_yellowpages_bbb(company_name: str, context: Dict) -> Dict:
    """
    Section F: YellowPages/BBB enrichment

    Uses Apify actors or custom scraper

    Args:
        company_name: Company search name
        context: Enrichment context with location

    Returns:
        Dict with YellowPages/BBB data
    """
    result = {
        'yp_phone': None,
        'yp_address': None,
        'yp_website': None,
        'yp_url': None,
        'yp_categories': None,
        'yp_match_confidence': 'none',
        'bbb_phone': None,
        'bbb_address': None,
        'bbb_website': None,
        'bbb_url': None,
        'bbb_match_confidence': 'none'
    }

    # This would require Apify actors or custom scrapers
    # For now, we'll skip implementation but maintain the structure

    logger.debug(f"YellowPages/BBB enrichment not implemented (requires Apify actors)")

    return result


def enrich_local_sources(company_name: str, context: Dict) -> Dict:
    """
    Aggregate local enrichment from all sources

    Args:
        company_name: Company search name
        context: Enrichment context

    Returns:
        Combined local enrichment data
    """
    result = {}

    # Google Maps (Priority #1 for phone)
    maps_data = enrich_from_google_maps(company_name, context)
    result.update(maps_data)

    # Yelp
    yelp_data = enrich_from_yelp(company_name, context)
    result.update(yelp_data)

    # YellowPages/BBB
    yp_bbb_data = enrich_from_yellowpages_bbb(company_name, context)
    result.update(yp_bbb_data)

    return result


def enrich_local_business(name: str, region: Optional[str] = None) -> Dict:
    """
    Perform unified local enrichment (Google Places + Yelp).

    Priority:
    1. Try Google Places first (using REST API)
    2. Fall back to Yelp if Google Places fails or returns nothing

    Returns a unified dict with phone/address/website information.

    Args:
        name: Company name to search
        region: Optional region/location info

    Returns:
        Dict with unified local enrichment data
    """
    result = {
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
        return result

    # Build query string
    query = name
    if region:
        query = f"{name} {region}"

    # 1) Try Google Places first (REST API)
    gp_data = None
    if GOOGLE_PLACES_API_KEY and name:
        logger.info(f"Trying Google Places REST API for local enrichment: '{name}'")
        try:
            # Text Search (New)
            search_url = "https://places.googleapis.com/v1/places:searchText"
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.internationalPhoneNumber,places.websiteUri,places.addressComponents"
            }
            payload = {
                "textQuery": query,
                "languageCode": "en"
            }

            logger.debug(f"Google Places query: {query}")

            response = requests.post(search_url, json=payload, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                places = data.get('places', [])

                if places:
                    # Choose best candidate by name similarity
                    best_place = None
                    best_similarity = 0.0

                    for place in places[:5]:  # Check top 5
                        place_name = place.get('displayName', {}).get('text', '')
                        similarity = calculate_name_similarity(place_name, name)

                        if similarity > best_similarity:
                            best_similarity = similarity
                            best_place = place

                    if best_place and best_similarity >= 0.6:
                        # Extract structured address components
                        address_components = best_place.get('addressComponents', [])
                        city_val = None
                        state_val = None
                        postal_val = None
                        country_val = None

                        for component in address_components:
                            types = component.get('types', [])
                            if 'locality' in types:
                                city_val = component.get('longText')
                            elif 'administrative_area_level_1' in types:
                                state_val = component.get('shortText')
                            elif 'postal_code' in types:
                                postal_val = component.get('longText')
                            elif 'country' in types:
                                country_val = component.get('longText')

                        gp_data = {
                            "phone": best_place.get('internationalPhoneNumber'),
                            "address": best_place.get('formattedAddress'),
                            "city": city_val,
                            "state_region": state_val,
                            "postal_code": postal_val,
                            "country": country_val,
                            "website": best_place.get('websiteUri'),
                        }

                        logger.info(f"✓ Google Places found data for '{name}' (similarity: {best_similarity:.2f})")
            else:
                logger.warning(f"Google Places API error: {response.status_code} - {response.text}")

        except Exception as e:
            logger.warning(f"Google Places API error: {e}")
            gp_data = None

    if gp_data and gp_data.get("phone"):
        # Success with Google Places
        result.update({
            "phone": gp_data.get("phone"),
            "phone_source": "google_places",
            "address": gp_data.get("address"),
            "city": gp_data.get("city"),
            "state_region": gp_data.get("state_region"),
            "postal_code": gp_data.get("postal_code"),
            "country": gp_data.get("country"),
            "website": gp_data.get("website"),
        })
        return result

    # 2) Fallback to Yelp if Places failed or returned nothing
    yelp_data = None
    if YELP_API_KEY and name:
        logger.info(f"Falling back to Yelp for local enrichment: '{name}'")
        try:
            url = "https://api.yelp.com/v3/businesses/search"

            headers = {
                "Authorization": f"Bearer {YELP_API_KEY}"
            }

            params = {
                "term": name,
                "limit": 5
            }

            # Add location if available
            if region:
                params["location"] = region

            logger.debug(f"Yelp query: {params}")

            response = requests.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                businesses = data.get('businesses', [])

                if businesses:
                    # Choose best candidate by name similarity
                    best_business = None
                    best_similarity = 0.0

                    for business in businesses:
                        business_name = business.get('name', '')
                        similarity = calculate_name_similarity(business_name, name)

                        if similarity > best_similarity:
                            best_similarity = similarity
                            best_business = business

                    if best_business and best_similarity >= 0.6:
                        # Extract data
                        location = best_business.get('location', {})

                        yelp_data = {
                            "phone": best_business.get('display_phone'),
                            "address": ', '.join(location.get('display_address', [])) if location.get('display_address') else None,
                            "city": location.get('city'),
                            "state_region": location.get('state'),
                            "postal_code": location.get('zip_code'),
                            "country": location.get('country'),
                            "website": None,  # Yelp doesn't provide business website in search results
                        }

                        logger.info(f"✓ Yelp found data for '{name}' (similarity: {best_similarity:.2f})")
        except Exception as e:
            logger.warning(f"Yelp API error: {e}")
            yelp_data = None

    if yelp_data and yelp_data.get("phone"):
        result.update({
            "phone": yelp_data.get("phone"),
            "phone_source": "yelp",
            "address": yelp_data.get("address"),
            "city": yelp_data.get("city"),
            "state_region": yelp_data.get("state_region"),
            "postal_code": yelp_data.get("postal_code"),
            "country": yelp_data.get("country"),
            "website": yelp_data.get("website"),
        })
        return result

    # No data from either source
    logger.warning(f"No local enrichment data found for '{name}'")
    return result
