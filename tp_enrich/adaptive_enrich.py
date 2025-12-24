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
# 5. Run Phase 4.5 canonical matcher (≥80%)
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
from tp_enrich.email_enrichment import assign_email  # PHASE 4.6.2
from tp_enrich.retry_ratelimit import timed  # PHASE 4.6.3
_rate = SimpleRateLimiter(min_interval_s=0.2)

def _pick_email_domain(row: dict) -> str:
    """PHASE 4.6.2: Pick best domain for email enrichment."""
    for k in ["company_domain", "business_domain", "canonical_domain", "discovered_domain"]:
        d = (row.get(k) or "").strip()
        if d:
            d = d.replace("http://", "").replace("https://", "").replace("www.", "").split("/")[0]
            return d
    return ""

# ============================================================
# PHASE 4.6.3 — EMAIL SPEED + COVERAGE GUARD
# ============================================================

DIRECTORY_DOMAINS = {
    "yelp.com", "bbb.org", "yellowpages.com",
    "brokersnapshot.com", "opencorporates.com",
    "zoominfo.com", "bizapedia.com"
}

def _domain_is_directory(domain: str) -> bool:
    """Check if domain is a directory/aggregator site."""
    d = (domain or "").lower()
    return any(d.endswith(x) for x in DIRECTORY_DOMAINS)

def _should_run_full_email(row: dict) -> bool:
    """
    PHASE 4.6.3: Only run full email waterfall when we have real signal.
    This prevents slow + empty runs on directory domains or missing anchors.

    Returns:
        True if we should run the full waterfall, False to skip
    """
    domain = (row.get("company_domain") or row.get("discovered_domain") or "").lower()
    phone = (row.get("primary_phone") or row.get("discovered_phone") or "").strip()
    html_flag = row.get("website_has_mailto")  # set earlier if available

    if not domain:
        return False

    if _domain_is_directory(domain):
        return False

    # Run if we have phone or mailto signal
    if phone:
        return True

    if html_flag:
        return True

    return False

def _promote_discovered_phone(row: dict, logger=None) -> dict:
    """
    PHASE 4.6.4: Promote discovered_phone to primary_phone if primary is empty.

    This ensures discovered phones are not lost when phone waterfall fails.
    """
    primary = (row.get("primary_phone") or "").strip()
    disc = (row.get("discovered_phone") or "").strip()

    if (not primary) and disc:
        row["primary_phone"] = disc
        row["primary_phone_display"] = disc
        row["primary_phone_source"] = row.get("discovered_evidence_source") or "discovery"
        # Don't claim high confidence for discovered-only phones
        if not row.get("primary_phone_confidence"):
            row["primary_phone_confidence"] = "medium"

        if logger:
            logger.info(f"   -> PHONE PROMOTION: Using discovered_phone={disc} (primary was empty)")

    return row

