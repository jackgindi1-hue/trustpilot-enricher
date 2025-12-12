# =========================
# Apollo API Client
# CRITICAL: Apollo requires X-Api-Key header (not json api_key)
# =========================

import os
import requests
from typing import Optional, Dict, Any

APOLLO_BASE = "https://api.apollo.io"

def apollo_headers() -> Dict[str, str]:
    key = os.getenv("APOLLO_API_KEY", "").strip()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Api-Key": key,  # <-- REQUIRED BY APOLLO
    }

def apollo_enrich_org_by_domain(domain: str, timeout: int = 20) -> Optional[Dict[str, Any]]:
    """
    Returns Apollo org enrichment payload or None.
    NOTE: Apollo requires X-Api-Key header; NOT a json "api_key".
    """
    key = os.getenv("APOLLO_API_KEY", "").strip()
    if not key:
        return None

    domain = (domain or "").strip().lower()
    if not domain:
        return None

    # Try enrich endpoint first (best)
    url = f"{APOLLO_BASE}/v1/organizations/enrich"
    payload = {"domain": domain}

    try:
        r = requests.post(url, headers=apollo_headers(), json=payload, timeout=timeout)
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
    except Exception:
        pass

    # Fallback: organizations/search
    url2 = f"{APOLLO_BASE}/v1/organizations/search"
    payload2 = {"q_organization_domains": domain, "page": 1}
    try:
        r2 = requests.post(url2, headers=apollo_headers(), json=payload2, timeout=timeout)
        if r2.status_code == 200:
            try:
                return r2.json()
            except Exception:
                return None
    except Exception:
        pass

    return None
