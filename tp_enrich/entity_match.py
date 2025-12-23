# ============================================================
# ENTITY MATCHING: Fuzzy key generation for deduplication
# ============================================================
import re
from typing import Optional, Dict, Any, Tuple, List
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
    s = re.sub(r"[\.\ ,\(\)\[\]\{\}\-\_]+", " ", s)
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
# ============================================================
# PHASE 4.5 FINAL LOCK — CANONICAL ENTITY MATCHER
# ============================================================
# ============================================================
# PHASE 4.6.3 — CANONICAL THRESHOLD OVERRIDE (SAFE)
# Accept >=0.80 normally
# Accept >=0.75 only if domain OR phone matches exactly
# ============================================================
DEFAULT_THRESHOLD = 0.80
SOFT_THRESHOLD = 0.75
def passes_threshold(best_score: float, meta: Dict[str, Any]) -> bool:
    """
    Smart threshold with override for exact domain/phone matches.
    Args:
        best_score: Overall match score (0.0-1.0)
        meta: Match metadata with exact match flags
    Returns:
        True if passes threshold (0.80 default, 0.75 if domain/phone exact)
    """
    phone_ok = bool(meta.get("phone_match_exact"))
    domain_ok = bool(meta.get("domain_match_exact"))
    # Pass if meets default threshold
    if best_score >= DEFAULT_THRESHOLD:
        return True
    # Pass if meets soft threshold AND has exact domain or phone match
    if best_score >= SOFT_THRESHOLD and (phone_ok or domain_ok):
        return True
    return False
def _score_candidate(query: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    """
    Score a candidate against the query business.
    Returns score 0.0-1.0 based on name similarity, state match, domain match.
    Scoring:
    - Name match (Jaccard): 60%
    - State match (exact): 20%
    - Domain match (exact): 10%
    - Phone match (normalized): 10%
    """
    # Name similarity (60% weight)
    q_name = query.get("name", "")
    c_name = candidate.get("name", "")
    name_score = 0.0
    if q_name and c_name:
        name_score = _token_jaccard(q_name, c_name)
    # State match (20% weight) - exact match required
    q_state = (query.get("state") or "").strip().upper()
    c_state = (candidate.get("state") or "").strip().upper()
    state_score = 1.0 if (q_state and c_state and q_state == c_state) else 0.0
    # Domain match (10% weight) - exact match
    q_domain = (query.get("domain") or "").strip().lower()
    c_domain = (candidate.get("domain") or "").strip().lower()
    domain_score = 1.0 if (q_domain and c_domain and q_domain == c_domain) else 0.0
    # Phone match (10% weight) - normalized digits
    q_phone = re.sub(r"\D", "", query.get("phone") or "")
    c_phone = re.sub(r"\D", "", candidate.get("phone") or "")
    phone_score = 0.0
    if q_phone and c_phone and len(q_phone) >= 10 and len(c_phone) >= 10:
        # Compare last 10 digits (US phone numbers)
        if q_phone[-10:] == c_phone[-10:]:
            phone_score = 1.0
    # Total weighted score
    total_score = (0.6 * name_score) + (0.2 * state_score) + (0.1 * domain_score) + (0.1 * phone_score)
    # PHASE 4.6.3: Track exact matches for threshold override
    domain_match_exact = (domain_score == 1.0)
    phone_match_exact = (phone_score == 1.0)
    # PHASE 4.6: Return tuple (total_score, component_scores) for diagnostic analysis
    return (total_score, {
        "score_name": name_score,
        "score_state": state_score,
        "score_domain": domain_score,
        "score_phone": phone_score,
        "domain_match_exact": domain_match_exact,
        "phone_match_exact": phone_match_exact,
    })
def pick_best(
    query: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    threshold: float = 0.80
) -> Dict[str, Any]:
    """
    Choose the best candidate that meets the threshold.
    Args:
        query: Business query dict with name, state, city, address, domain, phone
        candidates: List of candidate dicts from Google, Yelp, etc.
        threshold: Minimum score required (default 0.80)
    Returns:
        {
            "chosen": candidate dict or None,
            "best_score": float,
            "all_scores": list of (source, score) tuples,
            "passed_threshold": bool
        }
    """
    if not candidates:
        return {
            "chosen": None,
            "best_score": 0.0,
            "all_scores": [],
            "passed_threshold": False,
            "reason": "no_candidates"
        }
    # Score all candidates
    scored = []
    for candidate in candidates:
        total_score, components = _score_candidate(query, candidate)  # PHASE 4.6: Unpack tuple
        scored.append((candidate, total_score, components))
    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)
    best_candidate, best_score, best_components = scored[0]
    all_scores = [(c.get("source", "unknown"), s) for c, s, _ in scored]
    # PHASE 4.6.3: Use smart threshold with override for exact domain/phone matches
    passed = passes_threshold(best_score, best_components)
    # Build reason string
    if passed:
        if best_score >= DEFAULT_THRESHOLD:
            reason = "accepted_default_threshold"
        else:
            reason = f"accepted_soft_threshold_exact_match"
    else:
        reason = f"below_threshold_{SOFT_THRESHOLD}"
    # PHASE 4.6: Include component scores in result for diagnostic analysis
    return {
        "chosen": best_candidate if passed else None,
        "best_score": best_score,
        "all_scores": all_scores,
        "passed_threshold": passed,
        "reason": reason,
        # Component scores for tuning threshold intelligently
        "score_name": best_components.get("score_name", 0.0),
        "score_state": best_components.get("score_state", 0.0),
        "score_domain": best_components.get("score_domain", 0.0),
        "score_phone": best_components.get("score_phone", 0.0),
        # PHASE 4.6.3: Exact match flags for threshold override
        "phone_match_exact": best_components.get("phone_match_exact", False),
        "domain_match_exact": best_components.get("domain_match_exact", False),
    }
