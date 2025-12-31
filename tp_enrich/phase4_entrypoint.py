""" 
PHASE 4 ENTRYPOINT — SHARED BY CSV UPLOAD AND PHASE 5

This file provides a SINGLE entrypoint that BOTH flows use.
It calls the EXACT SAME function as the CSV upload route.

CSV Upload Route uses:
    from tp_enrich.pipeline import run_pipeline
    run_pipeline(input_path, output_path, cache_path, config={})

This entrypoint wraps that function for in-memory rows.

NO NEW LOGIC.
NO FILTERING.
NO SCHEMA CHANGES.
SAME FUNCTION, SAME BEHAVIOR.
"""
import os
import tempfile
from typing import List, Dict, Any
import pandas as pd

# THE EXACT SAME IMPORT AS CSV UPLOAD ROUTE (api_server.py line 21)
from tp_enrich.pipeline import run_pipeline


def run_phase4_exact(rows: List[Dict[str, Any]], config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    Run the EXACT SAME enrichment pipeline as CSV upload.

    This function:
    1. Writes rows to a temp CSV (same as CSV upload writes uploaded file)
    2. Calls run_pipeline() with file paths (EXACT SAME AS CSV UPLOAD)
    3. Reads the enriched CSV back to rows
    4. Returns enriched rows (NO FILTERING, NO CHANGES)

    Args:
        rows: List of dicts (from Apify or any source)
        config: Optional config dict (same as CSV upload config)

    Returns:
        List of dicts with enrichment columns added
        EXACTLY what run_pipeline() produces - NO MODIFICATIONS
    """
    if not rows:
        return []

    config = config or {}

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.csv")
        output_path = os.path.join(tmpdir, "output.csv")
        cache_path = os.path.join(tmpdir, "cache.json")

        # Write rows to temp CSV (same as CSV upload writes file to disk)
        df = pd.DataFrame(rows)
        df.to_csv(input_path, index=False, encoding='utf-8')

        # CALL THE EXACT SAME FUNCTION AS CSV UPLOAD (api_server.py line 164)
        # This is: run_pipeline(str(input_path), str(output_path), str(cache_path), config=config)
        stats = run_pipeline(
            input_csv_path=str(input_path),
            output_csv_path=str(output_path),
            cache_file=str(cache_path),
            config=config
        )

        # Read enriched CSV back to rows (same as what FileResponse would return)
        if not os.path.exists(output_path):
            raise RuntimeError("run_pipeline did not produce output file")

        enriched_df = pd.read_csv(output_path, encoding='utf-8')
        enriched_rows = enriched_df.to_dict('records')

        return enriched_rows
