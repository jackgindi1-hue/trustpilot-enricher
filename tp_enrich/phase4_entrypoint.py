"""
PHASE 4 ENTRYPOINT — HARD-WIRED TO CSV UPLOAD PIPELINE

CSV Upload uses (api_server.py line 21, 164):
    from tp_enrich.pipeline import run_pipeline
    run_pipeline(str(input_path), str(output_path), str(cache_path), config=config)

This wrapper calls the EXACT SAME function for in-memory rows.
NO NEW LOGIC. NO FILTERS. NO SCHEMA TRICKS.
"""
import os
import tempfile
from typing import List, Dict, Any
import pandas as pd

# =====================================================================
# THE EXACT SAME IMPORT AS CSV UPLOAD (api_server.py line 21)
# =====================================================================
from tp_enrich.pipeline import run_pipeline


def run_phase4_exact(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Call the EXACT SAME run_pipeline() function used by CSV upload.

    CSV upload flow:
    1. Write uploaded file to input_path
    2. Call run_pipeline(input_path, output_path, cache_path, config)
    3. Return output_path as FileResponse

    This function does the same:
    1. Write rows to temp input CSV
    2. Call run_pipeline(input_path, output_path, cache_path, config)
    3. Read output CSV back to rows

    NO NEW LOGIC. SAME FUNCTION. SAME BEHAVIOR.
    """
    if not rows:
        return []

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.csv")
        output_path = os.path.join(tmpdir, "enriched.csv")
        cache_path = os.path.join(tmpdir, "cache.json")

        # Write rows to temp CSV (same as CSV upload writes file to disk)
        df = pd.DataFrame(rows)
        df.to_csv(input_path, index=False, encoding='utf-8')

        print(f"PHASE4_ENTRYPOINT_INPUT rows={len(rows)} path={input_path}")

        # =====================================================================
        # CALL THE EXACT SAME FUNCTION AS CSV UPLOAD (api_server.py line 164)
        # =====================================================================
        stats = run_pipeline(
            str(input_path),
            str(output_path),
            str(cache_path),
            config={}
        )

        print(f"PHASE4_ENTRYPOINT_PIPELINE_DONE stats={stats}")

        # Read enriched CSV back to rows (same as what FileResponse returns)
        if not os.path.exists(output_path):
            raise RuntimeError("run_pipeline did not produce output file")

        enriched_df = pd.read_csv(output_path, encoding='utf-8')
        enriched_rows = enriched_df.to_dict('records')

        print(f"PHASE4_ENTRYPOINT_OUTPUT rows={len(enriched_rows)}")

        return enriched_rows
