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


# ============================================================
# PHASE 4.6.5.6 — BUSINESS NAME PROMOTION + SENTINELS
# ============================================================

_PROMOTE_BIZ_TOKENS = {
    "books", "bookstore", "classics", "consulting", "coding", "organics", "beauty",
    "entertainment", "media", "holdings", "distribution", "tire", "wash", "mobile",
    "plumbing", "roofing", "fitness", "athletics", "bistro", "cafe", "restaurant",
    "studio", "studios", "logistics", "transport", "trucking", "construction", "detailing"
}

# ============================================================
# PHASE 4.6.7.1 — FIX 4.6.5 + 4.6.6 PERSISTING ISSUES
# ============================================================

import importlib
import inspect


def _safe_str(v) -> str:
    """Safe string conversion."""
    return (v or "").strip()


def _bool_has(v) -> bool:
    """Check if value has content."""
    return bool(_safe_str(v))


def _set_if_blank(row: dict, key: str, val):
    """Set value only if current value is blank."""
    if not _bool_has(row.get(key)):
        row[key] = val


def _call_any(fn, row: dict, logger):
    """
    Call function with best-effort signature handling.
    Returns: (new_row, ok_bool, err_str)
    """
    try:
        sig = None
        try:
            sig = inspect.signature(fn)
        except Exception:
            sig = None

        if sig:
            params = list(sig.parameters.keys())
            if "logger" in params:
                return fn(row, logger=logger), True, ""
            if len(params) >= 2:
                return fn(row, logger), True, ""
            return fn(row), True, ""

        # Fallback
        try:
            return fn(row, logger=logger), True, ""
        except TypeError:
            try:
                return fn(row, logger), True, ""
            except TypeError:
                return fn(row), True, ""

    except Exception as e:
        return row, False, f"{type(e).__name__}: {e}"


def _resolve_callable_candidates(names):
    """
    Try to resolve callables from multiple sources.
    Returns first callable found else None.
    """
    # 1) globals
    for n in names:
        fn = globals().get(n)
        if callable(fn):
            return fn

    # 2-4) common modules
    mod_names = [
        "tp_enrich.phase2_final",
        "tp_enrich.phase2_enrichment",
        "tp_enrich.local_enrichment",
    ]
    for mn in mod_names:
        try:
            m = importlib.import_module(mn)
        except Exception:
            continue
        for n in names:
            fn = getattr(m, n, None)
            if callable(fn):
                return fn

    return None


def _normalize_bbb_payload_into_row(row: dict):
    """Populate phase2_bbb_* fields from any BBB outputs."""
    b_phone = row.get("phase2_bbb_phone") or row.get("bbb_phone") or row.get("bbb_business_phone") or ""
    b_email = row.get("phase2_bbb_email") or row.get("bbb_email") or row.get("bbb_business_email") or ""
    b_site = row.get("phase2_bbb_website") or row.get("bbb_website") or row.get("bbb_site") or ""

    _set_if_blank(row, "phase2_bbb_phone", _safe_str(b_phone))
    _set_if_blank(row, "phase2_bbb_email", _safe_str(b_email))
    _set_if_blank(row, "phase2_bbb_website", _safe_str(b_site))
    return row


def _normalize_yp_payload_into_row(row: dict):
    """Populate phase2_yp_* fields from any YP outputs."""
    y_phone = row.get("phase2_yp_phone") or row.get("yp_phone") or row.get("yellowpages_phone") or ""
    y_email = row.get("phase2_yp_email") or row.get("yp_email") or row.get("yellowpages_email") or ""
    y_site = row.get("phase2_yp_website") or row.get("yp_website") or row.get("yellowpages_website") or row.get("yp_site") or ""

    _set_if_blank(row, "phase2_yp_phone", _safe_str(y_phone))
    _set_if_blank(row, "phase2_yp_email", _safe_str(y_email))
    _set_if_blank(row, "phase2_yp_website", _safe_str(y_site))
    return row


def _bbbyp_empty_471(row: dict) -> bool:
    """Check if BBB/YP outputs are empty."""
    return (not _bool_has(row.get("phase2_bbb_phone"))
            and not _bool_has(row.get("phase2_bbb_email"))
            and not _bool_has(row.get("phase2_bbb_website"))
            and not _bool_has(row.get("phase2_yp_phone"))
            and not _bool_has(row.get("phase2_yp_email"))
            and not _bool_has(row.get("phase2_yp_website")))


