# ============================================================
# ENTITY MATCHING: Fuzzy key generation for deduplication
# ============================================================
import re
from typing import Optional, Dict, Any
from tp_enrich.phase0_gating import domain_from_url

_WS = re.compile(r"\s+")
_NON = re.compile(r"[^a-z0-9]+")

def normalize_company_key(name: str, google_payload: Optional[Dict[str, Any]] = None) -> str:
    """
    Lightweight entity matching key:
    name + (state if known) + (domain if known)
    This reduces duplicate enrich calls for messy naming.
    """
    n = (name or "").lower().strip()
    n = _WS.sub(" ", n)
    n = _NON.sub("", n)

    state = ""
    domain = ""
    if google_payload:
        state = (google_payload.get("state_region") or google_payload.get("state") or "").lower().strip()
        domain = domain_from_url(google_payload.get("website") or "") or ""

    key = f"{n}|{state}|{domain}"
    return key[:200]
