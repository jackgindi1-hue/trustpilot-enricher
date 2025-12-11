"""
I/O utilities for reading and writing CSV files
"""

import pandas as pd
from typing import Dict, List, Optional
from .logging_utils import setup_logger

logger = setup_logger(__name__)


def map_display_name_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Robustly map display name column to raw_display_name

    Handles various column naming conventions:
    - consumer.displayName (Apify Trustpilot scraper)
    - consumer.display_name
    - displayName
    - display_name

    Args:
        df: DataFrame with original columns (already normalized to lowercase)

    Returns:
        DataFrame with raw_display_name column added
    """
    # Create lowercase mapping (columns are already lowercase from load_input_csv)
    lower_map = {col.lower(): col for col in df.columns}

    # Priority list for display name columns (all lowercase since columns are normalized)
    candidate_keys = [
        "consumer.displayname",
        "consumer.display_name",
        "displayname",
        "display_name",
    ]

    # Find first matching column
    display_col = None
    for candidate in candidate_keys:
        if candidate in lower_map:
            display_col = lower_map[candidate]
            break

    # Map to raw_display_name
    if display_col:
        df["raw_display_name"] = df[display_col].astype("string").fillna("").str.strip()

        # Debug logging
        sample_values = df["raw_display_name"].head(5).tolist()
        logger.info(f"✓ Using display name column '{display_col}'")
        logger.info(f"  Sample values: {sample_values}")
    else:
        # No display name column found
        df["raw_display_name"] = pd.NA
        logger.warning(f"✗ No display name column found in: {list(df.columns)}")
        logger.warning(f"  Looked for: {candidate_keys}")

    return df


def map_review_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map review date column to review_date

    Handles various date column naming conventions:
    - dates.experiencedDate (Apify Trustpilot scraper)
    - dates.experienceddate (normalized)
    - date
    - review_date

    Args:
        df: DataFrame with original columns (already normalized to lowercase)

    Returns:
        DataFrame with review_date column added
    """
    # Create lowercase mapping (columns are already lowercase from load_input_csv)
    lower_map = {col.lower(): col for col in df.columns}

    # Priority list for date columns (all lowercase since columns are normalized)
    candidate_keys = [
        "dates.experienceddate",
        "dates.experienced_date",
        "review_date",
        "date",
    ]

    # Find first matching column
    date_col = None
    for candidate in candidate_keys:
        if candidate in lower_map:
            date_col = lower_map[candidate]
            break

    # Map to review_date
    if date_col:
        df["review_date"] = df[date_col]
        logger.info(f"✓ Using date column '{date_col}' for review_date")
    else:
        # No date column found
        df["review_date"] = pd.NA
        logger.warning(f"✗ No date column found in: {list(df.columns)}")
        logger.warning(f"  Looked for: {candidate_keys}")

    return df


def load_input_csv(filepath: str) -> pd.DataFrame:
    """
    Load and normalize input Trustpilot CSV

    Args:
        filepath: Path to input CSV

    Returns:
        DataFrame with normalized column names and raw_display_name mapped
    """
    logger.info(f"Loading input CSV from: {filepath}")
    df = pd.read_csv(filepath)

    # Store original column names for debugging
    original_columns = list(df.columns)

    # Normalize column names to lowercase with underscores
    df.columns = df.columns.str.lower().str.replace(' ', '_').str.replace('-', '_')

    logger.info(f"Loaded {len(df)} rows")
    logger.info(f"  Original columns: {original_columns}")
    logger.info(f"  Normalized columns: {list(df.columns)}")

    # Map display name column to raw_display_name
    df = map_display_name_column(df)

    # Map review date column
    df = map_review_date_column(df)

    return df


def write_output_csv(df: pd.DataFrame, filepath: str, column_order: List[str] = None) -> None:
    """
    Write the enriched DataFrame to CSV.

    IMPORTANT:
    - We DO NOT filter columns here.
    - We write EVERY COLUMN currently on the DataFrame.
      That includes:
        - consumer.displayName / DATE (original Trustpilot data)
        - business_phone
        - business_email
        - business_address
        - business_city
        - business_state_region
        - business_postal_code
        - business_country
        - business_website
        - and any future enrichment fields.

    Args:
        df: DataFrame to write
        filepath: Output file path
        column_order: DEPRECATED - ignored, all columns are written
    """
    logger.info(f"Writing output CSV to: {filepath}")
    logger.info(f"Final columns: {list(df.columns)}")
    logger.info(f"Total rows to write: {len(df)}")

    # 🚨 THE FIX: write EVERYTHING, no column whitelist
    df.to_csv(filepath, index=False)

    logger.info(f"Successfully wrote {len(df)} rows to {filepath}")

def get_output_schema() -> List[str]:
    """
    Returns the exact output schema as defined in Section M

    Returns:
        List of column names in exact order
    """
    return [
        'row_id',
        'source_platform',
        'source_lender_name',
        'source_review_url',
        'review_date',
        'review_rating',
        'raw_display_name',
        'name_classification',
        'company_search_name',
        'company_normalized_key',
        'company_domain',
        'domain_confidence',
        'primary_phone',
        'primary_phone_display',
        'primary_phone_source',
        'primary_phone_confidence',
        'primary_email',
        'primary_email_type',
        'primary_email_source',
        'primary_email_confidence',
        'business_address',
        'business_city',
        'business_state_region',
        'business_postal_code',
        'business_country',
        'oc_company_name',
        'oc_jurisdiction',
        'oc_company_number',
        'oc_incorporation_date',
        'oc_match_confidence',
        'overall_lead_confidence',
        'enrichment_status',
        'enrichment_notes',
        'all_phones_json',
        'generic_emails_json',
        'person_emails_json',
        'catchall_emails_json'
    ]
