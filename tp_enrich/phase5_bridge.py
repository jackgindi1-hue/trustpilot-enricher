"""
PHASE 5 BRIDGE

Calls the existing LOCKED Phase 4 pipeline without modifying it.
Auto-detects the Phase 4 enrich function from common module locations.
"""
from typing import Callable, Dict, Any, List, Optional
import importlib


class Phase5BridgeError(RuntimeError):
    pass


# ============================================================================
# PHASE 5 SCHEMA FIX: Force Apify rows to match CSV-upload schema
# ============================================================================
def _phase5_force_csv_schema(rows: List[dict]) -> List[dict]:
    """
    Force Phase 5 (Apify) rows to match the CSV-upload schema that Phase 4 expects.
    This fixes logs showing name=<NA> and row_id=None, which breaks person-vs-business
    classification and causes garbage anchor discovery (e.g., 411.com).
    """
    fixed = []
    for r in rows or []:
        rr = dict(r or {})

        # Phase 4 classification expects a stable "name" like CSV uploads provide.
        candidate = (
            rr.get("name")
            or rr.get("raw_display_name")
            or rr.get("consumer.displayname")
            or rr.get("company_search_name")
            or ""
        )
        candidate = str(candidate).strip()

        # Phase 4 expects a stable row_id (CSV uploads provide it).
        rid = rr.get("row_id") or rr.get("review_id") or rr.get("id") or ""
        rid = str(rid).strip() or None

        rr["name"] = candidate if candidate else None
        rr["row_id"] = rid
        rr["run_id"] = rr.get("run_id") or "phase5_apify"

        fixed.append(rr)
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
