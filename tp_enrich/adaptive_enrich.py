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
from collections.abc import Mapping

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

def _norm_domain(d: str) -> str:
    """Normalize domain from URL or domain string."""
    d = (d or "").strip().lower()
    d = d.replace("http://", "").replace("https://", "").replace("www.", "")
    d = d.split("/")[0].split("?")[0].split("#")[0]
    return d

def _is_blank(v) -> bool:
    """Check if value is blank/empty."""
    return not (v or "").strip()

def _pick_domain_any(row: dict) -> str:
    """Pick domain anchor for email + matching from any available field."""
    for k in ["company_domain", "canonical_domain", "discovered_domain", "business_domain"]:
        d = (row.get(k) or "").strip()
        if d:
            d = d.replace("http://", "").replace("https://", "").replace("www.", "").split("/")[0]
            return d
    return ""

def _pick_phone_any(row: dict) -> str:
    """Pick phone anchor for matching from any available field."""
    for k in ["primary_phone", "business_phone", "discovered_phone"]:
        p = (row.get(k) or "").strip()
        if p:
            return p
    return ""

def google_lookup_allow_name_only(name: str, api_key: str, discovered_phone: str = "", discovered_address: str = ""):
    """
    PHASE 4.6.5 HOTFIX: Google lookup that NEVER requires state/city.

    Priority:
      1) name + discovered_phone
      2) name + discovered_address
      3) name only

    Returns Google Places result with details if found, None otherwise.
    """
    n = (name or "").strip()
    if not n:
        return None

    # Try name + phone first (strongest signal)
    if discovered_phone:
        try:
            hit = local_enrichment.google_places_scout_by_name(n + " " + discovered_phone, api_key)
            if hit:
                return hit
        except:
            pass

    # Try name + address
    if discovered_address:
        try:
            hit = local_enrichment.google_places_scout_by_name(n + " " + discovered_address, api_key)
            if hit:
                return hit
        except:
            pass

    # Fall back to name only
    try:
        return local_enrichment.google_places_scout_by_name(n, api_key)
    except:
        return None

def _google_is_strong_anchor(google_hit: dict) -> bool:
    """
    PHASE 4.6.5 FINAL: Returns True if Google hit has a strong anchor.

    Strong anchor = Google provides a real phone or website (from Place Details).
    This is the 90% case where Google data is authoritative.

    Args:
        google_hit: Google Places API result dict

    Returns:
        True if has phone or website, False otherwise
    """
    if not isinstance(google_hit, dict):
        return False

    phone = (google_hit.get("formatted_phone_number") or google_hit.get("phone") or "").strip()
    website = (google_hit.get("website") or google_hit.get("domain") or "").strip()

    return bool(phone or website)

def _apply_google_details_to_row(row: dict, google_hit: dict):
    """
    PHASE 4.6.5 FINAL: Pull best anchors out of Google Details and write them into the row.

    This ensures domain/phone coverage increases and email enrichment can recover,
    even if canonical matching fails or Google is not selected as canonical.

    Always called BEFORE canonical selection to ensure Google Details anchors
    are available for both canonical scoring and email enrichment.
    """
    if not isinstance(google_hit, dict):
        return

    g_phone = (google_hit.get("formatted_phone_number") or google_hit.get("phone") or "").strip()
    g_site = (google_hit.get("website") or "").strip()
    g_domain = _norm_domain(g_site)

    # Only fill if empty (do not overwrite better existing values)
    if g_phone and not (row.get("primary_phone") or "").strip():
        row["primary_phone"] = g_phone
        row["primary_phone_source"] = row.get("primary_phone_source") or "google_details"

    if g_domain and not (row.get("company_domain") or "").strip():
        row["company_domain"] = g_domain
        row["company_domain_source"] = row.get("company_domain_source") or "google_details"

