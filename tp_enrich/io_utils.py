# tp_enrich/io_utils.py
# DataFrame-based IO utils (matches pipeline.py expectation: df.columns)

import os
from typing import List, Dict, Any
import pandas as pd


# Keep these columns guaranteed in output so Phase 2 can write data safely.
PHASE2_COLUMNS = [
    "phase2_bbb_url",
    "phase2_bbb_names",
    "phase2_bbb_phone",
    "phase2_bbb_email",
    "phase2_bbb_notes",

    "phase2_yp_url",
    "phase2_yp_names",
    "phase2_yp_phone",
    "phase2_yp_email",
    "phase2_yp_notes",

    "phase2_oc_url",
    "phase2_oc_names",
    "phase2_oc_company_number",
    "phase2_oc_status",
    "phase2_oc_notes",
]

# Optional legacy columns if other parts of the code reference them
LEGACY_COLUMNS = [
    "bbb_url",
    "yellowpages_url",
    "yelp_url",
]


def load_input_csv(path: str) -> pd.DataFrame:
    """
    Pipeline expects a pandas DataFrame.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input CSV not found: {path}")

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    # Normalize column names lightly (no breaking changes)
    df.columns = [c.strip() for c in df.columns]
    return df


def get_output_schema(df: pd.DataFrame) -> List[str]:
    """
    Output schema = existing df columns + guaranteed extras, no duplicates.
    """
    cols: List[str] = list(df.columns)

    def add_many(extra: List[str]) -> None:
        for c in extra:
            if c not in cols:
                cols.append(c)

    add_many(PHASE2_COLUMNS)
    add_many(LEGACY_COLUMNS)
    return cols


def write_output_csv(path: str, df: pd.DataFrame, schema: List[str]) -> None:
    """
    Ensure df has all schema columns, then write.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    for c in schema:
        if c not in df.columns:
            df[c] = ""

    # Keep schema order
    df = df[schema]
    df.to_csv(path, index=False)
