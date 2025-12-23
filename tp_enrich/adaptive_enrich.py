# ============================================================
# PHASE 4.6 — ADAPTIVE ENRICHMENT WATERFALL
#
# GOAL: Start with business_name (possibly broken) and return MAX correct
# contact data using ALL sources with anchor discovery.
#
# Flow:
# 0. Start with any existing row anchors (state/city/domain/phone)
# 1. Try Google Places (if name + state/city exists)
# 2. Try Yelp (same)
# 3. If weak candidates or missing anchors → run anchor discovery
# 4. If discovery found new anchors:
#    - If discovered_domain: run Hunter/Apollo on domain
#    - If discovered_phone/address/state: retry Google/Yelp with stronger query
# 5. Run Phase 4.5 canonical matcher (≥80%)
#    - If pass: apply canonical + continue full enrichment
#    - If fail: Keep discovered_* fields with evidence (NO empty row)
# 6. Always merge results with source tags and evidence URLs
# ============================================================

from typing import Dict, Any, Optional
from tp_enrich.anchor_discovery import phase46_anchor_discovery
from tp_enrich.canonical import choose_canonical_business, apply_canonical_to_row, should_run_opencorporates
from tp_enrich import local_enrichment
from tp_enrich.phone_enrichment import enrich_business_phone_waterfall
from tp_enrich.phase2_final import email_waterfall_enrich, phase2_enrich
from tp_enrich.website_email_scan import micro_scan_for_email
from tp_enrich.retry_ratelimit import SimpleRateLimiter
from tp_enrich.phase0_gating import domain_from_url

_rate = SimpleRateLimiter(min_interval_s=0.2)


