"""
PHASE 5 BRIDGE

Calls the existing LOCKED Phase 4 pipeline without modifying it.
Auto-detects the Phase 4 enrich function from common module locations.
"""
from typing import Callable, Dict, Any, List, Optional
import importlib
import hashlib


class Phase5BridgeError(RuntimeError):
    pass


# ============================================================================
# PHASE 5 SCHEMA FIX: Force exact CSV-upload schema at handoff
# ============================================================================

def _clean_blank(v):
    """Convert blank/NA values to empty string."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in {"<na>", "na", "nan", "none", "null"}:
        return ""
    return s


def _pick_name(rr: dict) -> str:
    """Try ALL possible places Apify might store reviewer name."""
    candidates = [
        rr.get("consumer.displayname"),
        rr.get("raw_display_name"),
        rr.get("company_search_name"),
        rr.get("name"),
        rr.get("reviewerName"),
        rr.get("reviewer"),
        rr.get("author"),
        rr.get("userName"),
    ]

    # Some actors store nested consumer objects
    consumer = rr.get("consumer")
    if isinstance(consumer, dict):
        candidates.insert(0, consumer.get("displayname"))
        candidates.insert(0, consumer.get("displayName"))

    for c in candidates:
        s = _clean_blank(c)
        if s:
            return s
    return ""


def _stable_row_id(rr: dict) -> str:
    """Generate deterministic ID if review_id is missing."""
    base = "|".join([
        _clean_blank(rr.get("reviewed_company_url") or rr.get("company_url")),
        _clean_blank(rr.get("review_date") or rr.get("date")),
        _clean_blank(rr.get("review_rating")),
        _clean_blank((rr.get("review_text") or ""))[:200],
        _pick_name(rr),
    ])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _phase5_force_csv_schema(rows):
    """
    Convert Phase 5 scraped rows into the SAME input schema as the working CSV upload.
    IMPORTANT: Do NOT drop everything. Only skip rows that truly have no reviewer name.
    """
    fixed = []
    rows = rows or []

    # DEBUG: show what keys actually exist on first row
    if rows:
        try:
            first = dict(rows[0] or {})
            print("PHASE5_DEBUG_FIRST_ROW_KEYS", sorted(list(first.keys()))[:80])
        except Exception:
            pass

    for r in rows:
        rr = dict(r or {})

        reviewer = _pick_name(rr)
        if not reviewer:
            # Skip only truly nameless rows
            continue

        # IMPORTANT: This is the exact schema your CSV upload flow uses
        rr["consumer.displayname"] = reviewer
        rr["raw_display_name"] = reviewer

        # company_search_name exists in your working enriched CSV; seed it to reviewer initially
        rr["company_search_name"] = reviewer

        # date column is present in your working CSV upload (separate from review_date)
        rr["date"] = _clean_blank(rr.get("date")) or _clean_blank(rr.get("review_date"))

        # stable identifiers required by pipeline
        rid = _clean_blank(rr.get("row_id")) or _clean_blank(rr.get("review_id")) or _clean_blank(rr.get("id"))
        rr["row_id"] = rid if rid else _stable_row_id(rr)

        rr["run_id"] = _clean_blank(rr.get("run_id")) or "phase5_apify"

        fixed.append(rr)

    print("PHASE5_DEBUG_ROWS_IN_OUT", {"in": len(rows), "out": len(fixed)})
    if fixed:
        print("PHASE5_DEBUG_SAMPLE_NAME", fixed[0].get("consumer.displayname"), "row_id=", fixed[0].get("row_id"))

    return fixed


def _try_import(path: str):
    try:
        return importlib.import_module(path)
    except Exception:
        return None


def _find_callable() -> Optional[Callable[[List[dict]], List[dict]]]:
    """
    Attempts to locate an existing Phase 4 "enrich rows" function in common places.
    We do NOT change Phase 4 — we only call it.
    """
    candidates = [
        # Common patterns we've used in this project
        ("tp_enrich.pipeline", "enrich_rows"),
        ("tp_enrich.pipeline", "run_enrich"),
        ("tp_enrich.enrich", "enrich_rows"),
        ("tp_enrich.enrich", "run_enrich"),
        ("tp_enrich.jobs", "enrich_rows"),
        ("tp_enrich.jobs", "run_enrich"),
        ("tp_enrich.routes_jobs", "enrich_rows"),
        ("tp_enrich.routes_jobs", "run_enrich"),
        ("tp_enrich.api", "enrich_rows"),
        ("tp_enrich.api", "run_enrich"),
        ("tp_enrich.app", "enrich_rows"),
        ("tp_enrich.app", "run_enrich"),
    ]

    for mod_path, fn_name in candidates:
        mod = _try_import(mod_path)
        if not mod:
            continue
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            return fn

    return None


def call_phase4_enrich_rows(rows: List[dict]) -> List[dict]:
    """
    Calls Phase 4 enrichment on in-memory rows.
    If we can't auto-detect the callable, we raise a single clear error.
    """
    fn = _find_callable()
    if not fn:
        raise Phase5BridgeError(
            "Could not locate Phase 4 enrich function automatically.\n\n"
            "Fix (NO Phase 4 logic changes):\n"
            "1) Identify the function used by your existing CSV upload flow to enrich rows.\n"
            "2) Expose it as a callable `enrich_rows(rows: List[dict]) -> List[dict]` in a stable module.\n"
            "   Example: tp_enrich/pipeline.py with def enrich_rows(rows): ...\n"
            "3) Or add its module/function to the candidates list in tp_enrich/phase5_bridge.py.\n"
        )

    # PHASE 5 SCHEMA FIX: Force Apify rows to match CSV-upload schema before calling Phase 4
    rows = _phase5_force_csv_schema(rows)

    # Compatibility wrapper: some functions accept (rows, logger) or (rows, options)
    try:
        return fn(rows)
    except TypeError:
        try:
            return fn(rows, None)
        except TypeError:
            try:
                return fn(rows, logger=None)
            except TypeError as e:
                raise Phase5BridgeError(f"Phase 4 enrich callable signature incompatible: {e}")
