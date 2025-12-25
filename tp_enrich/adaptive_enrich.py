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