def _needs_contact_471(row: dict) -> bool:
    """Check if still needs phone OR email."""
    return (not _bool_has(row.get("primary_phone"))) or (not _bool_has(row.get("primary_email")))


def _should_address_retry_471(row: dict) -> bool:
    """Decide if should retry BBB/YP with address."""
    if not _bool_has(row.get("discovered_address")):
        return False
    if not _needs_contact_471(row):
        return False
    if not _bbbyp_empty_471(row):
        return False
    if row.get("address_retry_ran") is True:
        return False
    return True


def _run_address_retry_bbb_yp(row: dict, logger):
    """
    Durable BBB + YP retry with auto-detection and signature handling.
    """
    row["address_retry_ran"] = True

    logger.info(
        "ADDRESS_RETRY_SENTINEL row_id=%s name=%s addr=%s needs_contact=%s",
        row.get("row_id"),
        row.get("company_search_name") or row.get("raw_display_name"),
        row.get("discovered_address"),
        _needs_contact_471(row),
    )

    # Resolve BBB function
    bbb_fn = _resolve_callable_candidates([
        "run_bbb_lookup",
        "bbb_lookup",
        "phase2_bbb_lookup",
        "run_phase2_bbb",
        "enrich_bbb",
        "bbb_enrich",
    ])

    # Resolve YP function
    yp_fn = _resolve_callable_candidates([
        "run_yp_lookup",
        "yp_lookup",
        "phase2_yp_lookup",
        "run_phase2_yp",
        "enrich_yp",
        "yp_enrich",
        "yellowpages_lookup",
    ])

    # Execute BBB
    if not callable(bbb_fn):
        logger.info("BBB_RETRY_SKIPPED_MISSING_FN row_id=%s", row.get("row_id"))
    else:
        row, ok, err = _call_any(bbb_fn, row, logger)
        if not ok:
            logger.warning("BBB_RETRY_ERROR row_id=%s err=%s", row.get("row_id"), err)
        row = _normalize_bbb_payload_into_row(row)

    # Execute YP
    if not callable(yp_fn):
        logger.info("YP_RETRY_SKIPPED_MISSING_FN row_id=%s", row.get("row_id"))
    else:
        row, ok, err = _call_any(yp_fn, row, logger)
        if not ok:
            logger.warning("YP_RETRY_ERROR row_id=%s err=%s", row.get("row_id"), err)
        row = _normalize_yp_payload_into_row(row)

    # Promote to primary if still missing
    if not _bool_has(row.get("primary_phone")):
        _set_if_blank(row, "primary_phone", row.get("phase2_bbb_phone") or row.get("phase2_yp_phone") or "")
        _set_if_blank(row, "primary_phone_source", "phase2_dir_retry")
    
    if not _bool_has(row.get("primary_email")):
        _set_if_blank(row, "primary_email", row.get("phase2_bbb_email") or row.get("phase2_yp_email") or "")
        if _bool_has(row.get("primary_email")):
            _set_if_blank(row, "primary_email_source", "phase2_dir_retry")

    return row


def _log_google_always_run(row: dict, logger, name: str):
    """Log Google always run sentinel."""
    logger.info(
        "GOOGLE_ALWAYS_RUN_SENTINEL row_id=%s class=%s name=%s",
        row.get("row_id"),
        (row.get("name_classification") or "").strip(),
        name,
    )


def _phase466_address_retry_hook(row: dict, logger):
    """Phase 4.6.6 address retry hook."""
    if _should_address_retry_471(row):
        row = _run_address_retry_bbb_yp(row, logger)
    return row


# ============================================================
# ENTRYPOINT ROUTER
# ============================================================
def enrich_row(row: dict, serp_api_key: str, google_api_key: str, logger):
    """
    Entrypoint router that ensures:
    - Business gating intact
    - Sentinels visible
    - enrich_single_business_adaptive is called
    """
    name = (row.get("company_search_name") or row.get("raw_display_name") or "").strip()
    cls = (row.get("name_classification") or "").strip().lower()

    logger.info("ENRICH_ENTRY_SENTINEL row_id=%s class=%s name=%s", row.get("row_id"), cls, name)

    if cls != "business":
        return row

    # Google always run sentinel
    _log_google_always_run(row, logger, name)

    # Call existing enrichment
    row = enrich_single_business_adaptive(
        name=name,
        region=row.get("business_state_region"),
        logger=logger
    )

    # Address retry hook
    row = _phase466_address_retry_hook(row, logger)

    return row



