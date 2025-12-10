"""
Business deduplication logic - Section B
Groups companies by normalized key and enriches only once
"""

import pandas as pd
from typing import Dict, List, Set
from .logging_utils import setup_logger

logger = setup_logger(__name__)


def identify_unique_businesses(df: pd.DataFrame) -> Dict[str, Dict]:
    """
    Identify unique businesses to enrich based on normalized key

    Section B: Group companies by normalized key and enrich only once

    Args:
        df: DataFrame with business rows (already classified)

    Returns:
        Dictionary mapping normalized_key -> business info dict
    """
    unique_businesses = {}

    # Filter to only business rows
    business_df = df[df['name_classification'] == 'business'].copy()

    logger.info(f"Identifying unique businesses from {len(business_df)} business rows")

    for idx, row in business_df.iterrows():
        normalized_key = row.get('company_normalized_key', '')

        if not normalized_key:
            continue

        # If we haven't seen this business yet, store it
        if normalized_key not in unique_businesses:
            unique_businesses[normalized_key] = {
                'company_search_name': row.get('company_search_name', ''),
                'company_normalized_key': normalized_key,
                'raw_display_name': row.get('raw_display_name', ''),
                # Extract any available location/context info
                'city': row.get('city', None),
                'state': row.get('state', None),
                'region': row.get('region', None),
                'country': row.get('country', None),
            }

    logger.info(f"Found {len(unique_businesses)} unique businesses to enrich")

    return unique_businesses


def get_enrichment_context(business_info: Dict) -> Dict:
    """
    Extract context for enrichment from business info

    Args:
        business_info: Business information dict

    Returns:
        Context dict with location and other info
    """
    return {
        'name': business_info.get('company_search_name', ''),
        'normalized_key': business_info.get('company_normalized_key', ''),
        'city': business_info.get('city'),
        'state': business_info.get('state'),
        'region': business_info.get('region'),
        'country': business_info.get('country'),
    }
