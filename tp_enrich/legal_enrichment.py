"""
Legal verification logic - Section G
OpenCorporates enrichment
"""

import os
import requests
from typing import Dict, Optional, Literal
from Levenshtein import ratio
from .logging_utils import setup_logger

logger = setup_logger(__name__)

MatchConfidence = Literal["none", "low", "medium", "high"]


def enrich_from_opencorporates(company_name: str, context: Dict) -> Dict:
    """
    Section G: OpenCorporates legal verification

    Args:
        company_name: Company search name
        context: Enrichment context with jurisdiction info

    Returns:
        Dict with OpenCorporates data
    """
    result = {
        'oc_company_name': None,
        'oc_jurisdiction': None,
        'oc_company_number': None,
        'oc_incorporation_date': None,
        'oc_registered_address': None,
        'oc_match_confidence': 'none'
    }

    api_key = os.getenv('OPENCORPORATES_API_KEY')

    # OpenCorporates works without API key but with rate limits
    # API key provides higher rate limits

    try:
        url = "https://api.opencorporates.com/v0.4/companies/search"

        params = {
            'q': company_name,
            'per_page': 5
        }

        # Add jurisdiction if available
        state = context.get('state')
        country = context.get('country', 'us')

        if state:
            # Format jurisdiction as country_state (e.g., us_ca for California)
            jurisdiction = f"{country}_{state}".lower()
            params['jurisdiction_code'] = jurisdiction

        if api_key:
            params['api_token'] = api_key

        logger.debug(f"Querying OpenCorporates for: {company_name}")

        response = requests.get(url, params=params, timeout=15)

        if response.status_code == 200:
            data = response.json()
            companies = data.get('results', {}).get('companies', [])

            if companies:
                # Find best match by name similarity
                best_company = None
                best_similarity = 0.0

                for item in companies:
                    company = item.get('company', {})
                    company_name_oc = company.get('name', '')

                    similarity = ratio(company_name.lower(), company_name_oc.lower())

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_company = company

                if best_company and best_similarity >= 0.7:
                    result['oc_company_name'] = best_company.get('name')
                    result['oc_jurisdiction'] = best_company.get('jurisdiction_code')
                    result['oc_company_number'] = best_company.get('company_number')
                    result['oc_incorporation_date'] = best_company.get('incorporation_date')

                    # Get registered address
                    registered_address = best_company.get('registered_address_in_full')
                    if registered_address:
                        result['oc_registered_address'] = registered_address

                    # Set confidence
                    if best_similarity >= 0.95:
                        result['oc_match_confidence'] = 'high'
                    elif best_similarity >= 0.85:
                        result['oc_match_confidence'] = 'medium'
                    else:
                        result['oc_match_confidence'] = 'low'

                    logger.debug(f"OpenCorporates found match for {company_name} (similarity: {best_similarity:.2f})")
            else:
                logger.debug(f"OpenCorporates: No results for {company_name}")

        elif response.status_code == 403:
            logger.warning("OpenCorporates: Rate limit exceeded or API key invalid")

    except Exception as e:
        logger.warning(f"OpenCorporates API error: {e}")

    return result