# ============================================================
# PHASE 4.6.7.1 — FIX 4.6.5 + 4.6.6 PERSISTING ISSUES
# ============================================================

import importlib
import inspect


def _safe_str(v) -> str:
    """Safe string conversion."""
    return (v or "").strip()


def _bool_has(v) -> bool:
    """Check if value has content."""
    return bool(_safe_str(v))


def _set_if_blank(row: dict, key: str, val):
    """Set value only if current value is blank."""
    if not _bool_has(row.get(key)):
        row[key] = val


def _call_any(fn, row: dict, logger):
    """
    Call function with best-effort signature handling.
    Returns: (new_row, ok_bool, err_str)
    """
    try:
        sig = None
        try:
            sig = inspect.signature(fn)
        except Exception:
            sig = None

        if sig:
            params = list(sig.parameters.keys())
            if "logger" in params:
                return fn(row, logger=logger), True, ""
            if len(params) >= 2:
                return fn(row, logger), True, ""
            return fn(row), True, ""

        # Fallback
        try:
            return fn(row, logger=logger), True, ""
        except TypeError:
            try:
                return fn(row, logger), True, ""
            except TypeError:
                return fn(row), True, ""

    except Exception as e:
        return row, False, f"{type(e).__name__}: {e}"


def _resolve_callable_candidates(names):
    """
    Try to resolve callables from multiple sources.
    Returns first callable found else None.
    """
    # 1) globals
    for n in names:
        fn = globals().get(n)
        if callable(fn):
            return fn

    # 2-4) common modules
    mod_names = [
        "tp_enrich.phase2_final",
        "tp_enrich.phase2_enrichment",
        "tp_enrich.local_enrichment",
    ]
    for mn in mod_names:
        try:
            m = importlib.import_module(mn)
        except Exception:
            continue
        for n in names:
            fn = getattr(m, n, None)
            if callable(fn):
                return fn

    return None


def _normalize_bbb_payload_into_row(row: dict):
    """Populate phase2_bbb_* fields from any BBB outputs."""
    b_phone = row.get("phase2_bbb_phone") or row.get("bbb_phone") or row.get("bbb_business_phone") or ""
    b_email = row.get("phase2_bbb_email") or row.get("bbb_email") or row.get("bbb_business_email") or ""
    b_site = row.get("phase2_bbb_website") or row.get("bbb_website") or row.get("bbb_site") or ""

    _set_if_blank(row, "phase2_bbb_phone", _safe_str(b_phone))
    _set_if_blank(row, "phase2_bbb_email", _safe_str(b_email))
    _set_if_blank(row, "phase2_bbb_website", _safe_str(b_site))
    return row


def _normalize_yp_payload_into_row(row: dict):
    """Populate phase2_yp_* fields from any YP outputs."""
    y_phone = row.get("phase2_yp_phone") or row.get("yp_phone") or row.get("yellowpages_phone") or ""
    y_email = row.get("phase2_yp_email") or row.get("yp_email") or row.get("yellowpages_email") or ""
    y_site = row.get("phase2_yp_website") or row.get("yp_website") or row.get("yellowpages_website") or row.get("yp_site") or ""

    _set_if_blank(row, "phase2_yp_phone", _safe_str(y_phone))
    _set_if_blank(row, "phase2_yp_email", _safe_str(y_email))
    _set_if_blank(row, "phase2_yp_website", _safe_str(y_site))
    return row


def _bbbyp_empty_471(row: dict) -> bool:
    """Check if BBB/YP outputs are empty."""
    return (not _bool_has(row.get("phase2_bbb_phone"))
            and not _bool_has(row.get("phase2_bbb_email"))
            and not _bool_has(row.get("phase2_bbb_website"))
            and not _bool_has(row.get("phase2_yp_phone"))
            and not _bool_has(row.get("phase2_yp_email"))
            and not _bool_has(row.get("phase2_yp_website")))


def _needs_contact_471(row: dict) -> bool:
    """Check if still needs phone OR email."""
    return (not _bool_has(row.get("primary_phone"))) or (not _bool_has(row.get("primary_email")))


