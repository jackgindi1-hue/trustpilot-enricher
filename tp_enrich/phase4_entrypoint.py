"""
PHASE 4 ENTRYPOINT — CALLS THE REAL CSV UPLOAD PIPELINE

CSV Upload (api_server.py line 21, 164) uses:
    from tp_enrich.pipeline import run_pipeline
    stats = run_pipeline(str(input_path), str(output_path), str(cache_path), config=config)

This wrapper calls the EXACT SAME function and returns the OUTPUT rows
loaded from the pipeline's enriched.csv (not the input rows).
"""
from typing import List, Dict, Any
import os
import tempfile
import pandas as pd

# THE EXACT SAME IMPORT AS CSV UPLOAD (api_server.py line 21)
from tp_enrich.pipeline import run_pipeline


def run_phase4_exact(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Calls the SAME Phase 4 pipeline used by CSV upload and returns the OUTPUT rows
    loaded from the pipeline's enriched.csv (not the input rows).
    """
    if not rows:
        print("PHASE4_ENTRYPOINT: No rows to process")
        return []

    tmpdir = tempfile.mkdtemp()
    input_csv = os.path.join(tmpdir, "input.csv")
    output_csv = os.path.join(tmpdir, "enriched.csv")
    cache_json = os.path.join(tmpdir, "cache.json")

    # Write input rows to CSV
    df_in = pd.DataFrame(rows)
    df_in.to_csv(input_csv, index=False, encoding='utf-8')

    print(f"PHASE4_ENTRYPOINT_INPUT rows={len(rows)} path={input_csv}")
    print(f"PHASE4_ENTRYPOINT_INPUT_COLS cols={list(df_in.columns)[:20]}")

    # ======================================================================
    # CALL THE EXACT SAME FUNCTION AS CSV UPLOAD (api_server.py line 164)
    # ======================================================================
    stats = run_pipeline(
        str(input_csv),
        str(output_csv),
        str(cache_json),
        config={}
    )

    print(f"PHASE4_ENTRYPOINT_PIPELINE_DONE stats={stats} output_csv={output_csv} exists={os.path.exists(output_csv)}")

    # ======================================================================
    # CRITICAL: Read the OUTPUT enriched.csv (not the input rows!)
    # ======================================================================
    if not os.path.exists(output_csv):
        raise RuntimeError(f"Phase4 pipeline did not write output_csv: {output_csv}")

    df_out = pd.read_csv(output_csv, encoding='utf-8')
    out_rows = df_out.to_dict(orient="records")

    print(f"PHASE4_ENTRYPOINT_OUTPUT rows={len(out_rows)} cols={list(df_out.columns)[:40]}")

    return out_rows
