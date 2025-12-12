"""
Core enrichment pipeline - reusable function for CLI and API
Extracted from main.py to enable both CLI and API access
"""
import os
import json
import pandas as pd
import numpy as np
from typing import Dict, Optional, Any
from urllib.parse import urlparse
from .logging_utils import setup_logger
from .io_utils import load_input_csv, write_output_csv, get_output_schema
from .classification import classify_name
from .normalization import normalize_business_name, ensure_company_search_name
from .dedupe import identify_unique_businesses, get_enrichment_context
from .cache import EnrichmentCache
from . import local_enrichment
from .email_enrichment import enrich_emails_for_domain
from .phone_enrichment import enrich_business_phone_waterfall
from .merge_results import merge_enrichment_results

logger = setup_logger(__name__)


def _safe_str(x):
    """Safely convert value to string, handling None and NaN."""
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    s = str(x).strip()
    return s


def _norm_key(x: str) -> str:
    """Normalize key: keep it simple + stable; match how company_search_name is generated."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().lower()
    # light normalization to make join stable
    s = " ".join(s.split())
    return s


def _get(res, *keys, default=None):
    """
    Works with:
      - dict results
      - dataclass/obj results (attributes)
      - nested dicts/objs
    """
    cur = res
    for k in keys:
        if cur is None:
            return default
        # dict
        if isinstance(cur, dict):
            cur = cur.get(k, None)
        else:
            cur = getattr(cur, k, None)
    return default if cur is None else cur


def merge_enrichment_back_to_rows(df: pd.DataFrame, enriched_businesses: list) -> pd.DataFrame:
    """
    PATCH: Robust merge-back function

    df: row-level dataframe (all rows including business + person + other)
    enriched_businesses: list of dicts, each dict contains business-level enrichment fields

    Returns df with enrichment columns filled.

    Key improvements:
    - Uses company_search_name as primary join key (works even if company_normalized_key is NaN)
    - Handles missing columns gracefully
    - Deduplicates businesses by join key (keeps best record by data completeness)
    - Forces object dtype to avoid pandas FutureWarning
    """
    logger.info("Step 6: Merging enrichment results back to rows...")

    if df is None or df.empty:
        logger.warning("  Empty dataframe, nothing to merge")
        return df

    # Ensure join columns exist
    if "company_search_name" not in df.columns:
        # fall back to raw_display_name if that exists
        if "raw_display_name" in df.columns:
            df["company_search_name"] = df["raw_display_name"]
        else:
            logger.error("  Missing company_search_name and raw_display_name columns")
            raise ValueError("merge_enrichment_back_to_rows: missing company_search_name and raw_display_name")

    # Create a stable join key on rows
    df = df.copy()
    df["_join_key"] = df["company_search_name"].apply(_norm_key)

    # Build business-level table
    biz_df = pd.DataFrame(enriched_businesses or [])
    if biz_df.empty:
        # Nothing to merge
        logger.info("  No enrichment results to merge")
        return df.drop(columns=["_join_key"], errors="ignore")

    # Create stable join key on businesses
    # Prefer company_search_name; fallback to company_normalized_key; fallback to company_name
    for cand in ["company_search_name", "company_normalized_key", "company_name", "name"]:
        if cand in biz_df.columns:
            biz_df["_join_key"] = biz_df[cand].apply(_norm_key)
            # If we got at least some non-empty keys, use it
            if (biz_df["_join_key"] != "").any():
                break
    if "_join_key" not in biz_df.columns:
        biz_df["_join_key"] = ""

    logger.info("  Built enrichment index for %d businesses", len(biz_df))

    # Deduplicate businesses by join key (keep best record: prefer ones with phone/email)
    def _score_row(r):
        score = 0
        if str(r.get("primary_phone") or "").strip(): score += 2
        if str(r.get("primary_email") or "").strip(): score += 2
        if str(r.get("business_address") or "").strip(): score += 1
        if str(r.get("company_domain") or "").strip(): score += 1
        return score

    biz_df["_score"] = biz_df.apply(_score_row, axis=1)
    biz_df = biz_df.sort_values(["_join_key", "_score"], ascending=[True, False])
    biz_df = biz_df.drop_duplicates(subset=["_join_key"], keep="first").drop(columns=["_score"])

    logger.info("  After deduplication: %d unique businesses", len(biz_df))

    # Columns we want to bring back (only if present in business table)
    wanted = [
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
        "all_phones_json",
        "generic_emails_json",
        "person_emails_json",
        "catchall_emails_json",
        "overall_lead_confidence",
        "enrichment_status",
        "enrichment_notes",
    ]
    present = [c for c in wanted if c in biz_df.columns]

    logger.info("  Merging %d enrichment columns: %s", len(present), present)

    # Prepare minimal merge frame
    merge_df = biz_df[["_join_key"] + present].copy()

    # Merge
    out = df.merge(merge_df, on="_join_key", how="left", suffixes=("", "_biz"))

    # IMPORTANT: pandas dtype safety (avoid FutureWarning & "incompatible dtype" issues)
    # Ensure target columns are object so assignment doesn't silently fail / coerce weirdly.
    for c in present:
        if c not in out.columns:
            out[c] = ""
        out[c] = out[c].astype("object")

    # Cleanup
    out = out.drop(columns=["_join_key"], errors="ignore")

    logger.info("  Finished merging enrichment results into %d rows", len(out))
    return out


def _compute_confidence(row: Dict[str, Any]) -> str:
    """Compute overall confidence based on phone and email presence."""
    has_phone = bool(row.get("primary_phone"))
    has_email = bool(row.get("primary_email"))
    if has_phone and has_email:
        return "high"
    if has_phone or has_email:
        return "medium"
    return "failed"


def enrich_single_business(name: str, region: str | None = None) -> Dict[str, Any]:
    """
    PHASE 2: Enhanced enrichment function with phone waterfall.

    Flow:
    1. Google Places for address/website (phone extracted but not used directly)
    2. Extract domain from website
    3. Phone waterfall (Google → Yelp → Website → Apollo) with validation
    4. Email enrichment (Hunter only)
    5. Compute overall confidence

    Args:
        name: Business name
        region: Optional region/location

    Returns:
        Dict with enriched business data including validated phone
    """
    logger.info("Enriching business: %s", name)

    row = {
        "business_name": name,
        "primary_phone": None,
        "phone": None,
        "primary_email": None,
        "email": None,
        "emails": [],
        "email_source": None,
        "website": None,
        "domain": None,
        "address": None,
        "city": None,
        "state_region": None,
        "postal_code": None,
        "country": None,
    }

    # ---- LOCAL (Google Places only)
    local = local_enrichment.enrich_local_business(name, region)

    # Store address/location data
    if local:
        for f in ["address", "city", "state_region", "postal_code", "country"]:
            if local.get(f):
                row[f] = local[f]

        if local.get("website"):
            row["website"] = local["website"]

    # ---- DOMAIN EXTRACT
    website = row.get("website")
    if website:
        try:
            parsed = urlparse(website)
            host = parsed.netloc or parsed.path
            if host.startswith("www."):
                host = host[4:]
            row["domain"] = host.lower()
        except Exception:
            row["domain"] = None

    domain = row.get("domain")

    # ---- PHONE WATERFALL (Google → Yelp → Website → Apollo)
    phone_layer = enrich_business_phone_waterfall(
        biz_name=name,
        google_hit=local or {},
        domain=domain
    )

    # Map phone results
    row["primary_phone"] = phone_layer.get("primary_phone")
    row["phone"] = phone_layer.get("primary_phone")
    row["phone_source"] = phone_layer.get("primary_phone_source")
    row["phone_confidence"] = phone_layer.get("primary_phone_confidence")
    row["all_phones_json"] = phone_layer.get("all_phones_json")

    # ---- EMAIL ENRICHMENT (Hunter only)
    email_data = enrich_emails_for_domain(domain)
    if email_data.get("primary_email"):
        row["primary_email"] = email_data["primary_email"]
        row["email"] = email_data["primary_email"]
        row["emails"] = email_data.get("emails") or []
        row["email_source"] = email_data.get("email_source")

    row["confidence"] = _compute_confidence(row)
    return row
def enrich_business(business_info: Dict, cache: EnrichmentCache) -> Dict:
    """
    Enrich a single business through all sources
    Args:
        business_info: Business information dict
        cache: Enrichment cache
    Returns:
        Complete enrichment result
    """
    normalized_key = business_info['company_normalized_key']
    company_name = business_info['company_search_name']

    # Check cache
    if cache.has(normalized_key):
        logger.info(f"  -> Using cached result for {company_name}")
        return cache.get(normalized_key)

    # Get enrichment context
    context = get_enrichment_context(business_info)
    region = context.get('state') or context.get('region')

    # Use the simplified enrichment function
    enriched_data = enrich_single_business(company_name, region=region)

    # --- BUILD ENRICHED ROW (DO NOT CHANGE INDENTATION) ---
    enriched_row = {
        # identity / keys
        "company_normalized_key": enriched_data.get("company_normalized_key", normalized_key),
        "company_search_name": enriched_data.get("company_search_name", company_name),
        "company_domain": enriched_data.get("company_domain") or enriched_data.get("domain"),
        "domain_confidence": enriched_data.get("domain_confidence", "high" if enriched_data.get("domain") else "none"),

        # primary phone
        "primary_phone": enriched_data.get("primary_phone"),
        "primary_phone_display": enriched_data.get("primary_phone"),
        "primary_phone_source": enriched_data.get("primary_phone_source") or enriched_data.get("phone_source"),
        "primary_phone_confidence": enriched_data.get("primary_phone_confidence") or enriched_data.get("phone_confidence"),

        # primary email
        "primary_email": enriched_data.get("primary_email"),
        "primary_email_type": enriched_data.get("primary_email_type", "generic"),
        "primary_email_source": enriched_data.get("primary_email_source") or enriched_data.get("email_source"),
        "primary_email_confidence": enriched_data.get("primary_email_confidence", "medium" if enriched_data.get("primary_email") else "none"),

        # address
        "business_address": enriched_data.get("business_address") or enriched_data.get("address"),
        "business_city": enriched_data.get("business_city") or enriched_data.get("city"),
        "business_state_region": enriched_data.get("business_state_region") or enriched_data.get("state_region"),
        "business_postal_code": enriched_data.get("business_postal_code") or enriched_data.get("postal_code"),
        "business_country": enriched_data.get("business_country") or enriched_data.get("country"),

        # metadata
        "overall_lead_confidence": enriched_data.get("overall_lead_confidence", enriched_data.get("confidence", "medium")),
        "enrichment_status": enriched_data.get("enrichment_status", "success"),
        "enrichment_notes": enriched_data.get("enrichment_notes", ""),

        # debug payloads
        "all_phones_json": enriched_data.get("all_phones_json"),
        "generic_emails_json": enriched_data.get("generic_emails_json"),
        "person_emails_json": enriched_data.get("person_emails_json"),
        "catchall_emails_json": enriched_data.get("catchall_emails_json"),
        "source_platform": "trustpilot",
    }
    # --- END BUILD ENRICHED ROW ---

    # Save to cache
    cache.set(normalized_key, enriched_row)
    logger.info(f"  -> Completed enrichment for {company_name} (confidence: {enriched_row.get('overall_lead_confidence')})}")
    return enriched_row
def run_pipeline(
    input_csv_path: str,
    output_csv_path: str,
    cache_file: str = "enrichment_cache.json",
    config: Optional[Dict] = None
) -> Dict:
    """
    Core enrichment pipeline - reusable function for CLI and API
    Args:
        input_csv_path: Path to input Trustpilot CSV
        output_csv_path: Path to output enriched CSV
        cache_file: Path to enrichment cache file
        config: Optional configuration dict (e.g., lender_name_override)
    Returns:
        Dict with statistics about the run
    """
    logger.info("="*60)
    logger.info("Starting Trustpilot Enrichment Pipeline")
    logger.info("="*60)
    config = config or {}
    # Load input CSV
    logger.info("Step 1: Loading input CSV...")
    df = load_input_csv(input_csv_path)
    # Add row_id if not present
    if 'row_id' not in df.columns:
        df['row_id'] = range(1, len(df) + 1)
    # Classify each row
    logger.info("Step 2: Classifying display names...")
    # Note: raw_display_name is already mapped in load_input_csv()
    df['name_classification'] = df['raw_display_name'].apply(classify_name)
    classification_counts = df['name_classification'].value_counts()
    logger.info(f"  Classification results: {dict(classification_counts)}")
    # Normalize business names
    logger.info("Step 3: Normalizing business names...")
    business_mask = df['name_classification'] == 'business'
    df.loc[business_mask, ['company_search_name', 'company_normalized_key']] = df.loc[business_mask, 'raw_display_name'].apply(
        lambda x: pd.Series(normalize_business_name(x))
    )
    # Ensure company_search_name is populated for all business rows
    df = ensure_company_search_name(df)
    # Debug logging
    logger.info(
        "Post-normalization: business rows=%s, business with company_search_name=%s",
        int((df["name_classification"] == "business").sum()),
        int(
            (
                (df["name_classification"] == "business")
                & df["company_search_name"].notna()
                & (df["company_search_name"].astype("string").str.strip() != "")
            ).sum()
        ),
    )
    # Dedup by company_normalized_key
    logger.info("Step 4: Identifying unique businesses...")
    unique_businesses = identify_unique_businesses(df)
    logger.info(f"  Found {len(unique_businesses)} unique businesses to enrich")
    # Initialize cache
    cache = EnrichmentCache(cache_file)
    # Enrich each unique business
    logger.info("Step 5: Enriching businesses...")
    enrichment_results = {}
    for idx, (normalized_key, business_info) in enumerate(unique_businesses.items(), 1):
        logger.info(f"  [{idx}/{len(unique_businesses)}] Processing: {business_info['company_search_name']}")
        try:
            result = enrich_business(business_info, cache)
            enrichment_results[normalized_key] = result
        except Exception as e:
            logger.error(f"  Error enriching {business_info['company_search_name']}: {e}")
            enrichment_results[normalized_key] = {
                'enrichment_status': 'error',
                'enrichment_notes': str(e),
                'overall_lead_confidence': 'failed'
            }
    # Save cache
    cache.save_cache()
    # Calculate enrichment statistics
    logger.info("="*60)
    logger.info("ENRICHMENT SUMMARY")
    logger.info("="*60)
    logger.info(f"  Total unique businesses processed: {len(enrichment_results)}")
    # Count results with domains, phones, emails
    with_domain = sum(1 for r in enrichment_results.values() if r.get('company_domain'))
    with_phone = sum(1 for r in enrichment_results.values() if r.get('primary_phone'))
    with_email = sum(1 for r in enrichment_results.values() if r.get('primary_email'))
    logger.info(f"  Businesses with domain: {with_domain}/{len(enrichment_results)}")
    logger.info(f"  Businesses with phone: {with_phone}/{len(enrichment_results)}")
    logger.info(f"  Businesses with email: {with_email}/{len(enrichment_results)}")
    # Count by confidence
    conf_counts = {}
    for r in enrichment_results.values():
        conf = r.get('overall_lead_confidence', 'unknown')
        conf_counts[conf] = conf_counts.get(conf, 0) + 1
    logger.info(f"  Confidence breakdown: {conf_counts}")
    logger.info("="*60)

    # Merge back to rows using robust matching function
    # Convert dict values to list for new merge function signature
    df = merge_enrichment_back_to_rows(df, list(enrichment_results.values()))
    # Map source columns from input
    if 'url' in df.columns:
        df['source_review_url'] = df['url']
    if 'date' in df.columns or 'review_date' in df.columns:
        df['review_date'] = df.get('date', df.get('review_date'))
    if 'rating' in df.columns or 'stars' in df.columns:
        df['review_rating'] = df.get('rating', df.get('stars'))
    df['source_platform'] = 'trustpilot'
    # Apply lender name override if provided
    if config.get('lender_name_override'):
        df['source_lender_name'] = config['lender_name_override']
    else:
        # Extract lender name from URL if available
        if 'source_review_url' in df.columns:
            df['source_lender_name'] = df['source_review_url'].apply(
                lambda x: x.split('/')[3] if isinstance(x, str) and '/' in x else None
            )
    # Write final CSV
    logger.info("Step 7: Writing output CSV...")
    output_schema = get_output_schema()
    write_output_csv(df, output_csv_path, output_schema)
    # Calculate statistics
    stats = {
        'total_rows': len(df),
        'businesses': int(classification_counts.get('business', 0)),
        'persons': int(classification_counts.get('person', 0)),
        'others': int(classification_counts.get('other', 0)),
        'unique_businesses': len(unique_businesses),
        'enriched': len([r for r in enrichment_results.values() if r.get('enrichment_status') != 'error']),
        'errors': len([r for r in enrichment_results.values() if r.get('enrichment_status') == 'error'])
    }
    logger.info("="*60)
    logger.info("Pipeline completed successfully!")
    logger.info(f"  Total rows: {stats['total_rows']}")
    logger.info(f"  Businesses: {stats['businesses']}")
    logger.info(f"  Unique businesses enriched: {stats['enriched']}/{stats['unique_businesses']}")
    logger.info(f"  Output file: {output_csv_path}")
    logger.info("="*60)
    return stats
