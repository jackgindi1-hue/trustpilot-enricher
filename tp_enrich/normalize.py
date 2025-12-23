"""
Shared normalization utilities for entity matching
"""

import re
from typing import Optional

_CORP_SUFFIX_RE = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|corp|corp\.|co|co\.|company|pllc|pc|p\.c\.|llp|l\.l\.p\.|plc)\b",
    re.IGNORECASE,
)

def normalize_company_name(name: Optional[str]) -> str:
    """
    Normalizes business name for matching:
    - lower
    - strips punctuation
    - removes common corp suffixes
    - collapses whitespace
    """
    if not name:
        return ""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s&-]", " ", s)          # keep words/spaces/&/-
    s = _CORP_SUFFIX_RE.sub(" ", s)           # remove suffixes
    s = re.sub(r"\s+", " ", s).strip()
    return s
