# tp_enrich/io_utils.py
# Clean rebuild (fixes SyntaxError caused by corrupted file contents)

import csv
import os
from typing import Dict, List, Tuple, Any, Optional


# Add any columns you want guaranteed in the output CSV.
# IMPORTANT: This must be a NORMAL Python list (no "\n" strings in the file).
PHASE2_COLUMNS = [
    # Phase 2 "data" columns (keep minimal + useful)
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

# Optional legacy / compatibility columns (ONLY if your pipeline already uses them)
LEGACY_COLUMNS = [
    "bbb_url",
    "yellowpages_url",
    "yelp_url",
]


def load_input_csv(path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Returns (rows, fieldnames).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input CSV not found: {path}")

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [row for row in reader]
    return rows, fieldnames


def get_output_schema(input_fieldnames: List[str]) -> List[str]:
    """
    Output schema = input columns + guaranteed extras (phase2 + legacy),
    without duplicates, preserving order.
    """
    out: List[str] = []
    seen = set()

    def add(cols: List[str]) -> None:
        for c in cols:
            if c and c not in seen:
                seen.add(c)
                out.append(c)

    add(input_fieldnames)
    add(PHASE2_COLUMNS)
    add(LEGACY_COLUMNS)

    return out


def write_output_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    """
    Writes rows to CSV. Ensures every row has every field.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            safe_row = {k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames}
            writer.writerow(safe_row)
