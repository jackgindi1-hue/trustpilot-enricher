"""
Core enrichment pipeline - reusable function for CLI and API
Extracted from main.py to enable both CLI and API access
"""
import os
import json
import uuid
import datetime
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
from .phase2_final import email_waterfall_enrich, phase2_enrich

# PHASE 4: Phase 0 gating + website email scan + rate limiting
from .phase0_gating import should_run_phase2, should_run_opencorporates, domain_from_url, is_high_enough_for_skip
from .website_email_scan import micro_scan_for_email
from .entity_match import normalize_company_key
from .retry_ratelimit import SimpleRateLimiter, with_retry

logger = setup_logger(__name__)

# ============================================================
# PHASE 4 SAFE PATCH: Phase2/OpenCorporates export columns
# ============================================================
PHASE2_OC_EXPORT_COLS = [
    "phase2_bbb_phone", "phase2_bbb_email", "phase2_bbb_website", "phase2_bbb_names",
    "phase2_yp_phone", "phase2_yp_email", "phase2_yp_website", "phase2_yp_names",
    "oc_company_name", "oc_jurisdiction", "oc_company_number", "oc_incorporation_date", "oc_match_confidence",
]
_rate = SimpleRateLimiter(min_interval_s=0.2)

# ============================================================
# PHASE 4: Checkpoint configuration for partial CSV exports
# ============================================================
CHECKPOINT_EVERY = 250  # Write partial CSV every N businesses (250 = ~10-15 min chunks)

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

    # ============================================================
    # PHASE 4 SAFE PATCH: Add Phase2/OpenCorporates columns to merge
    # ============================================================
    for col in PHASE2_OC_EXPORT_COLS:
        if col not in wanted:
            wanted.append(col)

    logger.info(f"Added Phase2/OC columns to merge: {PHASE2_OC_EXPORT_COLS}")
    # ============================================================

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