def _should_address_retry_471(row: dict) -> bool:
    """Decide if should retry BBB/YP with address."""
    if not _bool_has(row.get("discovered_address")):
        return False
    if not _needs_contact_471(row):
        return False
    if not _bbbyp_empty_471(row):
        return False
    if row.get("address_retry_ran") is True:
        return False
    return True


def _run_address_retry_bbb_yp(row: dict, logger):
    """
    Durable BBB + YP retry with auto-detection and signature handling.
    """
    row["address_retry_ran"] = True

    logger.info(
        "ADDRESS_RETRY_SENTINEL row_id=%s name=%s addr=%s needs_contact=%s",
        row.get("row_id"),
        row.get("company_search_name") or row.get("raw_display_name"),
        row.get("discovered_address"),
        _needs_contact_471(row),
    )

    # Resolve BBB function
    bbb_fn = _resolve_callable_candidates([
        "run_bbb_lookup",
        "bbb_lookup",
        "phase2_bbb_lookup",
        "run_phase2_bbb",
        "enrich_bbb",
        "bbb_enrich",
    ])

    # Resolve YP function
    yp_fn = _resolve_callable_candidates([
        "run_yp_lookup",
        "yp_lookup",
        "phase2_yp_lookup",
        "run_phase2_yp",
        "enrich_yp",
        "yp_enrich",
        "yellowpages_lookup",
    ])

    # Execute BBB
    if not callable(bbb_fn):
        logger.info("BBB_RETRY_SKIPPED_MISSING_FN row_id=%s", row.get("row_id"))
    else:
        row, ok, err = _call_any(bbb_fn, row, logger)
        if not ok:
            logger.warning("BBB_RETRY_ERROR row_id=%s err=%s", row.get("row_id"), err)
        row = _normalize_bbb_payload_into_row(row)

    # Execute YP
    if not callable(yp_fn):
        logger.info("YP_RETRY_SKIPPED_MISSING_FN row_id=%s", row.get("row_id"))
    else:
        row, ok, err = _call_any(yp_fn, row, logger)
        if not ok:
            logger.warning("YP_RETRY_ERROR row_id=%s err=%s", row.get("row_id"), err)
        row = _normalize_yp_payload_into_row(row)

    # Promote to primary if still missing
    if not _bool_has(row.get("primary_phone")):
        _set_if_blank(row, "primary_phone", row.get("phase2_bbb_phone") or row.get("phase2_yp_phone") or "")
        _set_if_blank(row, "primary_phone_source", "phase2_dir_retry")
    
    if not _bool_has(row.get("primary_email")):
        _set_if_blank(row, "primary_email", row.get("phase2_bbb_email") or row.get("phase2_yp_email") or "")
        if _bool_has(row.get("primary_email")):
            _set_if_blank(row, "primary_email_source", "phase2_dir_retry")

    return row


def _log_google_always_run(row: dict, logger, name: str):
    """Log Google always run sentinel."""
    logger.info(
        "GOOGLE_ALWAYS_RUN_SENTINEL row_id=%s class=%s name=%s",
        row.get("row_id"),
        (row.get("name_classification") or "").strip(),
        name,
    )


def _phase466_address_retry_hook(row: dict, logger):
    """Phase 4.6.6 address retry hook."""
    if _should_address_retry_471(row):
        row = _run_address_retry_bbb_yp(row, logger)
    return row


# ============================================================
# ENTRYPOINT ROUTER
# ============================================================
def enrich_row(row: dict, serp_api_key: str, google_api_key: str, logger):
    """
    Entrypoint router that ensures:
    - Business gating intact
    - Sentinels visible
    - enrich_single_business_adaptive is called
    """
    name = (row.get("company_search_name") or row.get("raw_display_name") or "").strip()
    cls = (row.get("name_classification") or "").strip().lower()

    logger.info("ENRICH_ENTRY_SENTINEL row_id=%s class=%s name=%s", row.get("row_id"), cls, name)

    if cls != "business":
        return row

    # Google always run sentinel
    _log_google_always_run(row, logger, name)

    # Call existing enrichment
    row = enrich_single_business_adaptive(
        name=name,
        region=row.get("business_state_region"),
        logger=logger
    )

    # Address retry hook
    row = _phase466_address_retry_hook(row, logger)

    return row