def _apply_canonical_compat(row: dict, candidate: dict, match_meta: dict, logger):
    """
    PHASE 4.6.5 FINAL: Call apply_canonical_to_row using whatever signature the repo expects.

    We try the common forms safely so we NEVER crash the row.
    This handles the error: "apply_canonical_to_row() missing 1 required positional argument: 'match_meta'"
    """
    try:
        # Most likely in your repo based on the error: requires match_meta as positional arg
        return apply_canonical_to_row(row, candidate, match_meta)
    except TypeError:
        try:
            # Some repos use keyword
            return apply_canonical_to_row(row, candidate, match_meta=match_meta)
        except TypeError as ex:
            if logger:
                logger.exception("apply_canonical_to_row signature mismatch; cannot apply canonical safely: %r", ex)
            return None

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

def enrich_row_phase46(
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

    # PHASE 4.6.7.3: Initialize CSV-level execution markers
    row = _phase467_init_markers(row, logger, name)
    # ============================================================
    # STEP 0: Start with existing anchors
    # ============================================================
    has_state = bool(region)
    has_domain = False
    has_phone = False
    # ============================================================
    # STEP 1: Try Google Places (ALWAYS - name-only allowed)
    # PHASE 4.6.5 HOTFIX: Use google_lookup_allow_name_only
    # ============================================================
    google_hit = None
    yelp_hit = None
    # Get API key from environment
    google_api_key = local_enrichment.GOOGLE_PLACES_API_KEY or ""

    # HOTFIX: Google NEVER skipped, tries multiple query strategies
    # PHASE 4.6.7.3: Mark Google always run BEFORE first call
    row = _mark_google_always(row, logger, name)

    try:
        google_hit = google_lookup_allow_name_only(
            name=name,
            api_key=google_api_key,
            discovered_phone=(row.get("discovered_phone") or "").strip(),
            discovered_address=(row.get("discovered_address") or "").strip(),
        )
        if logger and google_hit:
            logger.info(f"   -> Google Places: name={google_hit.get('name')} state={google_hit.get('state_region')}")
    except Exception as e:
        if logger:
            logger.warning(f"   -> Google Places failed: {e}")
    # ============================================================
    # STEP 2: Try Yelp (same logic)
    # ============================================================
    # Yelp integration optional - skip for now
    # ============================================================
    # STEP 3: Check if we have weak candidates or missing anchors
    # ============================================================
    has_candidates = bool(google_hit or yelp_hit)

    # FIX 2: Trigger discovery when missing BOTH domain AND phone (not just one)
    # Only run expensive anchor discovery when we have no anchors at all
    missing_domain = not bool((row.get("company_domain") or row.get("business_domain") or "").strip())
    missing_phone = not bool((row.get("primary_phone") or "").strip())
    missing_state = not bool((row.get("business_state_region") or "").strip())
    missing_key_anchors = missing_domain and missing_phone  # FIX 2: AND logic (both must be missing)

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
        # PHASE 4.6.7.3: Mark SERP-first before anchor discovery
        row = _mark_serp_first(row, logger, name)

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
            # 5A. Retry Google Places with discovered anchors
            # PHASE 4.6.5 HOTFIX: Use google_lookup_allow_name_only for retry
            if not google_hit:
                if logger:
                    logger.info("   -> FEEDBACK: Retrying Google Places with discovered anchors")
                try:
                    google_hit = google_lookup_allow_name_only(
                        name=name,
                        api_key=google_api_key,
                        discovered_phone=(row.get("discovered_phone") or "").strip(),
                        discovered_address=(row.get("discovered_address") or "").strip(),
                    )
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

                # PHASE 4.6.7.3: Mark email retry
                row = _mark_email_retry(row, logger)

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
    # This ensures row has company_domain/primary_phone for scoring
    if google_candidate:
        apply_candidate_anchors_to_row(row, google_candidate, logger=logger, source="google")
    if yelp_candidate:
        apply_candidate_anchors_to_row(row, yelp_candidate, logger=logger, source="yelp")

    # ============================================================
    # PHASE 4.6.5 FINAL: ALWAYS apply Google Details anchors first
    # This ensures phone/domain are written even if canonical fails
    # ============================================================
    _apply_google_details_to_row(row, google_hit)

    # ============================================================
    # CANONICAL SELECTION (GOOGLE STRONG-ANCHOR SHORT-CIRCUIT)
    # PHASE 4.6.5 FINAL: Auto-accept Google when it has phone or website
    # ============================================================
    match_meta = None

    if google_hit and _google_is_strong_anchor(google_hit):
        # ✅ AUTO-ACCEPT GOOGLE (STRONG ANCHOR)
        if logger:
            logger.info("   -> CANONICAL: auto-accepting Google strong anchor")

        match_meta = {
            "reason": "google_strong_anchor",
            "score": 1.0,
            "source": "google",
        }
        _apply_canonical_compat(row, google_hit, match_meta, logger)

        # ✅ Force canonical bookkeeping to be correct and not "unknown"
        row["canonical_source"] = "google"
        row["canonical_match_score"] = 1.0
        # Keep the reason if your schema supports it; otherwise leave debug_notes
        if "canonical_match_reason" in row:
            row["canonical_match_reason"] = "google_strong_anchor"
        else:
            row["debug_notes"] = (row.get("debug_notes") or "") + "|google_strong_anchor"

    else:
        # PHASE 4.6.5: Pass normalized candidates (not raw hits) to matcher
        canonical, match_meta = choose_canonical_business(row, google_candidate, yelp_candidate)
        if canonical:
            if logger:
                msg = f"   -> CANONICAL: {canonical['source']} (score={match_meta['best_score']:.2f}, reason={match_meta.get('reason', 'unknown')})"
                if "soft_threshold" in match_meta.get("reason", ""):
                    msg += f" [SOFT: domain={match_meta.get('domain_match_exact')}, phone={match_meta.get('phone_match_exact')}]"
                logger.info(msg)
            _apply_canonical_compat(row, canonical, match_meta or {}, logger)

            # ✅ Ensure canonical_source never becomes "unknown"
            if not (row.get("canonical_source") or "").strip() or str(row.get("canonical_source")).strip().lower() == "unknown":
                # if meta has a source, use it; else default to google if canonical came from google_hit
                src = (match_meta.get("source") if isinstance(match_meta, dict) else None) or "google"
                row["canonical_source"] = src

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
            row["canonical_match_reason"] = (match_meta.get("reason") if isinstance(match_meta, dict) else None) or "below_threshold"
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
    # STEP 7: Full enrichment (phone/email waterfalls)
    # Runs for ALL rows (canonical accepted OR rejected)
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


# ============================================================
# PHASE 4.6.7.2 — STOP REPEAT-LOOP + FORCE KEY FALLBACK + PROVE PROGRESS
# ============================================================

import os
import time

# ---- global reentry guard (process-wide) ----
_PHASE467_ACTIVE_KEYS = set()

def _env_first(*names) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""

def _resolve_keys(args, kwargs):
    # kwargs
    serp_api_key = (kwargs.get("serp_api_key") or "").strip()
    google_api_key = (kwargs.get("google_api_key") or "").strip()

    # context dict style: enrich(row, ctx)
    if args and len(args) == 1 and isinstance(args[0], dict):
        serp_api_key = serp_api_key or (args[0].get("serp_api_key") or "").strip()
        google_api_key = google_api_key or (args[0].get("google_api_key") or "").strip()

    # positional style: enrich(row, serp, google, logger)
    if args and not (len(args) == 1 and isinstance(args[0], dict)):
        if len(args) >= 1 and not serp_api_key:
            serp_api_key = (args[0] or "").strip()
        if len(args) >= 2 and not google_api_key:
            google_api_key = (args[1] or "").strip()

    # ENV fallback (critical)
    serp_api_key = serp_api_key or _env_first("SERP_API_KEY", "SERPAPI_KEY", "SERPAPI_API_KEY")
    google_api_key = google_api_key or _env_first("GOOGLE_API_KEY", "GOOGLE_PLACES_API_KEY", "GPLACES_KEY")

    return serp_api_key, google_api_key

def _resolve_logger(args, kwargs):
    logger = kwargs.get("logger", None)
    if logger is None and args:
        # positional (row, serp, google, logger)
        if len(args) >= 3 and not (len(args) == 1 and isinstance(args[0], dict)):
            logger = args[2]
        # context dict
        if len(args) == 1 and isinstance(args[0], dict):
            logger = args[0].get("logger", None)

    # no-op logger fallback
    class _NoopLogger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass
    return logger or _NoopLogger()

def _business_key(row: dict) -> str:
    # stable key so we can detect repeat loop on same business
    return (
        (row.get("company_normalized_key") or "").strip().lower()
        or (row.get("company_search_name") or row.get("raw_display_name") or "").strip().lower()
    )

def _coerce_row_obj(obj) -> dict:
    """
    Accepts dict-like, pandas Series, or string business name.
    Returns a dict row that the rest of the pipeline can handle.
    """
    if obj is None:
        return {}

    # dict-like
    if isinstance(obj, Mapping):
        return dict(obj)

    # pandas Series or similar
    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
        try:
            d = obj.to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass

    # plain string (business name)
    if isinstance(obj, str):
        name = obj.strip()
        return {
            "raw_display_name": name,
            "company_search_name": name,
            "name_classification": "business",
            "enrichment_status": "",
            "debug_notes": "",
        }

    # last resort
    return {"raw_display_name": str(obj), "company_search_name": str(obj), "name_classification": "business"}

def enrich_single_business_adaptive(row, *args, **kwargs):
    """
    CRITICAL: Handles row being dict, pandas Series, OR string.
    Enriches ANY row with a name (no business classification gating).
    """
    # Coerce to dict safely (handles string, Series, dict)
    row = _coerce_row_obj(row)

    # -------- extract context safely --------
    serp_api_key = kwargs.get("serp_api_key")
    google_api_key = kwargs.get("google_api_key")
    logger = kwargs.get("logger")

    if args:
        if len(args) == 1 and isinstance(args[0], dict):
            serp_api_key = serp_api_key or args[0].get("serp_api_key")
            google_api_key = google_api_key or args[0].get("google_api_key")
            logger = logger or args[0].get("logger")
        else:
            if len(args) > 0 and serp_api_key is None:
                serp_api_key = args[0]
            if len(args) > 1 and google_api_key is None:
                google_api_key = args[1]
            if len(args) > 2 and logger is None:
                logger = args[2]

    # no-op logger fallback
    class _Noop:
        def info(self,*a,**k): pass
        def warning(self,*a,**k): pass
        def error(self,*a,**k): pass
        def exception(self,*a,**k): pass
    if logger is None:
        logger = _Noop()

    # -------- determine name (THIS IS THE TRUE GATE) --------
    name = (
        (row.get("company_search_name") or "").strip()
        or (row.get("raw_display_name") or "").strip()
    )
    cls = (row.get("name_classification") or "").strip().lower()

    logger.warning(
        "ENRICH_ENTRY_SENTINEL row_id=%s class=%s name=%s input_type=%s",
        row.get("row_id"),
        cls,
        name,
        type(row).__name__,
    )

    # If we literally have no name, bail
    if not name:
        row["enrichment_status"] = "skipped_no_name"
        return row

    # -------- initialize flags so CSV proves execution --------
    row.setdefault("google_always_ran", False)
    row.setdefault("serp_first_ran", False)
    row.setdefault("address_retry_ran", False)
    row.setdefault("domain_retry_ran", False)
    row.setdefault("email_retry_ran", False)

    # -------- ENV key fallback --------
    import os
    if not serp_api_key:
        serp_api_key = os.environ.get("SERP_API_KEY") or os.environ.get("SERPAPI_KEY") or ""
    if not google_api_key:
        google_api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_PLACES_API_KEY") or ""

    # -------- ROUTE INTO REAL ENRICHMENT --------
    try:
        # Extract region for enrich_row_phase46
        region = row.get("business_state_region")

        # Call with name/region/logger signature (current implementation)
        return enrich_row_phase46(
            name=name,
            region=region,
            logger=logger,
        )
    except Exception as e:
        logger.exception(
            "ENRICH_FATAL_ERROR row_id=%s name=%s err=%s",
            row.get("row_id"),
            name,
            e,
        )
        row["enrichment_status"] = "error"
        row["debug_notes"] = (row.get("debug_notes") or "") + f"|fatal:{type(e).__name__}:{e}"
        return row


# Legacy alias safety
enrich_single_business = enrich_single_business_adaptive

# ============================================================
# END PHASE 4.6.7.2
# ============================================================


# ============================================================
# PHASE 4.6.7.3 — CSV-LEVEL TRIGGER PROOF + WARNING SENTINELS
# ============================================================

def _flag(row: dict, key: str, val=True):
    # persist boolean-ish flags into the row for CSV output
    try:
        row[key] = bool(val)
    except Exception:
        row[key] = val
    return row

def _append_debug(row: dict, token: str):
    token = (token or "").strip()
    if not token:
        return row
    cur = (row.get("debug_notes") or "").strip()
    if token in cur:
        return row
    row["debug_notes"] = (cur + ("|" if cur else "") + token).strip("|")
    return row

def _sentinel(logger, msg: str, **kv):
    """
    WARNING-level sentinel so it survives log level changes/truncation.
    """
    try:
        parts = [msg] + [f"{k}={v}" for k, v in kv.items()]
        logger.warning(" ".join(parts))
    except Exception:
        pass

def _phase467_init_markers(row: dict, logger, name: str):
    row = _flag(row, "phase467_version", "4.6.7.3")
    row = _flag(row, "google_always_ran", False)
    row = _flag(row, "serp_first_ran", False)
    row = _flag(row, "address_retry_ran", bool(row.get("address_retry_ran") is True))
    row = _flag(row, "domain_retry_ran", bool(row.get("domain_retry_ran") is True))
    row = _flag(row, "email_retry_ran", bool(row.get("email_retry_ran") is True))
    _sentinel(logger, "PHASE467_ROW_INIT", row_id=row.get("row_id"), name=name)
    return row

def _mark_google_always(row: dict, logger, name: str):
    row = _flag(row, "google_always_ran", True)
    row = _append_debug(row, "google_always_ran")
    _sentinel(logger, "GOOGLE_ALWAYS_RUN_SENTINEL", row_id=row.get("row_id"), name=name)
    return row

def _mark_serp_first(row: dict, logger, name: str):
    row = _flag(row, "serp_first_ran", True)
    row = _append_debug(row, "serp_first_ran")
    _sentinel(logger, "SERP_FIRST_SENTINEL", row_id=row.get("row_id"), name=name)
    return row

def _mark_address_retry(row: dict, logger):
    row = _flag(row, "address_retry_ran", True)
    row = _append_debug(row, "address_retry_ran")
    _sentinel(logger, "ADDRESS_RETRY_SENTINEL", row_id=row.get("row_id"))
    return row

def _mark_domain_retry(row: dict, logger):
    row = _flag(row, "domain_retry_ran", True)
    row = _append_debug(row, "domain_retry_ran")
    _sentinel(logger, "DOMAIN_RETRY_SENTINEL", row_id=row.get("row_id"))
    return row

def _mark_email_retry(row: dict, logger):
    row = _flag(row, "email_retry_ran", True)
    row = _append_debug(row, "email_retry_ran")
    _sentinel(logger, "EMAIL_RETRY_SENTINEL", row_id=row.get("row_id"))
    return row

# ============================================================
# END PHASE 4.6.7.3
# ============================================================
