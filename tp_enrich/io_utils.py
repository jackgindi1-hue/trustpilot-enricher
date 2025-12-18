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
def write_output_csv(df, output_path: str, *args, **kwargs):
    """
    Write enriched CSV reliably.
    IMPORTANT:
    Some callers pass (df, output_path, logger) or other extra args.
    We accept *args/**kwargs to stay compatible and avoid 500s.
    - Never silently drops enrichment columns
    - Ensures expected enrichment columns exist (creates them if missing)
    - Writes expected columns first, then any extra columns that may exist
    """
    expected_cols = [
        "consumer.displayname",
        "date",
        "raw_display_name",
        "review_date",
        "row_id",
        "run_id",
        "name_classification",
        "company_search_name",
        "company_normalized_key",
        "company_domain",
        "domain_confidence",
        "primary_phone",
        "primary_phone_display",
        "primary_phone_source",
        "primary_phone_confidence",
        "primary_email",
        "primary_email_type",
        "primary_email_source",
        "primary_email_confidence",
        "business_address",
        "business_city",
        "business_state_region",
        "business_postal_code",
        "business_country",
        "oc_company_name",
        "oc_jurisdiction",
        "oc_company_number",
        "oc_incorporation_date",
        "oc_match_confidence",
        "overall_lead_confidence",
        "enrichment_status",
        "debug_notes",  # PHASE 4 CLEANUP: renamed from enrichment_notes
        "all_phones_json",
        # PHASE 4 CLEANUP: Split phone columns
        "phone_google",
        "phone_yelp",
        "phone_website",
        "phone_apollo",
        "generic_emails_json",
        "person_emails_json",
        "catchall_emails_json",
        # Phase 2 Contact Data Fields (HOTFIX v2)
        "phase2_bbb_phone",
        "phase2_bbb_email",
        "phase2_bbb_website",
        "phase2_bbb_names",
        "phase2_yp_phone",
        "phase2_yp_email",
        "phase2_yp_website",
        "phase2_yp_names",
        "source_platform",
    ]
    # Ensure expected columns exist
    for col in expected_cols:
        if col not in df.columns:
            df[col] = None
    # Keep expected first, then extras
    extras = [c for c in df.columns.tolist() if c not in expected_cols]
    final_cols = expected_cols + extras
    # ============================================================
    # PHASE 4 CLEANUP: Split all_phones_json into real columns
    # ============================================================
    import json

    def _safe_json(x):
        try:
            if not x:
                return {}
            return json.loads(x) if isinstance(x, str) else (x or {})
        except Exception:
            return {}

    if "all_phones_json" in df.columns:
        phones = df["all_phones_json"].apply(_safe_json)
        df["phone_google"] = phones.apply(lambda x: x.get("google") or "")
        df["phone_yelp"] = phones.apply(lambda x: x.get("yelp") or "")
        df["phone_website"] = phones.apply(lambda x: x.get("website") or "")
        df["phone_apollo"] = phones.apply(lambda x: x.get("apollo") or "")
        logger.info("Split all_phones_json into: phone_google, phone_yelp, phone_website, phone_apollo")

    # ============================================================
    # PHASE 4 CLEANUP: Rename enrichment_notes -> debug_notes
    # ============================================================
    if "enrichment_notes" in df.columns:
        df["debug_notes"] = df["enrichment_notes"]
        df = df.drop(columns=["enrichment_notes"])
        logger.info("Renamed enrichment_notes -> debug_notes")

    # Sanity log
    try:
        phones = int(df["primary_phone"].notna().sum())
        emails = int(df["primary_email"].notna().sum())
        logger.info(f"Export sanity: rows={len(df)} phones_nonnull={phones} emails_nonnull={emails}")
    except Exception:
        logger.info(f"Export sanity: rows={len(df)} (could not compute phone/email counts)")
    logger.info(f"Writing output CSV to: {output_path}")
    logger.info(f"Final columns count: {len(final_cols)}")

    # ============================================================
    # PHASE 4 CLEANUP: Replace "none" with empty strings
    # ============================================================
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = (
                df[c]
                .astype(str)
                .str.replace(r"[\u0000-\u001F\u007F]", "", regex=True)  # control chars
                .str.replace("\u00A0", " ", regex=False)                # nbsp
                .replace("none", "")  # PHASE 4 CLEANUP: Remove literal "none"
                .replace("None", "")
            )

    # PHASE 4 CLEANUP: Write with na_rep="" to avoid "none" in CSV
    df.to_csv(output_path, index=False, columns=final_cols, encoding="utf-8", na_rep="")
    logger.info(f"Successfully wrote {len(df)} rows to {output_path}")
def get_output_schema(df=None):
    """
    PATCH 2 — HOTFIX v3: Returns the output column order (schema).
    If df is provided, we keep all existing df columns and append Phase2 fields (no duplicates).
    """
    base_cols = [
        # essentials
        "row_id",
        "raw_display_name",
        "name_classification",
        "company_search_name",
        "domain",
        "primary_phone",
        "phone_source",
        "phone_confidence",
        "primary_email",
        "email_type",
        "email_source",
        "email_confidence",
        "email_tried",
        "email_providers_attempted",
        # location
        "address",
        "city",
        "state_region",
        "postal_code",
        "country",
        "website",
        # bookkeeping
        "overall_confidence",
        "status",
        "run_id",
        "source_platform",
    ]

    phase2_cols = [
        # phase 2 derived (keep minimal, actual DATA not just URLs)
        "phase2_bbb_phone",
        "phase2_bbb_email",
        "phase2_bbb_website",
        "phase2_bbb_names_json",
        "phase2_yp_phone",
        "phase2_yp_email",
        "phase2_yp_website",
        "phase2_yp_names_json",
        "phase2_oc_company_number",
        "phase2_oc_status",
        "phase2_notes",
    ]

    if df is None:
        # fallback if called without df
        return base_cols + phase2_cols

    # Keep ALL input columns, then ensure base+phase2 exist in output ordering.
    # Order preference: base_cols (if present) -> all other df cols -> phase2 cols
    cols = list(df.columns)

    seen = set()
    out = []

    def add(c):
        if c and c not in seen:
            seen.add(c)
            out.append(c)

    for c in base_cols:
        if c in cols:
            add(c)

    for c in cols:
        add(c)

    for c in phase2_cols:
        add(c)

    return out