def _run_email_step(name: str, row: dict, logger=None) -> dict:
    """
    PHASE 4.6.3/4.6.4: ALWAYS run email enrichment when ANY domain exists.

    Critical fix: Email must run even when canonical matching fails (<80%).
    This ensures max email coverage using discovered OR canonical domains.

    Phase 4.6.3 adds guard to skip slow/empty waterfalls on directory domains.

    Uses assign_email() to preserve directory emails as secondary.
    """
    email_domain = _pick_email_domain(row)

    if not email_domain:
        if logger:
            logger.info("   -> EMAIL: Skipped (no domain anchor present)")
        return row

    # REQUIRED REGRESSION LOG (Phase 4.6.4)
    canonical_source = (row.get("canonical_source") or "").strip()
    discovered_domain = (row.get("discovered_domain") or "").strip()

    if not canonical_source and discovered_domain:
        if logger:
            logger.info("CANONICAL rejected; still running email due to discovered_domain")

    # PHASE 4.6.3: Guard against slow/empty waterfalls
    if not _should_run_full_email(row):
        if logger:
            logger.info(
                f"   -> EMAIL: Skipping full waterfall (directory domain or no signal) domain={email_domain}"
            )

        # Still preserve directory emails even if we skip waterfall
        for dsrc, col in [
            ("bbb", "phase2_bbb_email"),
            ("yp", "phase2_yp_email"),
            ("discovery", "discovered_email")
        ]:
            ev = (row.get(col) or "").strip()
            if ev:
                assign_email(row, ev, source=dsrc)

        return row

    if logger:
        logger.info(
            f"   -> EMAIL: Running FULL waterfall domain={email_domain} "
            f"(canonical_source={canonical_source or 'none'} score={row.get('canonical_match_score', 0.0):.2f})"
        )

    try:
        # Run email waterfall with best available domain
        done = timed(logger, "EMAIL_WATERFALL")

        wf = email_waterfall_enrich(
            company=name,
            domain=email_domain,
            person_name=None,
            logger=logger
        )

        done(f"domain={email_domain}")

        # Collect ALL found emails and route through assign_email
        found = []

        if isinstance(wf, dict):
            # Primary email from waterfall
            pe = (wf.get("primary_email") or "").strip()
            if pe:
                found.append(("waterfall_primary", pe))

            # Additional emails from lists
            for lk in ["emails", "all_emails", "found_emails"]:
                vals = wf.get(lk)
                if isinstance(vals, list):
                    for e in vals:
                        e = (e or "").strip()
                        if e:
                            found.append((lk, e))

            # Provider-specific emails
            src_map = wf.get("by_source") or wf.get("provider_emails")
            if isinstance(src_map, dict):
                for src, vals in src_map.items():
                    if isinstance(vals, list):
                        for e in vals:
                            e = (e or "").strip()
                            if e:
                                found.append((str(src), e))

        # Route all emails through assign_email (dedup + directory preservation)
        seen = set()
        for src, e in found:
            k = e.lower()
            if k in seen:
                continue
            seen.add(k)
            # ✅ CRITICAL: assign_email() preserves directory emails as secondary
            assign_email(row, e, source=str(src))

        # Also preserve explicit directory emails (don't lose them)
        for dsrc, col in [
            ("bbb", "phase2_bbb_email"),
            ("yp", "phase2_yp_email"),
            ("discovery", "discovered_email")
        ]:
            ev = (row.get(col) or "").strip()
            if ev:
                assign_email(row, ev, source=dsrc)

        # Update metadata
        if row.get("primary_email"):
            row["primary_email_confidence"] = wf.get("email_confidence") or row.get("primary_email_confidence")
            row["email_type"] = wf.get("email_type") or row.get("email_type")
            row["email_providers_attempted"] = wf.get("email_tried") or ""

            if logger:
                logger.info(
                    f"   -> EMAIL: SUCCESS {row['primary_email']} "
                    f"(source={row.get('primary_email_source')})"
                )

    except Exception as ex:
        if logger:
            logger.warning(f"   -> EMAIL: Waterfall failed domain={email_domain} err={repr(ex)}")

    return row

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

    # Try anchored Google Places if we have state/city
    if has_state or region:
        try:
            google_hit = local_enrichment.enrich_local_business(name, region)
            if logger and google_hit:
                logger.info(f"   -> Google Places (anchored): name={google_hit.get('name')} state={google_hit.get('state_region')}")
        except Exception as e:
            if logger:
                logger.warning(f"   -> Google Places (anchored) failed: {e}")

    # PHASE 4.6.3: Scout mode fallback (name-only) if no hit yet
    if not google_hit:
        try:
            import os
            google_key = os.getenv("GOOGLE_PLACES_API_KEY")
            if google_key:
                if logger:
                    logger.info(f"   -> GOOGLE SCOUT: Trying name-only lookup for '{name}'")
                google_hit = local_enrichment.google_places_scout_by_name(name, google_key)
                if logger and google_hit:
                    logger.info(f"   -> GOOGLE SCOUT SUCCESS: name={google_hit.get('name')} state={google_hit.get('state_region')} place_id={google_hit.get('place_id')}")
        except Exception as e:
            if logger:
                logger.warning(f"   -> Google scout mode failed: {e}")
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
            # PHASE 4.6.4: Reduced max_urls from 3 to 2 for speed
            discovered = phase46_anchor_discovery(name, vertical=None, max_urls=2, logger=logger)
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
                # PHASE 4.6.4: Don't directly assign - let phone waterfall try first,
                # then _promote_discovered_phone() will use it if waterfall fails
            if discovered.get("discovered_state_region"):
                has_state = True
                row["business_state_region"] = discovered["discovered_state_region"]
            if discovered.get("discovered_address"):
                row["business_address"] = discovered["discovered_address"]
            if discovered.get("discovered_email"):
                assign_email(row, discovered["discovered_email"], source="anchor_discovery")
            if logger:
                logger.info(
                    f"   -> Anchor discovery complete: domain={bool(has_domain)}, "
                    f"phone={bool(has_phone)}, state={bool(has_state)}"
                )
            # ============================================================
            # STEP 5: FEEDBACK LOOP - Retry providers with discovered anchors
            # ============================================================
            # 5A. Retry Google Places with discovered state/phone
            if (has_state or has_phone) and not google_hit:
                if logger:
                    logger.info("   -> FEEDBACK: Retrying Google Places with discovered anchors")
                try:
                    # Build better query with discovered anchors
                    query_name = name
                    query_region = row.get("business_state_region") or region
                    # If we have discovered phone, add it to search
                    if has_phone:
                        discovered_phone = row.get("discovered_phone")
                        if logger:
                            logger.info(f"   -> Retrying Google Places: {query_name} + phone={discovered_phone}")
                        # Try with phone-enhanced query
                        google_hit = local_enrichment.enrich_local_business(
                            f"{query_name} {discovered_phone}",
                            query_region
                        )
                    # If still no hit and we have state, try with state
                    if not google_hit and has_state:
                        if logger:
                            logger.info(f"   -> Retrying Google Places: {query_name} + state={query_region}")
                        google_hit = local_enrichment.enrich_local_business(query_name, query_region)
                    if logger and google_hit:
                        logger.info(f"   -> FEEDBACK: Google Places retry SUCCESS - got new candidate!")
                except Exception as e:
                    if logger:
                        logger.warning(f"   -> FEEDBACK: Google Places retry failed: {e}")
            # 5B. Run Hunter/Apollo/Snov immediately with discovered domain
            if has_domain and not row.get("primary_email"):
                discovered_domain = row.get("discovered_domain")
                if logger:
                    logger.info(f"   -> FEEDBACK: Running email providers with discovered domain={discovered_domain}")
                try:
                    # Run email waterfall with discovered domain
                    wf = email_waterfall_enrich(
                        company=name,
                        domain=discovered_domain,
                        person_name=None,
                        logger=logger
                    )
                    if wf.get("primary_email"):
                        assign_email(row, wf["primary_email"], source=wf.get("email_source") or "feedback_waterfall")
                        row["primary_email_confidence"] = wf.get("email_confidence")
                        row["email_type"] = wf.get("email_type")
                        row["email_providers_attempted"] = wf.get("email_tried") or ""
                        if logger:
                            logger.info(
                                f"   -> FEEDBACK: Email waterfall SUCCESS - "
                                f"got {row['primary_email']} from {row['primary_email_source']}"
                            )
                except Exception as e:
                    if logger:
                        logger.warning(f"   -> FEEDBACK: Email waterfall with discovered domain failed: {e}")
            # 5C. Update candidates flag after retry (feeds into canonical matching)
            has_candidates = bool(google_hit or yelp_hit)
            if logger:
                logger.info(
                    f"   -> FEEDBACK: After retry, has_candidates={has_candidates} "
                    f"(google_hit={bool(google_hit)}, yelp_hit={bool(yelp_hit)})"
                )
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
            msg = f"   -> CANONICAL: {canonical['source']} (score={match_meta['best_score']:.2f}, reason={match_meta.get('reason', 'unknown')})"
            if "soft_threshold" in match_meta.get("reason", ""):
                msg += f" [SOFT: domain={match_meta.get('domain_match_exact')}, phone={match_meta.get('phone_match_exact')}]"
            logger.info(msg)
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
        # PHASE 4.6.2: Email enrichment MOVED to after canonical block (runs even if canonical fails)
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
        row["canonical_match_score"] = float(match_meta.get("best_score") or 0.0)  # PHASE 4.6: Keep REAL best score for analysis
        row["canonical_match_reason"] = match_meta.get("reason", "below_threshold_0.8")
        row["debug_notes"] += "|entity_match_below_80"
        # PHASE 4.6: Keep component scores for threshold tuning
        row["canonical_score_name"] = float(match_meta.get("score_name") or 0.0)
        row["canonical_score_state"] = float(match_meta.get("score_state") or 0.0)
        row["canonical_score_domain"] = float(match_meta.get("score_domain") or 0.0)
        row["canonical_score_phone"] = float(match_meta.get("score_phone") or 0.0)
        # IMPORTANT: Keep discovered_* fields even if canonical failed
        # This ensures "rejected" rows still have useful data
        if logger:
            logger.info(
                f"   -> Keeping discovered data + diagnostic scores: "
                f"best_score={row['canonical_match_score']:.2f} "
                f"(name={row['canonical_score_name']:.2f}, state={row['canonical_score_state']:.2f}, "
                f"domain={row['canonical_score_domain']:.2f}, phone={row['canonical_score_phone']:.2f})"
            )

    # ============================================================
    # STEP 7.5: PHONE PROMOTION (Phase 4.6.4)
    # Promote discovered_phone if primary is still empty
    # ============================================================
    row = _promote_discovered_phone(row, logger=logger)

    # ============================================================
    # STEP 7.6: EMAIL ENRICHMENT (Phase 4.6.4)
    # ALWAYS run when ANY domain exists (canonical OR discovered)
    # ============================================================
    row = _run_email_step(name, row, logger=logger)
    # Website micro-scan fallback (if still no email)
    if not row.get("primary_email"):
        website = row.get("business_website") or row.get("canonical_website") or row.get("discovered_website")
        if website:
            try:
                _rate.wait("website_email_scan")
                if logger:
                    logger.info(f"   -> Website scan for email: {website}")
                e2 = micro_scan_for_email(website, logger=logger)
                if e2:
                    assign_email(row, e2, source="website_scan")
                    row["email_type"] = "generic"
                    row["primary_email_confidence"] = "low"
                    if logger:
                        logger.info(f"   -> Website scan found: {e2}")
            except Exception as e:
                if logger:
                    logger.warning(f"   -> Website scan failed: {e}")
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
