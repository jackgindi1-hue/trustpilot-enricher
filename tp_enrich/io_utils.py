# tp_enrich/io_utils.py
# IO + input column normalization so pipeline never returns "empty" outputs

import os
from typing import List, Optional
import pandas as pd


PHASE2_COLUMNS = [
    "phase2_bbb_url", "phase2_bbb_names", "phase2_bbb_phone", "phase2_bbb_email", "phase2_bbb_notes",
    "phase2_yp_url", "phase2_yp_names", "phase2_yp_phone", "phase2_yp_email", "phase2_yp_notes",
    "phase2_oc_url", "phase2_oc_names", "phase2_oc_company_number", "phase2_oc_status", "phase2_oc_notes",
]

LEGACY_COLUMNS = ["bbb_url", "yellowpages_url", "yelp_url"]


def _first_existing_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols_lc = {c.lower(): c for c in df.columns}
    for c in candidates:
        hit = cols_lc.get(c.lower())
        if hit:
            return hit
    return None


def _ensure_col(df: pd.DataFrame, target: str, candidates: List[str], default: str = "") -> None:
    """
    Ensure df[target] exists. If missing, copy from the first matching candidate column; else create default.
    """
    if target in df.columns:
        return
    src = _first_existing_col(df, candidates)
    if src:
        df[target] = df[src].astype(str)
    else:
        df[target] = default


def load_input_csv(path: str) -> pd.DataFrame:
    """
    Must return a pandas DataFrame (NOT tuple).
    Also normalizes common header aliases so enrichment actually runs.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input CSV not found: {path}")

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]

    # Always have row_id
    if "row_id" not in df.columns:
        df["row_id"] = [str(i + 1) for i in range(len(df))]

    # ---- CRITICAL: business/company name (drives enrichment loop) ----
    # Your uploads vary a lot; we normalize to `business_name`
    _ensure_col(
        df,
        "business_name",
        candidates=[
            "business_name", "business", "company", "company_name", "merchant", "merchant_name",
            "account_name", "name", "display_name", "tp_business", "trustpilot_business",
        ],
        default="",
    )

    # ---- Trustpilot-ish metadata (optional but helps) ----
    _ensure_col(df, "review_date", candidates=["review_date", "date", "created_at"], default="")
    _ensure_col(df, "source", candidates=["source", "platform"], default="trustpilot")

    # ---- raw_display_name (pipeline step 2 expects this) ----
    # If your file isn't reviewer-focused, we still set it to business_name so it won't crash.
    _ensure_col(
        df,
        "raw_display_name",
        candidates=["raw_display_name", "reviewer_name", "reviewer", "author", "contact_name", "name"],
        default="",
    )
    if not df["raw_display_name"].astype(str).str.strip().any():
        # fallback: use business_name to keep classifier happy
        df["raw_display_name"] = df["business_name"].astype(str)

    # ---- domain/website aliases (helps email enrichment) ----
    _ensure_col(df, "website", candidates=["website", "site", "url", "homepage"], default="")
    _ensure_col(df, "domain", candidates=["domain", "root_domain"], default="")

    # If website exists but domain missing, derive a cheap domain (no heavy parsing)
    if "domain" in df.columns and "website" in df.columns:
        needs_domain = df["domain"].astype(str).str.strip().eq("")
        if needs_domain.any():
            w = df.loc[needs_domain, "website"].astype(str).str.strip()
            # strip protocol + path
            w = w.str.replace(r"^https?://", "", regex=True)
            w = w.str.replace(r"/.*$", "", regex=True)
            df.loc[needs_domain, "domain"] = w

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
