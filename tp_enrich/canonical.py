# ============================================================
# PHASE 4.5 FINAL LOCK — CANONICAL ENTITY MATCH + OC GUARD
#
# GOAL:
# - ONE canonical business decision per row
# - All providers must pass entity_match >= 0.80
# - OpenCorporates ONLY when state is known
# - Deterministic, auditable, no guessing
# ============================================================

from typing import Dict, Any, Optional, Tuple
from tp_enrich.entity_match import pick_best


def choose_canonical_business(
    row: Dict[str, Any],
    google_hit: Optional[Dict[str, Any]] = None,
    yelp_hit: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Choose ONE canonical business from available providers.

    Returns:
        (canonical_dict, match_metadata)

    canonical_dict contains:
        - source: "google" or "yelp"
        - name, state, city, address, domain, phone, website

    match_metadata contains:
        - best_score, all_scores, passed_threshold, reason
    """
    # Build query from row
    query = {
        "name": row.get("business_name") or row.get("company_search_name") or row.get("raw_display_name"),
        "state": row.get("business_state_region") or row.get("state_region"),
        "city": row.get("business_city") or row.get("city"),
        "address": row.get("business_address") or row.get("address"),
        "domain": row.get("business_domain") or row.get("domain") or row.get("company_domain"),
        "phone": row.get("business_phone") or row.get("primary_phone") or row.get("phone"),
    }

    # Build candidate list
    candidates = []

    if google_hit:
        candidates.append({
            "source": "google",
            "name": google_hit.get("name") or google_hit.get("business_name"),
            "state": google_hit.get("state_region") or google_hit.get("state"),
            "city": google_hit.get("city"),
            "address": google_hit.get("address"),
            "domain": google_hit.get("domain"),
            "phone": google_hit.get("phone"),
            "website": google_hit.get("website"),
            "lat": google_hit.get("lat"),
            "lng": google_hit.get("lng") or google_hit.get("lon"),
            "place_id": google_hit.get("place_id"),
        })

    if yelp_hit:
        candidates.append({
            "source": "yelp",
            "name": yelp_hit.get("name") or yelp_hit.get("business_name"),
            "state": yelp_hit.get("state_region") or yelp_hit.get("state"),
            "city": yelp_hit.get("city"),
            "address": yelp_hit.get("address"),
            "domain": yelp_hit.get("domain"),
            "phone": yelp_hit.get("phone"),
            "website": yelp_hit.get("website"),
            "rating": yelp_hit.get("rating"),
            "review_count": yelp_hit.get("review_count"),
        })

    # Pick best candidate (≥80% threshold)
    result = pick_best(query, candidates, threshold=0.80)

    if not result["passed_threshold"]:
        return None, result

    return result["chosen"], result


def apply_canonical_to_row(
    row: Dict[str, Any],
    canonical: Dict[str, Any],
    match_meta: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Apply canonical business data to row, preferring canonical over existing.

    Args:
        row: Business row dict
        canonical: Canonical business dict from choose_canonical_business
        match_meta: Match metadata from choose_canonical_business

    Returns:
        Updated row dict
    """
    if not canonical:
        return row

    # Record canonical source and score
    row["canonical_source"] = canonical.get("source", "unknown")
    row["canonical_match_score"] = match_meta.get("best_score", 0.0)

    # Apply canonical data (prefer canonical over existing)
    if canonical.get("state"):
        row["business_state_region"] = canonical["state"]

    if canonical.get("city"):
        row["business_city"] = canonical["city"]

    if canonical.get("address"):
        row["business_address"] = canonical["address"]

    if canonical.get("domain"):
        row["business_domain"] = canonical["domain"]
        row["company_domain"] = canonical["domain"]

    if canonical.get("phone"):
        row["business_phone"] = canonical["phone"]
        row["primary_phone"] = canonical["phone"]

    if canonical.get("website"):
        row["business_website"] = canonical["website"]

    # Store additional metadata
    if canonical.get("place_id"):
        row["google_place_id"] = canonical["place_id"]

    if canonical.get("lat") and canonical.get("lng"):
        row["business_lat"] = canonical["lat"]
        row["business_lng"] = canonical["lng"]

    return row


def should_run_opencorporates(row: Dict[str, Any]) -> bool:
    """
    HARD GUARD: Only run OpenCorporates when state is known.

    Args:
        row: Business row dict

    Returns:
        True if state is known and valid, False otherwise
    """
    state = (row.get("business_state_region") or "").strip()

    # Must have state and be 2-3 chars (US state codes)
    if not state or len(state) not in (2, 3):
        return False

    # State must be uppercase alpha (CA, NY, etc.)
    if not state.replace("-", "").isalpha():
        return False

    return True