def enrich_single_business_adaptive(
    name: str,
    region: Optional[str] = None,
    logger=None
) -> Dict[str, Any]:
    """
    PHASE 4.6 ADAPTIVE ENRICHMENT

    Adaptive waterfall that discovers anchors when canonical matching fails.
    Returns MAX contact data even for "rejected" rows.

    Args:
        name: Business name
        region: Optional region/state
        logger: Optional logger

    Returns:
        Enriched business dict with canonical + discovered fields
    """
    if logger:
        logger.info(f"Enriching business (adaptive): {name}")

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
        # PHASE 4.6: Discovered anchors
        "discovered_domain": None,
        "discovered_phone": None,
        "discovered_state_region": None,
        "discovered_address": None,
        "discovered_email": None,
        "discovered_evidence_url": None,
        "discovered_evidence_source": None,
        "discovery_evidence_json": "[]",
    }

    # ============================================================
    # STEP 0: Start with existing anchors
    # ============================================================
    has_state = bool(region)
    has_domain = False
    has_phone = False

    # ============================================================
    # STEP 1: Try Google Places (if name + state/city exists)
    # ============================================================
    google_hit = None
    yelp_hit = None

    if has_state or region:
        try:
            google_hit = local_enrichment.enrich_local_business(name, region)
            if logger and google_hit:
                logger.info(f"   -> Google Places: name={google_hit.get('name')} state={google_hit.get('state_region')}")
        except Exception as e:
            if logger:
                logger.warning(f"   -> Google Places failed: {e}")
    else:
        if logger:
            logger.info("   -> Skipping Google Places (no state/city anchor)")

    # ============================================================
    # STEP 2: Try Yelp (same logic)
    # ============================================================
    # Yelp integration optional - skip for now

    # ============================================================
    # STEP 3: Check if we have weak candidates or missing anchors
    # ============================================================
    has_candidates = bool(google_hit or yelp_hit)

    if not has_candidates:
        if logger:
            logger.info("   -> No candidates from Google/Yelp, triggering anchor discovery")

        # ============================================================
        # STEP 4: Run anchor discovery
        # ============================================================
        try:
            discovered = phase46_anchor_discovery(name, vertical=None, max_urls=3, logger=logger)

            # Merge discovered anchors into row
            row["discovered_domain"] = discovered.get("discovered_domain")
            row["discovered_phone"] = discovered.get("discovered_phone")
            row["discovered_state_region"] = discovered.get("discovered_state_region")
            row["discovered_address"] = discovered.get("discovered_address")
            row["discovered_email"] = discovered.get("discovered_email")
            row["discovered_evidence_url"] = discovered.get("discovered_evidence_url")
            row["discovered_evidence_source"] = discovered.get("discovered_evidence_source")
            row["discovery_evidence_json"] = discovered.get("discovery_evidence_json")

            # Update flags
            if discovered.get("discovered_domain"):
                has_domain = True
                row["business_domain"] = discovered["discovered_domain"]

            if discovered.get("discovered_phone"):
                has_phone = True
                row["primary_phone"] = discovered["discovered_phone"]
                row["primary_phone_source"] = "anchor_discovery"
                row["primary_phone_confidence"] = "medium"

            if discovered.get("discovered_state_region"):
                has_state = True
                row["business_state_region"] = discovered["discovered_state_region"]

            if discovered.get("discovered_address"):
                row["business_address"] = discovered["discovered_address"]

            if discovered.get("discovered_email"):
                row["primary_email"] = discovered["discovered_email"]
                row["primary_email_source"] = "anchor_discovery"
                row["primary_email_confidence"] = "low"

            if logger:
                logger.info(
                    f"   -> Anchor discovery complete: domain={bool(has_domain)}, "
                    f"phone={bool(has_phone)}, state={bool(has_state)}"
                )

            # ============================================================
            # STEP 5: Retry providers with discovered anchors
            # ============================================================
            if has_state and not google_hit:
                # Retry Google Places with discovered state
                try:
                    state = row["business_state_region"]
                    google_hit = local_enrichment.enrich_local_business(name, state)
                    if logger and google_hit:
                        logger.info(f"   -> Google Places (retry with state): SUCCESS")
                except Exception as e:
                    if logger:
                        logger.warning(f"   -> Google Places (retry) failed: {e}")

        except Exception as e:
            if logger:
                logger.exception(f"   -> Anchor discovery failed: {e}")
            row["debug_notes"] += f"|anchor_discovery_error_{repr(e)[:50]}"

    # ============================================================
    # STEP 6: Canonical matching (≥80%)
    # ============================================================
    canonical, match_meta = choose_canonical_business(row, google_hit, yelp_hit)

    if canonical:
        if logger:
            logger.info(
                f"   -> CANONICAL: {canonical['source']} (score={match_meta['best_score']:.2f})"
            )
        row = apply_canonical_to_row(row, canonical, match_meta)

        # ============================================================
        # STEP 7: Full enrichment (phone/email waterfalls)
        # ============================================================
        domain = row.get("business_domain")
        website = row.get("business_website")

        # Phone waterfall
        if not row.get("primary_phone"):
            phone_layer = enrich_business_phone_waterfall(
                biz_name=name,
                google_hit=google_hit or {},
                domain=domain
            )

            row["primary_phone"] = phone_layer.get("primary_phone")
            row["primary_phone_source"] = phone_layer.get("primary_phone_source")
            row["primary_phone_confidence"] = phone_layer.get("primary_phone_confidence")
            row["all_phones_json"] = phone_layer.get("all_phones_json")

        # Email waterfall
        if not row.get("primary_email"):
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
            except Exception as e:
                if logger:
                    logger.warning(f"   -> Website scan failed: {e}")

        # OpenCorporates (if state known)
        if should_run_opencorporates(row):
            state = row["business_state_region"]
            if logger:
                logger.info(f"   -> OpenCorporates lookup: {name} in {state}")

            try:
                google_payload = {
                    "lat": google_hit.get("lat") if google_hit else None,
                    "lng": google_hit.get("lng") if google_hit else None,
                    "city": row.get("business_city"),
                    "state_region": state,
                    "state": state,
                    "postal_code": row.get("business_postal_code"),
                    "website": website,
                }

                p2_data = phase2_enrich(company=name, google_payload=google_payload, logger=logger)
                row.update(p2_data)

                if logger:
                    logger.info(f"   -> Phase 2 complete")
            except Exception as e:
                if logger:
                    logger.exception(f"   -> Phase 2 failed: {e}")
                row["debug_notes"] += f"|phase2_error_{repr(e)[:50]}"
        else:
            if logger:
                logger.info(f"   -> OpenCorporates SKIPPED (no state)")
            row["debug_notes"] += "|oc_skipped_no_state"

    else:
        # ============================================================
        # CANONICAL REJECTED - Keep discovered data (NO empty row)
        # ============================================================
        if logger:
            logger.info(
                f"   -> CANONICAL: Rejected (reason={match_meta.get('reason')})"
            )

        row["canonical_source"] = ""
        row["canonical_match_score"] = 0.0
        row["canonical_match_reason"] = match_meta.get("reason", "below_threshold_0.8")
        row["debug_notes"] += "|entity_match_below_80"

        # IMPORTANT: Keep discovered_* fields even if canonical failed
        # This ensures "rejected" rows still have useful data
        if logger:
            logger.info(
                f"   -> Keeping discovered data: domain={bool(row.get('discovered_domain'))}, "
                f"phone={bool(row.get('discovered_phone'))}, "
                f"email={bool(row.get('discovered_email'))}"
            )

    # ============================================================
    # STEP 8: Compute confidence
    # ============================================================
    has_phone = bool(row.get("primary_phone") or row.get("discovered_phone"))
    has_email = bool(row.get("primary_email") or row.get("discovered_email"))

    if has_phone and has_email:
        row["overall_lead_confidence"] = "high"
    elif has_phone or has_email:
        row["overall_lead_confidence"] = "medium"
    else:
        row["overall_lead_confidence"] = "failed"

    return row
