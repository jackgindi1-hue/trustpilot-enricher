"""
CSV Utilities

Standalone CSV serialization helpers to avoid circular imports
between phase5_jobs and routes_phase5.
"""
import io
import csv
from typing import List, Dict, Any


def rows_to_csv_bytes(rows: List[Dict[str, Any]]) -> bytes:
    """
    Serialize list-of-dicts to CSV bytes.
    Standalone helper to avoid circular imports.

    Args:
        rows: List of dictionaries to serialize

    Returns:
        CSV bytes (UTF-8 encoded)
    """
    output = io.StringIO()

    # Collect all unique fieldnames from all rows
    fieldnames = []
    seen = set()
    for r in rows or []:
        for k in (r or {}).keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)

    # Write CSV
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows or []:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})

    return output.getvalue().encode("utf-8")
