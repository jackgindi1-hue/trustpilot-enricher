"""
Business name normalization logic - Section B
"""

import re
from typing import Tuple
from .logging_utils import setup_logger

logger = setup_logger(__name__)


def create_company_search_name(display_name: str) -> str:
    """
    Create company_search_name from displayName

    Section B rules:
    - Clean displayName
    - Remove "Customer Service" or similar trailing noise
    - Keep LLC/Inc suffixes if present

    Args:
        display_name: Raw display name

    Returns:
        Cleaned company search name
    """
    if not display_name:
        return ""

    name = str(display_name).strip()

    # Remove "Customer Service" and similar trailing noise
    noise_patterns = [
        r'\s*-?\s*customer\s+service\s*$',
        r'\s*-?\s*cust\.?\s+svc\.?\s*$',
        r'\s*-?\s*support\s*$',
    ]

    for pattern in noise_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # Clean up extra whitespace
    name = ' '.join(name.split())

    return name


def create_company_normalized_key(company_search_name: str) -> str:
    """
    Create company_normalized_key for deduplication

    Section B rules:
    - lowercase
    - strip punctuation
    - collapse spaces

    Args:
        company_search_name: Cleaned company name

    Returns:
        Normalized key for deduplication
    """
    if not company_search_name:
        return ""

    # Lowercase
    key = company_search_name.lower()

    # Strip punctuation (keep only alphanumeric and spaces)
    key = re.sub(r'[^\w\s]', '', key)

    # Collapse spaces
    key = ' '.join(key.split())

    # Remove all spaces for tight matching
    key = key.replace(' ', '')

    return key


def normalize_business_name(display_name: str) -> Tuple[str, str]:
    """
    Normalize business name and create search/dedup keys

    Args:
        display_name: Raw display name

    Returns:
        Tuple of (company_search_name, company_normalized_key)
    """
    search_name = create_company_search_name(display_name)
    normalized_key = create_company_normalized_key(search_name)

    return search_name, normalized_key
