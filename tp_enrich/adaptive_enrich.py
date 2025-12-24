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
from tp_enrich.candidates import build_google_candidate, build_yelp_candidate, apply_candidate_anchors_to_row
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
    # PHASE 4.6.5: Trigger discovery when missing ANY key anchor (domain OR phone)
    # Changed from AND to OR - discovery should fire when EITHER is missing
    missing_domain = not bool((row.get("company_domain") or row.get("business_domain") or "").strip())
    missing_phone = not bool((row.get("primary_phone") or "").strip())
    missing_state = not bool((row.get("business_state_region") or "").strip())
    missing_key_anchors = missing_domain or missing_phone  # PHASE 4.6.5: OR logic (not AND)
    should_discover = (not has_candidates) or missing_key_anchors
    if should_discover:
        if not has_candidates:
            if logger:
                logger.info("   -> No candidates from Google/Yelp, triggering anchor discovery")
        else:
            if logger:
                logger.info(
                    f"   -> Weak anchors (domain={not missing_domain}, phone={not missing_phone}, "
                    f"state={not missing_state}), triggering anchor discovery"
                )
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
                # PHASE 4.6.5: CRITICAL - NEVER overwrite business_state_region
                # discovered_state_region stays separate and is only used as query fallback
                # Overwriting caused state poisoning (VA -> ID) which killed canonical matching
            if discovered.get("discovered_address"):
                # PHASE 4.6.5: Only set address if currently missing (don't overwrite)
                if not row.get("business_address"):
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
                    # PHASE 4.6.5: Use discovered_state_region as query fallback only (NOT as replacement)
                    query_region = (
                        row.get("business_state_region")  # Original input (sacred, never overwrite)
                        or row.get("discovered_state_region")  # Discovery fallback (for queries only)
                        or region  # Initial region param
                    )
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
    # PHASE 4.6.5: Build normalized candidates from raw hits
    # This ensures canonical matching can see website/phone from Google Details
    google_candidate = build_google_candidate(row, google_hit) if google_hit else None
    yelp_candidate = build_yelp_candidate(row, yelp_hit) if yelp_hit else None
    # PHASE 4.6.5: Apply candidate anchors to row BEFORE canonical matching
    # This copies website/phone from candidates to the row, fixing empty row inputs
    if google_candidate:
        apply_candidate_anchors_to_row(row, google_candidate)
    if yelp_candidate:
        apply_candidate_anchors_to_row(row, yelp_candidate)
    # Now run canonical matching with enriched row
    candidates = [c for c in [google_candidate, yelp_candidate] if c]
    if candidates:
        try:
            canonical = choose_canonical_business(name, candidates)
            if canonical and canonical.get("match_score", 0.0) >= 0.80:
                # Apply canonical data to row
                apply_canonical_to_row(row, canonical)
                if logger:
                    logger.info(
                        f"   -> CANONICAL: Accepted {canonical.get('source')} "
                        f"(score={canonical.get('match_score', 0.0):.2f})"
                    )
            else:
                if logger:
                    logger.info(
                        f"   -> CANONICAL: Rejected - score too low "
                        f"({canonical.get('match_score', 0.0) if canonical else 0.0:.2f} < 0.80)"
                    )
        except Exception as e:
            if logger:
                logger.warning(f"   -> CANONICAL: Matching failed: {e}")
    # ============================================================
    # STEP 7: Fill gaps with phase2 enrichment
    # ============================================================
    # PHASE 4.6.5: Run OpenCorporates ONLY for specific patterns
    # Don't run on every business - this was too slow
    if should_run_opencorporates(row):
        try:
            if logger:
                logger.info("   -> Running OpenCorporates (missing critical anchors)")
            oc_data = phase2_enrich(
                row.get("business_name"),
                city=row.get("business_city"),
                state=row.get("business_state_region"),
                logger=logger
            )
            # Merge OpenCorporates data into row (only if missing)
            if oc_data:
                for k in ["business_address", "business_city", "business_state_region", "business_postal_code"]:
                    if not row.get(k) and oc_data.get(k):
                        row[k] = oc_data[k]
                if logger:
                    logger.info("   -> OpenCorporates: Data merged")
        except Exception as e:
            if logger:
                logger.warning(f"   -> OpenCorporates failed: {e}")
    # ============================================================
    # STEP 8: Phone enrichment
    # ============================================================
    # PHASE 4.6.5: Always run phone enrichment when we have anchors
    # This was previously skipped when canonical matching failed
    if row.get("business_domain") or row.get("discovered_domain") or row.get("business_address"):
        if not row.get("primary_phone"):
            try:
                if logger:
                    logger.info("   -> Running phone enrichment waterfall")
                phone_data = enrich_business_phone_waterfall(
                    business_name=name,
                    domain=row.get("business_domain") or row.get("discovered_domain"),
                    address=row.get("business_address"),
                    city=row.get("business_city"),
                    state=row.get("business_state_region"),
                    logger=logger
                )
                if phone_data and phone_data.get("phone"):
                    row["primary_phone"] = phone_data["phone"]
                    row["primary_phone_display"] = phone_data.get("display") or phone_data["phone"]
                    row["primary_phone_source"] = phone_data.get("source") or "phone_waterfall"
                    row["primary_phone_confidence"] = phone_data.get("confidence") or "medium"
                    if logger:
                        logger.info(f"   -> PHONE: SUCCESS {row['primary_phone']} (source={row['primary_phone_source']})")
            except Exception as e:
                if logger:
                    logger.warning(f"   -> Phone enrichment failed: {e}")
        # PHASE 4.6.4: Promote discovered phone if waterfall failed
        row = _promote_discovered_phone(row, logger=logger)
    # ============================================================
    # STEP 9: Email enrichment
    # ============================================================
    # PHASE 4.6.3/4.6.4: ALWAYS run email when ANY domain exists
    # This is CRITICAL - email must run even when canonical matching fails
    row = _run_email_step(name, row, logger=logger)
    # ============================================================
    # STEP 10: Final cleanup
    # ============================================================
    # Ensure we have at least discovered data even if canonical failed
    if not row.get("business_domain") and row.get("discovered_domain"):
        row["business_domain"] = row["discovered_domain"]
    if not row.get("primary_phone") and row.get("discovered_phone"):
        row = _promote_discovered_phone(row, logger=logger)
    if not row.get("business_address") and row.get("discovered_address"):
        row["business_address"] = row["discovered_address"]
    if logger:
        logger.info(
            f"Enrichment complete: domain={bool(row.get('business_domain'))}, "
            f"phone={bool(row.get('primary_phone'))}, email={bool(row.get('primary_email'))}"
        )
    return row