def _write_checkpoint_csv(df: pd.DataFrame, partial_path: str, logger):
    """
    Write partial CSV checkpoint for recovery on error.
    PHASE 4: Enables "Download partial results" button in UI.

    Args:
        df: DataFrame with merged enrichment results
        partial_path: Path to write partial CSV (e.g., job_123.partial.csv)
        logger: Logger instance
    """
    try:
        from .io_utils import write_output_csv, get_output_schema
        output_schema = get_output_schema(df)
        write_output_csv(df, partial_path, output_schema)
        logger.info(f"✓ CHECKPOINT: Wrote partial CSV → {partial_path}")
    except Exception as e:
        logger.warning(f"✗ CHECKPOINT: Failed to write partial CSV: {e}")

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
    local = local_enrichment.enrich_local_business(name, region)
    # Store address/location data
    if local:
        for f in ["address", "city", "state_region", "postal_code", "country"]:
            if local.get(f):
                row[f] = local[f]
        if local.get("website"):
            row["website"] = local["website"]
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

    # PHASE 4: Phase 0 domain canonicalization (enforce early)
    if not row.get("domain") and website:
        d = domain_from_url(website)
        if d:
            row["domain"] = d
            logger.info(f"   -> Phase 0: Canonical domain={d} from website={website}")

    domain = row.get("domain")
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
    # ============================================================
    # WIRING EDIT 2 — EMAIL WATERFALL (STOP ON WINNER)
    # ============================================================
    logger.info(f"   -> Email enrichment (CONTINUE-ON-EMPTY - MAX COVERAGE) for {name} domain={domain}")
    wf = email_waterfall_enrich(company=name, domain=domain, person_name=None, logger=logger)
    primary_email = wf.get("primary_email")
    primary_email_source = wf.get("email_source")
    primary_email_confidence = wf.get("email_confidence")
    email_type = wf.get("email_type")
    email_providers_attempted = wf.get("email_tried") or ""

    logger.info(f"   -> Email waterfall complete: email={primary_email} source={primary_email_source} confidence={primary_email_confidence} tried={email_providers_attempted}")

    # Store into row
    row["primary_email"] = primary_email
    row["primary_email_source"] = primary_email_source
    row["primary_email_confidence"] = primary_email_confidence
    row["email_type"] = email_type
    row["email_providers_attempted"] = email_providers_attempted

    # PHASE 4: EMAIL-FIRST FALLBACK (website micro-scan if email still missing)
    if not row.get("primary_email") and website:
        try:
            _rate.wait("website_email_scan")
            logger.info(f"   -> Phase 4: Email missing, trying website micro-scan for {name}")
            e2 = micro_scan_for_email(website, logger=logger)
            if e2:
                row["primary_email"] = e2
                row["primary_email_source"] = "website_scan"
                row["email_type"] = "generic"
                row["primary_email_confidence"] = "low"
                logger.info(f"   -> Phase 4: Website scan found email={e2}")
        except Exception as _e:
            logger.warning(f"   -> Phase 4: Website scan failed: {repr(_e)}")

    # ============================================================
    # END WIRING EDIT 2
    # ============================================================
    # ============================================================
    # PHASE 2: Apply fallback enrichment for phone/website coverage (WITH GATING)
    # ============================================================
    from tp_enrich.phase2_enrichment import apply_phase2_fallbacks_v2

    # Build google_payload from local enrichment data
    google_payload = {
        "lat": local.get("lat") if local else None,
        "lng": local.get("lng") if local else None,
        "lon": local.get("lon") if local else None,
        "city": row.get("city"),
        "state_region": row.get("state_region"),
        "state": row.get("state_region"),
        "postal_code": row.get("postal_code"),
        "website": row.get("website"),
    }

    # PHASE 4: GATING LOGIC (Phase 2 = discovery/fallback only)
    # Build a temporary base_out dict to check if we should run Phase 2
    base_out_check = {
        "primary_phone": row.get("primary_phone"),
        "primary_phone_display": row.get("phone"),
        "company_domain": row.get("domain"),
        "domain": row.get("domain"),
        "primary_email": row.get("primary_email"),
        "business_website": row.get("website"),
    }

    if should_run_phase2(base_out_check, google_payload):
        logger.info(f"   -> PHASE 2 RUN | missing/weak anchors (fallback/discovery mode) for {name}")

        p2 = apply_phase2_fallbacks_v2(
            business_name=name,
            google_payload=google_payload,
            current_phone=row.get("primary_phone"),
            current_website=row.get("website"),
            logger=logger
        )
        # Apply phone fallback if we didn't have one
        if not row.get("primary_phone") and p2.get("normalized_phone"):
            row["primary_phone"] = p2["normalized_phone"]
            row["phone"] = p2["normalized_phone"]
            row["phone_source"] = p2.get("phone_source", "phase2_fallback")
            row["phone_confidence"] = p2.get("phone_confidence", "low")
            logger.info(f"   -> Phase 2 phone fallback: {p2['phone_final']} from {p2.get('phone_source')}")
        # Apply website fallback if we didn't have one
        if not row.get("website") and p2.get("website_final"):
            row["website"] = p2["website_final"]
            logger.info(f"   -> Phase 2 website fallback: {p2['website_final']}")

        # Store contact names extracted from BBB/YP/OC scrapers
        contact_names = p2.get("contact_names", [])
        if contact_names:
            row["phase2_bbb_names_json"] = str(contact_names)
            logger.info(f"   -> Phase 2 extracted {len(contact_names)} contact names: {contact_names}")
        else:
            row["phase2_bbb_names_json"] = "[]"

        # Add discovery URLs (optional but useful)
        row["phase2_bbb_link"] = p2.get("bbb_url")
        row["phase2_yp_link"] = p2.get("yp_url")
        row["yelp_url"] = p2.get("yelp_url")
        # Add OpenCorporates validation (optional)
        row["oc_company_number"] = p2.get("oc_company_number")
        row["oc_status"] = p2.get("oc_status")
        # Add notes for debugging
        row["phase2_notes"] = f"yelp:{p2.get('yelp_notes')} serp:{p2.get('serp_notes')} oc:{p2.get('oc_notes')}"
        logger.info(f"   -> Phase 2 fallbacks complete")

        # ============================================================
        # WIRING EDIT 3 — PHASE 2 ENRICH (DATA NOT URLS)
        # ============================================================
        try:
            logger.info(f"   -> Applying Phase 2 data enrichment (FINAL PATCH) for {name}")
            p2_data = phase2_enrich(company=name, google_payload=google_payload, logger=logger)

            # Merge Phase 2 data into row
            row.update(p2_data)

            logger.info(f"   -> Phase 2 data enrichment complete: bbb_phone={bool(p2_data.get('phase2_bbb_phone'))} yp_phone={bool(p2_data.get('phase2_yp_phone'))} notes={p2_data.get('phase2_notes')}")
        except Exception as e:
            logger.exception(f"Phase 2 data enrichment failed (non-fatal): {repr(e)}")
            # Set safe defaults so CSV doesn't break
            row["phase2_notes"] = f"exception_{repr(e)}"
    else:
        logger.info(f"   -> PHASE 2 SKIP | Phase 0 satisfied (phone+domain+email present) for {name}")
        # Set empty defaults so CSV doesn't break
        row["phase2_bbb_names_json"] = "[]"
        row["phase2_bbb_link"] = None
        row["phase2_yp_link"] = None
        row["yelp_url"] = None
        row["oc_company_number"] = None
        row["oc_status"] = None
        row["phase2_notes"] = "skipped_phase0_satisfied"
    # ============================================================
    # END WIRING EDIT 3
    # ============================================================
    row["confidence"] = _compute_confidence(row)
    return row

