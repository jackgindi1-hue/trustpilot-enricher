"""
PHASE 4 ENTRYPOINT — CALLS THE REAL CSV UPLOAD PIPELINE + PHASE 6 PREFILL

CSV Upload (api_server.py line 21, 164) uses:
    from tp_enrich.pipeline import run_pipeline
    stats = run_pipeline(str(input_path), str(output_path), str(cache_path), config=config)

This wrapper:
1. Applies Phase 6 prefill (if PHASE6_MODE=shadow|enforce)
2. Calls the EXACT SAME function as CSV Upload
3. Returns the OUTPUT rows loaded from the pipeline's enriched.csv

SAFETY:
- PHASE6_MODE=off (default): no behavior changes
- PHASE6_MODE=shadow: logs only, NO output changes
- PHASE6_MODE=enforce: applies overrides/model prefill BEFORE pipeline
"""
import os
import csv
import json
import tempfile
from typing import List, Dict, Any, Tuple


def _mode() -> str:
    return (os.getenv("PHASE6_MODE") or "off").strip().lower()


# REQUIRED headers FIRST (keeps pipeline behavior closer to the "known-good CSV upload")
REQUIRED_HEADERS = [
    "consumer.displayName",
    "review_text",
    "review_rating",
    "review_date",
    "review_id",
    "reviewed_company_url",
    "reviewed_company_name",
    "source_platform",
    # include these if present, but don't require them
    "company_search_name",
    "raw_display_name",
    "name",
]


def _get_name(row: Dict[str, Any]) -> str:
    """Extract name from row, checking multiple possible columns."""
    v = row.get("consumer.displayName")
    if v is None or str(v).strip() == "":
        v = row.get("consumer.displayname") or row.get("name") or ""
    return (str(v).strip() if v is not None else "")


def _phase6_prefill(rows: List[Dict[str, Any]]) -> Tuple[int, int, int, int]:
    """
    Apply Phase 6 prefill to rows BEFORE pipeline runs.

    Returns: (override_hits, model_hits, would_set, applied)
    SHADOW: logs only, no mutations
    ENFORCE: sets name_classification (+ phase6_* markers)
    """
    mode = _mode()
    if mode not in ("shadow", "enforce"):
        return (0, 0, 0, 0)

    from tp_enrich.phase6 import store as p6_store
    from tp_enrich.phase6 import model as p6_model

    model_art = None
    try:
        model_art = p6_store.load_latest_model()
    except Exception:
        model_art = None

    would = 0
    applied = 0
    override_hit = 0
    model_hit = 0

    for r in rows:
        nm = _get_name(r)
        if not nm:
            continue

        # Never overwrite existing classification
        if str(r.get("name_classification") or "").strip():
            continue

        forced = None
        try:
            forced = p6_store.lookup_override(nm)
        except Exception:
            forced = None

        if forced in ("business", "person"):
            override_hit += 1
            if mode == "shadow":
                would += 1
            else:
                r["name_classification"] = forced
                r["phase6_prefilled"] = "1"
                r["phase6_forced_label"] = forced
                r["phase6_reason"] = "override_exact"
                applied += 1
            continue

        if model_art:
            s = p6_model.score_name(nm, model_art)
            if s.get("label") in ("business", "person") and float(s.get("confidence", 0.0)) >= 0.80:
                model_hit += 1
                if mode == "shadow":
                    would += 1
                else:
                    r["name_classification"] = s["label"]
                    r["phase6_prefilled"] = "1"
                    r["phase6_forced_label"] = s["label"]
                    r["phase6_reason"] = "model_" + ",".join((s.get("reasons") or [])[:3])
                    r["phase6_score"] = s.get("score")
                    applied += 1

    print("PHASE6_PREFILL", {
        "mode": mode,
        "rows": len(rows),
        "override_hits": override_hit,
        "model_hits": model_hit,
        "would_set": would,
        "applied": applied,
    })
    return (override_hit, model_hit, would, applied)


