# ============================================================
# ENTITY MATCHING: Fuzzy key generation for deduplication
# ============================================================
import re
from typing import Optional, Dict, Any, Tuple
from tp_enrich.phase0_gating import domain_from_url

_WS = re.compile(r"\s+")
_NON = re.compile(r"[^a-z0-9]+")

def normalize_company_key(name: str, google_payload: Optional[Dict[str, Any]] = None) -> str:
    """
    Lightweight entity matching key:
    name + (state if known) + (domain if known)
    This reduces duplicate enrich calls for messy naming.
    """
    n = (name or "").lower().strip()
    n = _WS.sub(" ", n)
    n = _NON.sub("", n)

    state = ""
    domain = ""
    if google_payload:
        state = (google_payload.get("state_region") or google_payload.get("state") or "").lower().strip()
        domain = domain_from_url(google_payload.get("website") or "") or ""

    key = f"{n}|{state}|{domain}"
    return key[:200]


"""
Entity Matching with 80% confidence and Google verification (PHASE 4.5)

Improves match quality when state is known by using token overlap
and verifying matches against Google Places API.
"""


def _clean_name(s: str) -> str:
    """Normalize business name for matching"""
    s = (s or "").strip().lower()
    s = re.sub(r"[\.\,\(\)\[\]\{\}\-\_]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # light suffix stripping (don't overdo it)
    for suf in [" llc", " inc", " ltd", " corp", " co", " company", " limited"]:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s


def _token_jaccard(a: str, b: str) -> float:
    """Calculate Jaccard similarity between two business names"""
    ta = set(_clean_name(a).split())
    tb = set(_clean_name(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / max(union, 1)


def propose_better_query(
    raw_name: str,
    state: Optional[str],
    google_payload: Optional[Dict[str, Any]] = None,
    yelp_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float, str]:
    """
    "AI-ish" matcher: chooses the best search string we already know.
    - If Google/Yelp already matched a name, we trust that (highest).
    - Else use normalized business name.
    Returns: (proposed_query, confidence_0_1, reason)
    """
    # If Google matched, use its formatted name
    gname = (google_payload or {}).get("name") or (google_payload or {}).get("business_name")
    if gname and _clean_name(gname):
        return gname, 0.95, "google_name"

    yname = (yelp_payload or {}).get("name") or (yelp_payload or {}).get("business_name")
    if yname and _clean_name(yname):
        return yname, 0.90, "yelp_name"

    # fallback: normalized original
    base = raw_name or ""
    return base.strip(), 0.70, "raw_name"


def should_try_entity_match(state: Optional[str]) -> bool:
    """Only run when state is known/found (user requirement)"""
    st = (state or "").strip()
    return bool(st) and len(st) in (2, 3)  # US abbreviations, etc.


def entity_match_80_verified(
    raw_name: str,
    state: str,
    google_findplace_fn,
    logger=None,
) -> Dict[str, Any]:
    """
    Runs an entity match attempt and only "accepts" if Google returns a place_id in the SAME state.

    Args:
        raw_name: Original business name
        state: State code (e.g., "CA", "NY")
        google_findplace_fn: Function that takes query string and returns Google Places result
        logger: Optional logger

    Returns dict with:
        - matched: bool
        - proposed_query: str
        - confidence: float
        - verified_by_google: bool
        - google_place: google payload (optional)
    """
    proposed, base_conf, reason = propose_better_query(raw_name, state)
    if not proposed:
        return {"matched": False, "reason": "empty_name"}

    # Ask Google to findplace using proposed query + state hint
    query = f"{proposed} {state}".strip()
    gp = None
    try:
        gp = google_findplace_fn(query)
    except Exception as e:
        if logger:
            logger.warning(f"ENTITY_MATCH: google_findplace failed: {e}")
        return {"matched": False, "reason": "google_findplace_error"}

    # Verify: must have place_id and same state_region
    place_id = (gp or {}).get("place_id")
    st = ((gp or {}).get("state_region") or (gp or {}).get("state") or "").strip()
    verified = bool(place_id) and (st.upper() == state.upper())

    # Score: combine token overlap with base_conf, but accept only if >=0.80 and verified
    overlap = _token_jaccard(raw_name, (gp or {}).get("name") or proposed)
    score = (0.6 * base_conf) + (0.4 * overlap)

    accepted = bool(verified) and (score >= 0.80)

    if logger:
        logger.info(
            f"ENTITY_MATCH: proposed='{proposed}' reason={reason} score={score:.2f} verified={verified} accepted={accepted}"
        )

    return {
        "matched": accepted,
        "proposed_query": proposed,
        "confidence": float(score),
        "verified_by_google": bool(verified),
        "google_place": gp if accepted else None,
        "reason": reason,
    }