def enrich_business(business_info: Dict, cache: EnrichmentCache, run_id: str = None) -> Dict:
    """
    Enrich a single business through all sources
    Args:
        business_info: Business information dict
        cache: Enrichment cache
        run_id: Optional RUN_ID for tracing this enrichment run
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
    enriched_row = {
        # identity / keys
        "company_normalized_key": normalized_key,
        "company_search_name": company_name,
        "company_domain": enriched_data.get("domain"),
        "domain_confidence": "high" if enriched_data.get("domain") else "none",
        # primary phone
        "primary_phone": enriched_data.get("primary_phone"),
        "primary_phone_display": enriched_data.get("primary_phone"),
        "primary_phone_source": enriched_data.get("phone_source"),
        "primary_phone_confidence": enriched_data.get("phone_confidence"),
        # primary email
        "primary_email": enriched_data.get("primary_email"),
        "primary_email_type": "generic",
        "primary_email_source": enriched_data.get("primary_email_source") or enriched_data.get("email_source"),
        "primary_email_confidence": enriched_data.get("primary_email_confidence") or ("medium" if enriched_data.get("primary_email") else "none"),
        # address
        "business_address": enriched_data.get("address"),
        "business_city": enriched_data.get("city"),
        "business_state_region": enriched_data.get("state_region"),
        "business_postal_code": enriched_data.get("postal_code"),
        "business_country": enriched_data.get("country"),
        # metadata
        "overall_lead_confidence": enriched_data.get("confidence", "failed"),
        "enrichment_status": "completed",
        "enrichment_notes": "",
        # debug payloads
        "all_phones_json": enriched_data.get("all_phones_json", "{}"),
        "generic_emails_json": "[]",
        "person_emails_json": "[]",
        "catchall_emails_json": "[]",
        # PHASE 1 PROOF: waterfall tracking fields
        "email_providers_tried": enriched_data.get("email_providers_tried", ""),
        "email_provider_errors_json": enriched_data.get("email_provider_errors_json", "{}"),
        "email_waterfall_winner": enriched_data.get("email_waterfall_winner"),
        "source_platform": "trustpilot",
        # RUN_ID for tracing this enrichment run
        "run_id": run_id or "",
        # ============================================================
        # PHASE 4 SAFE PATCH: Phase2/OpenCorporates data mapping
        # ============================================================
        # BBB data (support both phase2_* and legacy bbb_* keys)
        "phase2_bbb_phone": enriched_data.get("phase2_bbb_phone") or enriched_data.get("bbb_phone") or "",
        "phase2_bbb_email": enriched_data.get("phase2_bbb_email") or enriched_data.get("bbb_email") or "",
        "phase2_bbb_website": enriched_data.get("phase2_bbb_website") or enriched_data.get("bbb_website") or "",
        "phase2_bbb_names": enriched_data.get("phase2_bbb_names") or enriched_data.get("bbb_names") or enriched_data.get("phase2_bbb_names_json") or "",
        # YellowPages data
        "phase2_yp_phone": enriched_data.get("phase2_yp_phone") or enriched_data.get("yp_phone") or "",
        "phase2_yp_email": enriched_data.get("phase2_yp_email") or enriched_data.get("yp_email") or "",
        "phase2_yp_website": enriched_data.get("phase2_yp_website") or enriched_data.get("yp_website") or "",
        "phase2_yp_names": enriched_data.get("phase2_yp_names") or enriched_data.get("yp_names") or enriched_data.get("phase2_yp_names_json") or "",
        # OpenCorporates data
        "oc_company_name": enriched_data.get("oc_company_name") or "",
        "oc_jurisdiction": enriched_data.get("oc_jurisdiction") or "",
        "oc_company_number": enriched_data.get("oc_company_number") or "",
        "oc_incorporation_date": enriched_data.get("oc_incorporation_date") or "",
        "oc_match_confidence": enriched_data.get("oc_match_confidence") or "",
    }

    # ============================================================
    # PHASE 4 PATCH: Phase2 Output Sanitizer (BBB/YP/OC)
    # Prevent Phase2 from polluting CSV with garbage values
    # ============================================================
    import re
    from urllib.parse import urlparse

    BAD_EMAIL_DOMAINS = {"bbb.org", "mybbb.org"}
    BAD_WEBSITE_DOMAINS = {
        "cdn.mouseflow.com", "mouseflow.com", "googletagmanager.com",
        "google-analytics.com", "doubleclick.net", "bbb.org",
        "www.bbb.org", "mybbb.org", "www.mybbb.org",
    }

    def _domain_of(url: str) -> str:
        try:
            u = (url or "").strip()
            if not u:
                return ""
            if not re.match(r"^https?://", u, re.I):
                u = "http://" + u
            return (urlparse(u).netloc or "").lower().strip()
        except Exception:
            return ""

    def _is_bad_email(email: str) -> bool:
        e = (email or "").strip().lower()
        if not e or "@" not in e:
            return False
        dom = e.split("@")[-1]
        return dom in BAD_EMAIL_DOMAINS

    def _is_bad_website(url: str) -> bool:
        d = _domain_of(url)
        if not d:
            return False
        if d in BAD_WEBSITE_DOMAINS:
            return True
        # block obvious tracker/script "websites"
        if any(x in d for x in ["mouseflow", "googletagmanager", "google-analytics", "doubleclick"]):
            return True
        return False

    def _looks_like_bbb_profile_phone(phone: str) -> bool:
        """
        BBB profile IDs look like 0443-91843895. Some parsers turn 4439184389 into a phone.
        We reject suspicious "phone" if it matches pattern derived from BBB profile IDs.
        """
        p = re.sub(r"\D+", "", (phone or ""))
        return len(p) == 10 and p.startswith(("443", "0443"))

    def _append_debug(base_out: dict, msg: str):
        key = "debug_notes" if "debug_notes" in base_out else ("enrichment_notes" if "enrichment_notes" in base_out else "debug_notes")
        cur = (base_out.get(key) or "").strip()
        if not cur:
            base_out[key] = msg
        else:
            if msg not in cur:
                base_out[key] = cur + " | " + msg

    # Sanitize BBB fields
    bbb_email = enriched_row.get("phase2_bbb_email") or ""
    bbb_site = enriched_row.get("phase2_bbb_website") or ""
    bbb_phone = enriched_row.get("phase2_bbb_phone") or ""
    bbb_names = enriched_row.get("phase2_bbb_names")

    changed = False

    # Blank BBB email if it's BBB-owned
    if _is_bad_email(bbb_email):
        enriched_row["phase2_bbb_email"] = ""
        _append_debug(enriched_row, "phase2_bbb_email_sanitized(bbb_domain)")
        changed = True

    # Blank BBB website if it's a tracker or BBB domain
    if _is_bad_website(bbb_site):
        enriched_row["phase2_bbb_website"] = ""
        _append_debug(enriched_row, "phase2_bbb_website_sanitized(tracker_or_bbb)")
        changed = True

    # Blank BBB names if it's just [] or empty list string
    if bbb_names in (None, "", "[]", [], {}, "none", "None"):
        enriched_row["phase2_bbb_names"] = ""
        if bbb_names == "[]":
            _append_debug(enriched_row, "phase2_bbb_names_empty")
            changed = True

    # If BBB email/website were junk AND phone looks like a profile-id phone, blank it too
    if (not enriched_row.get("phase2_bbb_email") and not enriched_row.get("phase2_bbb_website")) and _looks_like_bbb_profile_phone(bbb_phone):
        enriched_row["phase2_bbb_phone"] = ""
        _append_debug(enriched_row, "phase2_bbb_phone_sanitized(profile_id_artifact)")
        changed = True

    # Sanitize YP fields (light touch)
    yp_site = enriched_row.get("phase2_yp_website") or ""
    if _is_bad_website(yp_site):
        enriched_row["phase2_yp_website"] = ""
        _append_debug(enriched_row, "phase2_yp_website_sanitized(tracker)")
        changed = True

    # Prevent literal 'none' strings
    for k in [
        "phase2_bbb_phone", "phase2_bbb_email", "phase2_bbb_website", "phase2_bbb_names",
        "phase2_yp_phone", "phase2_yp_email", "phase2_yp_website", "phase2_yp_names",
        "oc_company_name", "oc_jurisdiction", "oc_company_number", "oc_incorporation_date", "oc_match_confidence",
    ]:
        v = enriched_row.get(k)
        if isinstance(v, str) and v.strip().lower() in ("none", "null", "nan"):
            enriched_row[k] = ""

    if changed:
        logger.info(f"  -> PHASE2 SANITIZER: cleaned BBB/YP outputs for {company_name}")
    # ============================================================
    # END PHASE 4 PATCH: Phase2 Output Sanitizer
    # ============================================================

    # Save to cache
    cache.set(normalized_key, enriched_row)
    logger.info(f"  -> Completed enrichment for {company_name} (confidence: {enriched_row.get('overall_lead_confidence')})")
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
    # ============================================================
    # RUN ID (trace one CSV run across logs + output)
    # ============================================================
    RUN_ID = f"{datetime.datetime.utcnow().isoformat()}_{uuid.uuid4().hex[:8]}"
    logger.info(f"RUN_ID={RUN_ID}")
    config = config or {}
    # Load input CSV
    logger.info("Step 1: Loading input CSV...")
    df = load_input_csv(input_csv_path)
    # Add row_id if not present
    if 'row_id' not in df.columns:
        df['row_id'] = range(1, len(df) + 1)

    # ============================================================
    # PATCH 1 — HOTFIX v3: Ensure raw_display_name populated + safer classification
    # ============================================================
    def _pick_first_existing_col(df, candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    # --- ensure raw_display_name exists + is filled ---
    if "raw_display_name" not in df.columns:
        src = _pick_first_existing_col(df, [
            "consumer.displayName",
            "displayName",
            "business_name",
            "businessName",
            "company",
            "company_name",
            "name",
            "Business Name",
            "Company",
        ])
        if src:
            df["raw_display_name"] = df[src]
        else:
            # last resort: first column
            df["raw_display_name"] = df.iloc[:, 0]

    # Fill NaNs / non-strings
    df["raw_display_name"] = df["raw_display_name"].fillna("").astype(str).str.strip()

    # If you have a "business display name" but it came in another column, backfill it
    alt = _pick_first_existing_col(df, ["consumer.displayName", "displayName"])
    if alt:
        m = df["raw_display_name"].eq("") & df[alt].notna()
        if m.any():
            df.loc[m, "raw_display_name"] = df.loc[m, alt].astype(str).str.strip()

    # --- classification hardening: business by default ---
    # Only classify as person when it's very likely a human name; otherwise business.
    import re

    _PERSON_RE = re.compile(r"^[A-Za-z]+(?:\s+[A-Za-z]+){0,2}$")  # "John" / "John Smith" / "John A Smith"
    _BUSINESS_HINTS = re.compile(
        r"\b(llc|inc|ltd|corp|co\.?|company|pllc|pc|lp|llp|construction|roof|roofing|plumbing|electric|hvac|transport|trucking|logistics|restaurant|cafe|bakery|fitness|gym|auto|detailing|repair|sewer|drain|florist|shop|store|market|bar|grill|salon|spa)\b",
        re.I,
    )

    def classify_name_sane(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return "business"   # for this pipeline, empty should not kill enrichment
        # very likely person (simple 1-3 word alpha name, no business hints)
        if _PERSON_RE.match(s) and not _BUSINESS_HINTS.search(s):
            return "person"
        return "business"

    # Classify each row
    logger.info("Step 2: Classifying display names (HOTFIX v3)...")
    df["name_classification"] = df["raw_display_name"].apply(classify_name_sane)
    classification_counts = df['name_classification'].value_counts()
    logger.info(f"  Classification results (HOTFIX v3): {dict(classification_counts)}")

    # If somehow everything is still "other" from older code paths, force business
    if (df["name_classification"] == "other").all():
        df["name_classification"] = "business"
        logger.warning("HOTFIX v3: All rows classified as 'other' — forced to 'business' to avoid empty pipeline.")
    # ============================================================
    # END PATCH 1 — HOTFIX v3
    # ============================================================
    # ============================================================
    # POST-CLASSIFICATION OVERRIDES (business pattern boosts)
    # ============================================================
    import re
    BUSINESS_KEYWORDS = [
        "llc", "inc", "ltd", "corp", "company", "co.", "co ",
        "plumbing", "roofing", "construction", "landscapes", "landscaping",
        "cosmetics", "detailing", "transport", "logistics", "trucking",
        "restaurant", "bistro", "sushi", "fitness", "academy", "institute",
        "services", "service", "shop", "auto", "glass", "appliance", "spa",
    ]
    def _looks_like_business(name: str) -> bool:
        s = (name or "").strip().lower()
        if not s:
            return False
        s2 = re.sub(r"[^a-z0-9]+", " ", s).strip()
        # glued suffixes like westerstatepainllc
        if re.search(r"(llc|inc|ltd|corp)$", s):
            return True
        for kw in BUSINESS_KEYWORDS:
            if kw in s2:
                return True
        # patterns like "X - Y" where Y contains a business noun
        if " - " in s2 or "-" in s:
            return any(kw in s2 for kw in ["llc", "inc", "plumbing", "appliance", "landscap", "cosmetic", "institute"])
        return False
    def _extract_company_search_name(raw_name: str) -> str:
        s = (raw_name or "").strip()
        # If format "Owner - Company", take the RHS as the company search name
        if " - " in s:
            left, right = s.split(" - ", 1)
            # If right side isn't empty, prefer it
            if right.strip():
                return right.strip()
        # If "Name, LLC" etc. leave as-is
        return s
    if "raw_display_name" in df.columns:
        raw_col = df["raw_display_name"].astype(str)
    else:
        # fall back to your display name column
        raw_col = df["consumer.displayname"].astype(str) if "consumer.displayname" in df.columns else df.iloc[:, 0].astype(str)
    mask = raw_col.apply(_looks_like_business)
    # Upgrade misclassified rows to business
    df.loc[mask, "name_classification"] = "business"
    # Ensure company_search_name is populated with best guess for search
    if "company_search_name" in df.columns:
        df.loc[mask, "company_search_name"] = raw_col[mask].apply(_extract_company_search_name)
    # ============================================================
    # END POST-CLASSIFICATION OVERRIDES
    # ============================================================
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

    # Get progress callback if provided
    progress_callback = config.get('progress_callback') if config else None
    total_businesses = len(unique_businesses)

    for idx, (normalized_key, business_info) in enumerate(unique_businesses.items(), 1):
        logger.info(f"  [{idx}/{total_businesses}] Processing: {business_info['company_search_name']}")

        # Report progress
        if progress_callback:
            try:
                progress_callback(idx, total_businesses)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

        try:
            result = enrich_business(business_info, cache, run_id=RUN_ID)
            enrichment_results[normalized_key] = result
        except Exception as e:
            logger.error(f"  Error enriching {business_info['company_search_name']}: {e}")
            enrichment_results[normalized_key] = {
                'enrichment_status': 'error',
                'enrichment_notes': str(e),
                'overall_lead_confidence': 'failed',
                'run_id': RUN_ID
            }

        # ============================================================
        # PHASE 4: Write checkpoint every N businesses (partial CSV)
        # ============================================================
        if idx % CHECKPOINT_EVERY == 0:
            partial_path = output_csv_path.replace(".enriched.csv", ".partial.csv")
            logger.info(f"  CHECKPOINT: {idx}/{total_businesses} businesses completed, writing partial CSV...")
            try:
                # Merge current enrichment results into df for checkpoint
                df_checkpoint = merge_enrichment_back_to_rows(df.copy(), list(enrichment_results.values()))
                _write_checkpoint_csv(df_checkpoint, partial_path, logger)
            except Exception as e:
                logger.warning(f"  CHECKPOINT: Failed to write partial CSV: {e}")
        # ============================================================

    # Save cache
    cache.save_cache()

    # ============================================================
    # PHASE 4: Write final checkpoint (ensures partial CSV exists even for small jobs)
    # ============================================================
    if enrichment_results:
        partial_path = output_csv_path.replace(".enriched.csv", ".partial.csv")
        logger.info(f"  CHECKPOINT (FINAL): Writing final partial CSV with {len(enrichment_results)} businesses...")
        try:
            df_final_checkpoint = merge_enrichment_back_to_rows(df.copy(), list(enrichment_results.values()))
            _write_checkpoint_csv(df_final_checkpoint, partial_path, logger)
        except Exception as e:
            logger.warning(f"  CHECKPOINT (FINAL): Failed to write final partial CSV: {e}")
    # ============================================================
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
    # ============================================================
    # PROVIDER SCOREBOARD (WINS BY SOURCE) - SAFE, NO API CALLS
    # ============================================================
    try:
        # Email winners
        if "primary_email" in df.columns:
            email_populated = df[df["primary_email"].notna() & (df["primary_email"].astype(str).str.strip() != "")]
            if "primary_email_source" in df.columns:
                email_wins = email_populated["primary_email_source"].fillna("unknown").astype(str).str.lower().value_counts()
                logger.info(f"EMAIL WINNERS (primary_email_source): {email_wins.to_dict()}")
            else:
                logger.info("EMAIL WINNERS: primary_email_source column missing")
            logger.info(f"EMAIL POPULATED ROWS: {len(email_populated)}/{len(df)}")
        else:
            logger.info("EMAIL WINNERS: primary_email column missing")
        # Phone winners
        if "primary_phone" in df.columns:
            phone_populated = df[df["primary_phone"].notna() & (df["primary_phone"].astype(str).str.strip() != "")]
            if "primary_phone_source" in df.columns:
                phone_wins = phone_populated["primary_phone_source"].fillna("unknown").astype(str).str.lower().value_counts()
                logger.info(f"PHONE WINNERS (primary_phone_source): {phone_wins.to_dict()}")
            else:
                logger.info("PHONE WINNERS: primary_phone_source column missing")
            logger.info(f"PHONE POPULATED ROWS: {len(phone_populated)}/{len(df)}")
        else:
            logger.info("PHONE WINNERS: primary_phone column missing")
    except Exception as e:
        logger.warning(f"Provider scoreboard logging failed: {e}")
    # ============================================================
    # END PROVIDER SCOREBOARD
    # ============================================================
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
