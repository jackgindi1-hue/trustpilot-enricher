"""
PHASE 5 BRIDGE

Calls the existing LOCKED Phase 4 pipeline without modifying it.
Auto-detects the Phase 4 enrich function from common module locations.
"""
from typing import Callable, Dict, Any, List, Optional
import importlib


class Phase5BridgeError(RuntimeError):
    pass


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