def _rows_to_csv(rows: List[Dict[str, Any]], path: str) -> None:
    """Write rows to CSV with required headers first for schema stability."""
    keys: List[str] = []
    seen: set = set()

    # Required headers first if present OR if we need them for pipeline stability
    for k in REQUIRED_HEADERS:
        if k not in seen:
            keys.append(k)
            seen.add(k)

    # Then union of observed keys (preserve everything else)
    for r in rows:
        for k in r.keys():
            if k not in seen:
                keys.append(k)
                seen.add(k)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for k in keys:
                v = r.get(k, "")
                if v is None:
                    v = ""
                elif isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                out[k] = v
            w.writerow(out)


def _csv_to_rows(path: str) -> List[Dict[str, Any]]:
    """Read CSV to list of dicts."""
    with open(path, "r", newline="", encoding="utf-8", errors="ignore") as f:
        r = csv.DictReader(f)
        return [dict(row) for row in r]


def _postcheck_pipeline_overwrite(out_rows: List[Dict[str, Any]]) -> None:
    """
    Only meaningful in ENFORCE mode where we add phase6_prefilled markers.
    If pipeline drops/overwrites them, you'll see it here.
    """
    mode = _mode()
    if mode != "enforce":
        return
    total = len(out_rows)
    marked = 0
    survived = 0
    for r in out_rows:
        if str(r.get("phase6_prefilled") or "").strip() == "1":
            marked += 1
            forced = str(r.get("phase6_forced_label") or "").strip().lower()
            actual = str(r.get("name_classification") or "").strip().lower()
            if forced and actual == forced:
                survived += 1
    print("PHASE6_POSTCHECK", {
        "mode": mode,
        "out_rows": total,
        "prefilled_marked_rows": marked,
        "prefill_survived_same_label": survived,
        "note": "If marked_rows>0 but survived==0, pipeline is overwriting name_classification (still safe; we'll inject earlier later)."
    })


def run_phase4_exact(rows: List[Dict[str, Any]], config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    SINGLE SHARED ENTRYPOINT:
    - CSV upload flow AND URL->Apify flow call THIS.
    - Calls EXACT pipeline: tp_enrich.pipeline.run_pipeline(input_csv, output_csv, cache_json, config={})

    Args:
        rows: List of row dicts to enrich
        config: Optional config dict to pass to run_pipeline (e.g., lender_name_override, progress_callback)

    Returns:
        List of enriched rows from the pipeline output CSV
    """
    if not rows:
        print("PHASE4_ENTRYPOINT: No rows to process")
        return []

    # Phase 6 prefill (if enabled)
    _phase6_prefill(rows)

    # THE EXACT SAME IMPORT AS CSV UPLOAD (api_server.py line 21)
    from tp_enrich.pipeline import run_pipeline

    with tempfile.TemporaryDirectory(prefix="tp_enrich_p6_") as td:
        input_csv = os.path.join(td, "input.csv")
        output_csv = os.path.join(td, "output.csv")
        cache_json = os.path.join(td, "cache.json")

        print(f"PHASE4_ENTRYPOINT_INPUT rows={len(rows)}")
        _rows_to_csv(rows, input_csv)

        # ======================================================================
        # CALL THE EXACT SAME FUNCTION AS CSV UPLOAD (api_server.py line 164)
        # Pass through config if provided
        # ======================================================================
        pipeline_config = dict(config or {})
        stats = run_pipeline(
            str(input_csv),
            str(output_csv),
            str(cache_json),
            config=pipeline_config
        )

        print("PHASE4_ENTRYPOINT_STATS", stats if isinstance(stats, dict) else {"stats": str(stats)[:500]})

        # ======================================================================
        # CRITICAL: Read the OUTPUT enriched.csv (not the input rows!)
        # ======================================================================
        if not os.path.exists(output_csv):
            raise RuntimeError(f"Phase4 pipeline did not write output_csv: {output_csv}")

        out_rows = _csv_to_rows(output_csv)
        print(f"PHASE4_ENTRYPOINT_OUTPUT rows={len(out_rows)}")

    # Post-check whether pipeline overwrote our prefills
    _postcheck_pipeline_overwrite(out_rows)

    return out_rows
