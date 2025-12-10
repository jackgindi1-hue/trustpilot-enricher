"""
Core enrichment pipeline - reusable function for CLI and API
Extracted from main.py to enable both CLI and API access
"""

import os
import pandas as pd
from typing import Dict, Optional

from .logging_utils import setup_logger
from .io_utils import load_input_csv, write_output_csv, get_output_schema
from .classification import classify_name
from .normalization import normalize_business_name, ensure_company_search_name
from .dedupe import identify_unique_businesses, get_enrichment_context
from .cache import EnrichmentCache
from .domain_enrichment import discover_domain
from .local_enrichment import enrich_local_sources
from .legal_enrichment import enrich_from_opencorporates
from .email_enrichment import enrich_emails
from .social_enrichment import enrich_from_social
from .merge_results import merge_enrichment_results

logger = setup_logger(__name__)


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

    logger.info(f"Enriching business: {company_name}")

    # Check cache
    if cache.has(normalized_key):
        logger.info(f"  -> Using cached result for {company_name}")
        return cache.get(normalized_key)

    # Get enrichment context
    context = get_enrichment_context(business_info)

    enrichment_data = {
        'company_search_name': company_name,
        'company_normalized_key': normalized_key
    }

    # Domain enrichment
    logger.debug(f"  -> Domain enrichment for {company_name}")
    domain, domain_confidence = discover_domain(company_name, context)
    enrichment_data['company_domain'] = domain
    enrichment_data['domain_confidence'] = domain_confidence

    # Local enrichment (Google Maps, Yelp, YP/BBB)
    logger.debug(f"  -> Local enrichment for {company_name}")
    local_data = enrich_local_sources(company_name, context)
    enrichment_data.update(local_data)

    # Legal enrichment (OpenCorporates)
    logger.debug(f"  -> Legal enrichment for {company_name}")
    legal_data = enrich_from_opencorporates(company_name, context)
    enrichment_data.update(legal_data)

    # Email enrichment
    logger.debug(f"  -> Email enrichment for {company_name}")
    email_data = enrich_emails(domain)
    enrichment_data.update(email_data)

    # Social enrichment
    logger.debug(f"  -> Social enrichment for {company_name}")
    website_urls = [
        enrichment_data.get('maps_website'),
        enrichment_data.get('yelp_website'),
        enrichment_data.get('company_domain')
    ]
    social_data = enrich_from_social(website_urls)
    enrichment_data.update(social_data)

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

    # Merge back to rows
    logger.info("Step 6: Merging enrichment results back to rows...")

    # Create enrichment columns
    enrichment_cols = [
        'company_domain', 'domain_confidence',
        'primary_phone', 'primary_phone_display', 'primary_phone_source', 'primary_phone_confidence',
        'primary_email', 'primary_email_type', 'primary_email_source', 'primary_email_confidence',
        'business_address', 'business_city', 'business_state_region', 'business_postal_code', 'business_country',
        'oc_company_name', 'oc_jurisdiction', 'oc_company_number', 'oc_incorporation_date', 'oc_match_confidence',
        'overall_lead_confidence', 'enrichment_status', 'enrichment_notes',
        'all_phones_json', 'generic_emails_json', 'person_emails_json', 'catchall_emails_json'
    ]

    # Initialize enrichment columns
    for col in enrichment_cols:
        df[col] = None

    # Merge enrichment data for business rows
    for idx, row in df[business_mask].iterrows():
        normalized_key = row['company_normalized_key']

        if normalized_key in enrichment_results:
            result = enrichment_results[normalized_key]

            for col in enrichment_cols:
                if col in result:
                    df.at[idx, col] = result[col]

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
