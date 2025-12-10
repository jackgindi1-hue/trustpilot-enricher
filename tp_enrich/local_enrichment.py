"""
Local business enrichment logic - Sections D, E, F
Google Maps, Yelp Fusion, YellowPages/BBB
"""

import os
import requests
import googlemaps
from typing import Dict, Optional, Literal
from Levenshtein import ratio
from .logging_utils import setup_logger
from .domain_enrichment import extract_domain

logger = setup_logger(__name__)

MatchConfidence = Literal["none", "low", "medium", "high"]


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
    Section D: Google Maps / Places enrichment

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
        gmaps = googlemaps.Client(key=api_key)

        # Build query
        city = context.get('city', '')
        state = context.get('state', '')

        if city or state:
            query = f"{company_name} {city} {state}".strip()
        else:
            query = company_name

        logger.debug(f"Querying Google Maps for: {query}")

        # Text search
        places_result = gmaps.places(query=query)

        if places_result.get('results'):
            candidates = places_result['results']

            # Choose best candidate by name similarity
            best_candidate = None
            best_similarity = 0.0

            for candidate in candidates[:5]:  # Check top 5
                candidate_name = candidate.get('name', '')
                similarity = calculate_name_similarity(candidate_name, company_name)

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_candidate = candidate

            if best_candidate and best_similarity >= 0.6:
                place_id = best_candidate.get('place_id')

                # Get place details
                details = gmaps.place(place_id=place_id, fields=[
                    'formatted_phone_number',
                    'international_phone_number',
                    'formatted_address',
                    'website',
                    'types'
                ])

                if details.get('result'):
                    place = details['result']

                    result['maps_phone_main'] = place.get('formatted_phone_number')
                    result['maps_phone_international'] = place.get('international_phone_number')
                    result['maps_website'] = place.get('website')
                    result['maps_address'] = place.get('formatted_address')
                    result['maps_types'] = ','.join(place.get('types', []))

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
