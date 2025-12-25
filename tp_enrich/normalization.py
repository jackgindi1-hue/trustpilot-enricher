"""
Business name normalization logic - Section B
"""

import re
import pandas as pd
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


def ensure_company_search_name(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure company_search_name is populated for business rows

    If a row is classified as "business" but company_search_name is empty,
    copy the value from raw_display_name.

    This fixes the bug where business rows don't get enriched because
    company_search_name is missing.

    Args:
        df: DataFrame with name_classification and raw_display_name columns

    Returns:
        DataFrame with company_search_name populated for business rows
    """
    if "company_search_name" not in df.columns:
        df["company_search_name"] = pd.NA

    if "raw_display_name" not in df.columns:
        return df

    name_class = df.get("name_classification")
    if name_class is None:
        return df

    mask_business = name_class.eq("business")
    company_col = df["company_search_name"].astype("string")
    mask_empty = company_col.isna() | (company_col.str.strip() == "")

    raw_col = df["raw_display_name"].astype("string")
    mask_raw_ok = raw_col.notna() & (raw_col.str.strip() != "")

    mask_update = mask_business & mask_empty & mask_raw_ok

    df.loc[mask_update, "company_search_name"] = raw_col[mask_update].str.strip()

    return df
