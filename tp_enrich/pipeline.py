"""
Core enrichment pipeline - reusable function for CLI and API
Extracted from main.py to enable both CLI and API access
"""
import os
import json
import pandas as pd
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
from .merge_results import merge_enrichment_results

logger = setup_logger(__name__)


def _norm_key(val):
    """Normalize a key for matching (stringify, strip, lowercase)."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except Exception:
        pass
    return str(val).strip().lower() or None


def merge_enrichment_back_to_rows(df: pd.DataFrame, business_enrichments: dict) -> pd.DataFrame:
    """
    Attach enrichment results (phones, emails, address, etc.) to each original row.

    IMPORTANT:
    - We match by company_search_name (which IS populated in your CSV)
      and fall back to company_normalized_key / raw_display_name / consumer.displayname.
    - This completely bypasses company_normalized_key when it's NaN, so data finally shows up.
    """
    logger.info("Step 6: Merging enrichment results back to rows...")

    # ---------------------------------------------------------
    # Build an index of enrichment results keyed by company name
    # ---------------------------------------------------------
    enrichment_index = {}

    for raw_key, biz in (business_enrichments or {}).items():
        # Prefer the explicit company_search_name stored in the enrichment
        key = (
            biz.get("company_search_name")
            or biz.get("company_normalized_key")
            or biz.get("raw_display_name")
            or raw_key
        )
        norm = _norm_key(key)
        if norm:
            enrichment_index[norm] = biz

    logger.info("  Built enrichment index for %d businesses", len(enrichment_index))

    enriched_rows = []

    # Ensure all expected enrichment columns exist in df
    enrichment_columns = [
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
        "enrichment_notes",
        "all_phones_json",
        "generic_emails_json",
        "person_emails_json",
        "catchall_emails_json",
        "source_platform",
    ]

    for col in enrichment_columns:
        if col not in df.columns:
            df[col] = None

    # ---------------------------------------------
    # Attach enrichment row-by-row using the index
    # ---------------------------------------------
    for _, row in df.iterrows():
        # Try a few candidates in order of "niceness"
        key = (
            row.get("company_search_name")
            or row.get("company_normalized_key")
            or row.get("raw_display_name")
            or row.get("consumer.displayname")
        )
        norm = _norm_key(key)
        biz = enrichment_index.get(norm, {}) if norm else {}

        # Simple helper: prefer enrichment value, fall back to existing df value
        def pick(col_name, enrichment_key=None):
            if not enrichment_key:
                enrichment_key = col_name
            val = biz.get(enrichment_key)
            return val if val not in (None, "", []) else row.get(col_name)

        # Domain + phone/email fields
        row["company_domain"] = pick("company_domain")
        row["domain_confidence"] = pick("domain_confidence")

        row["primary_phone"] = pick("primary_phone", "primary_phone")
        row["primary_phone_display"] = pick("primary_phone_display", "primary_phone_display")
        row["primary_phone_source"] = pick("primary_phone_source", "primary_phone_source")
        row["primary_phone_confidence"] = pick("primary_phone_confidence", "primary_phone_confidence")

        row["primary_email"] = pick("primary_email", "primary_email")
        row["primary_email_type"] = pick("primary_email_type", "primary_email_type")
        row["primary_email_source"] = pick("primary_email_source", "primary_email_source")
        row["primary_email_confidence"] = pick("primary_email_confidence", "primary_email_confidence")

        # Address fields
        row["business_address"] = pick("business_address")
        row["business_city"] = pick("business_city")
        row["business_state_region"] = pick("business_state_region")
        row["business_postal_code"] = pick("business_postal_code")
        row["business_country"] = pick("business_country")

        # OpenCorporates / company registry fields (if present)
        row["oc_company_name"] = pick("oc_company_name")
        row["oc_jurisdiction"] = pick("oc_jurisdiction")
        row["oc_company_number"] = pick("oc_company_number")
        row["oc_incorporation_date"] = pick("oc_incorporation_date")
        row["oc_match_confidence"] = pick("oc_match_confidence")

        # Overall/confidence + notes
        row["overall_lead_confidence"] = pick("overall_lead_confidence")
        row["enrichment_status"] = pick("enrichment_status")
        row["enrichment_notes"] = pick("enrichment_notes")
        row["source_platform"] = pick("source_platform")

        # Multi-value JSON fields (phones/emails arrays)
        all_phones = biz.get("all_phones")
        generic_emails = biz.get("generic_emails")
        person_emails = biz.get("person_emails")
        catchall_emails = biz.get("catchall_emails")

        if all_phones is not None:
            row["all_phones_json"] = json.dumps(all_phones, ensure_ascii=False)
        if generic_emails is not None:
            row["generic_emails_json"] = json.dumps(generic_emails, ensure_ascii=False)
        if person_emails is not None:
            row["person_emails_json"] = json.dumps(person_emails, ensure_ascii=False)
        if catchall_emails is not None:
            row["catchall_emails_json"] = json.dumps(catchall_emails, ensure_ascii=False)

        enriched_rows.append(row)

    out_df = pd.DataFrame(enriched_rows)
    logger.info("  Finished merging enrichment results into %d rows", len(out_df))
    return out_df


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
    Simplified enrichment function for a single business.

    Flow:
    1. Local enrichment (Google Places → Yelp) for phone/address/website
    2. Extract domain from website
    3. Email enrichment (Hunter → Snov → Apollo) for emails
    4. Compute overall confidence

    Args:
        name: Business name
        region: Optional region/location

    Returns:
        Dict with enriched business data
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

    # ---- LOCAL (Google → Yelp)
    local = local_enrichment.enrich_local_business(name, region)
    if local:
        if local.get("phone"):
            row["phone"] = local["phone"]
            row["primary_phone"] = local["phone"]

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

    # ---- EMAIL ENRICHMENT (Hunter → Snov → Apollo)
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

    # Build enrichment data structure for merge_results
    enrichment_data = {
        'company_search_name': company_name,
        'company_normalized_key': normalized_key,
        'local_enrichment': {
            'phone': enriched_data.get('phone'),
            'phone_source': 'google_places' if enriched_data.get('phone') else None,
            'address': enriched_data.get('address'),
            'city': enriched_data.get('city'),
            'state_region': enriched_data.get('state_region'),
            'postal_code': enriched_data.get('postal_code'),
            'country': enriched_data.get('country'),
            'website': enriched_data.get('website'),
        },
        'company_domain': enriched_data.get('domain'),
        'domain_confidence': 'high' if enriched_data.get('domain') else 'none',
        'primary_email': enriched_data.get('primary_email'),
        'email_source': enriched_data.get('email_source'),
        'emails': enriched_data.get('emails', []),
    }

    # Merge results and apply priority rules
    logger.debug(f"  -> Merging results for {company_name}")
    final_result = merge_enrichment_results(enrichment_data)

    # Save to cache
    cache.set(normalized_key, final_result)
    logger.info(f"  -> Completed enrichment for {company_name} (confidence: {final_result.get('overall_lead_confidence')})")
    return final_result
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
    df = merge_enrichment_back_to_rows(df, enrichment_results)
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
