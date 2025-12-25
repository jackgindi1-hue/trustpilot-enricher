# ============================================================
# CANONICAL ENRICHMENT — PHASE 4.5 FINAL ARCHITECTURE
#
# Flow:
# 1. Get Google + Yelp candidates
# 2. Entity match chooses ONE canonical (≥80%)
# 3. Use canonical anchors for phone/email
# 4. OpenCorporates ONLY if state known
# 5. Phase 2 discovery uses canonical data
# ============================================================

from typing import Dict, Any, Optional
from tp_enrich.canonical import (
    choose_canonical_business,
    apply_canonical_to_row,
    should_run_opencorporates,
)
from tp_enrich import local_enrichment
from tp_enrich.phone_enrichment import enrich_business_phone_waterfall
from tp_enrich.phase2_final import email_waterfall_enrich, phase2_enrich
from tp_enrich.website_email_scan import micro_scan_for_email
from tp_enrich.retry_ratelimit import SimpleRateLimiter
from tp_enrich.phase0_gating import domain_from_url

_rate = SimpleRateLimiter(min_interval_s=0.2)


def enrich_single_business_canonical(
    name: str,
    region: Optional[str] = None,
    logger=None
) -> Dict[str, Any]:
    """
    PHASE 4.5 CANONICAL ENRICHMENT

    One canonical business decision per row.
    All providers must pass entity_match ≥ 80%.
    Deterministic, auditable, no guessing.

    Args:
        name: Business name
        region: Optional region/state
        logger: Optional logger

    Returns:
        Enriched business dict with canonical source tracking
    """
    if logger:
        logger.info(f"Enriching business (canonical): {name}")

    row = {
        "business_name": name,
        "primary_phone": None,
        "primary_email": None,
        "business_website": None,
        "business_domain": None,
        "business_address": None,
        "business_city": None,
        "business_state_region": region,
        "business_postal_code": None,
        "business_country": None,
        "canonical_source": None,
        "canonical_match_score": 0.0,
        "debug_notes": "",
    }

    # ============================================================
    # (1) GET CANDIDATES: Google + Yelp
    # ============================================================
    google_hit = None
    yelp_hit = None

    # Google Places lookup
    try:
        google_hit = local_enrichment.enrich_local_business(name, region)
        if logger and google_hit:
            logger.info(f"   -> Google Places: name={google_hit.get('name')} state={google_hit.get('state_region')}")
    except Exception as e:
        if logger:
            logger.warning(f"   -> Google Places failed: {e}")

    # Yelp lookup (optional - if you have Yelp integration)
    # try:
    #     yelp_hit = yelp_enrichment.search_business(name, region)
    #     if logger and yelp_hit:
    #         logger.info(f"   -> Yelp: name={yelp_hit.get('name')} state={yelp_hit.get('state')}")
    # except Exception as e:
    #     if logger:
    #         logger.warning(f"   -> Yelp failed: {e}")

    # ============================================================
    # (2) ENTITY MATCH — CHOOSE ONE CANONICAL (≥80%)
    # ============================================================
    canonical, match_meta = choose_canonical_business(row, google_hit, yelp_hit)

    if canonical:
        if logger:
            logger.info(
                f"   -> CANONICAL: {canonical['source']} (score={match_meta['best_score']:.2f})"
            )
        row = apply_canonical_to_row(row, canonical, match_meta)
    else:
        if logger:
            logger.info(
                f"   -> CANONICAL: No match (reason={match_meta.get('reason')})"
            )
        row["debug_notes"] += "|entity_match_below_80"
        row["canonical_source"] = ""
        row["canonical_match_score"] = 0.0
        row["canonical_match_reason"] = match_meta.get("reason", "below_threshold_0.8")

        # 🔥 PHASE 4.5.4 GATEKEEPER: NO FALLBACK BYPASS
        # IMPORTANT: Do NOT merge google/yelp data when below threshold
        # This enforces the entity matching gatekeeper at ≥80%
        if logger:
            logger.info("   -> Gatekeeper: Rejecting providers (below 80% threshold)")

    # ============================================================
    # (3) PHONE WATERFALL — Use canonical anchors
    # ============================================================
    domain = row.get("business_domain")
    website = row.get("business_website")

    phone_layer = enrich_business_phone_waterfall(
        biz_name=name,
        google_hit=google_hit or {},
        domain=domain
    )

    row["primary_phone"] = phone_layer.get("primary_phone")
    row["primary_phone_source"] = phone_layer.get("primary_phone_source")
    row["primary_phone_confidence"] = phone_layer.get("primary_phone_confidence")
    row["all_phones_json"] = phone_layer.get("all_phones_json")

    # ============================================================
    # (4) EMAIL WATERFALL — Use canonical domain
    # ============================================================
    if logger:
        logger.info(f"   -> Email enrichment for {name} domain={domain}")

    wf = email_waterfall_enrich(company=name, domain=domain, person_name=None, logger=logger)

    row["primary_email"] = wf.get("primary_email")
    row["primary_email_source"] = wf.get("email_source")
    row["primary_email_confidence"] = wf.get("email_confidence")
    row["email_type"] = wf.get("email_type")
    row["email_providers_attempted"] = wf.get("email_tried") or ""

    # Website micro-scan fallback
    if not row.get("primary_email") and website:
        try:
            _rate.wait("website_email_scan")
            if logger:
                logger.info(f"   -> Website scan for email: {website}")
            e2 = micro_scan_for_email(website, logger=logger)
            if e2:
                row["primary_email"] = e2
                row["primary_email_source"] = "website_scan"
                row["email_type"] = "generic"
                row["primary_email_confidence"] = "low"
                if logger:
                    logger.info(f"   -> Website scan found: {e2}")
        except Exception as e:
            if logger:
                logger.warning(f"   -> Website scan failed: {e}")

    # ============================================================
    # (5) OPEN CORPORATES — HARD STATE GUARD
    # ============================================================
    if should_run_opencorporates(row):
        state = row["business_state_region"]
        if logger:
            logger.info(f"   -> OpenCorporates lookup: {name} in {state}")

        try:
            # Build google_payload for Phase 2
            google_payload = {
                "lat": google_hit.get("lat") if google_hit else None,
                "lng": google_hit.get("lng") if google_hit else None,
                "city": row.get("business_city"),
                "state_region": state,
                "state": state,
                "postal_code": row.get("business_postal_code"),
                "website": website,
            }

            # Phase 2 enrichment (includes OpenCorporates)
            p2_data = phase2_enrich(company=name, google_payload=google_payload, logger=logger)

            # Merge Phase 2 data
            row.update(p2_data)

            if logger:
                logger.info(
                    f"   -> Phase 2 complete: bbb={bool(p2_data.get('phase2_bbb_phone'))} "
                    f"yp={bool(p2_data.get('phase2_yp_phone'))} "
                    f"oc={bool(p2_data.get('oc_company_number'))}"
                )
        except Exception as e:
            if logger:
                logger.exception(f"   -> Phase 2 failed: {e}")
            row["debug_notes"] += f"|phase2_error_{repr(e)[:50]}"
    else:
        if logger:
            logger.info(f"   -> OpenCorporates SKIPPED (no state)")
        row["debug_notes"] += "|oc_skipped_no_state"

    # ============================================================
    # (6) COMPUTE CONFIDENCE
    # ============================================================
    has_phone = bool(row.get("primary_phone"))
    has_email = bool(row.get("primary_email"))

    if has_phone and has_email:
        row["overall_lead_confidence"] = "high"
    elif has_phone or has_email:
        row["overall_lead_confidence"] = "medium"
    else:
        row["overall_lead_confidence"] = "failed"

    return row
