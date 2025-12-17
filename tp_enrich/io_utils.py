# tp_enrich/io_utils.py
# DataFrame-based IO utils + input column normalization (anti-debug-hell)

import os
from typing import List
import pandas as pd


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

LEGACY_COLUMNS = [
    "bbb_url",
    "yellowpages_url",
    "yelp_url",
]

# Columns pipeline logic expects (minimum safety set)
REQUIRED_INPUT_COLUMNS = [
    "raw_display_name",  # pipeline Step 2 expects this
]


def _first_existing_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    cols_lc = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in cols_lc:
            return cols_lc[c.lower()]
    return None


def load_input_csv(path: str) -> pd.DataFrame:
    """
    Must return pandas DataFrame. Also ensures required columns exist
    even if the uploaded CSV uses different header names.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input CSV not found: {path}")

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]

    # ---- REQUIRED COLUMN FIXES ----
    # Guarantee raw_display_name exists by aliasing from common input headers.
    if "raw_display_name" not in df.columns:
        src = _first_existing_col(df, [
            # Trustpilot-ish / review-ish
            "display_name", "reviewer_name", "reviewer", "author", "name",
            # your sheets often contain business name; better than crashing
            "business_name", "company", "company_name",
            # fallbacks
            "raw_name", "raw_display",
        ])
        if src:
            df["raw_display_name"] = df[src].astype(str)
        else:
            # last resort: create empty column so pipeline won't crash
            df["raw_display_name"] = ""

    # Also ensure row_id exists if pipeline expects it (safe)
    if "row_id" not in df.columns:
        df["row_id"] = [str(i + 1) for i in range(len(df))]

    return df


def get_output_schema(df: pd.DataFrame) -> List[str]:
    cols: List[str] = list(df.columns)

    def add_many(extra: List[str]) -> None:
        for c in extra:
            if c not in cols:
                cols.append(c)

    add_many(PHASE2_COLUMNS)
    add_many(LEGACY_COLUMNS)
    return cols


def write_output_csv(path: str, df: pd.DataFrame, schema: List[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    for c in schema:
        if c not in df.columns:
            df[c] = ""

    df = df[schema]
    df.to_csv(path, index=False)
