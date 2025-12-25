# =========================
# Snov.io API Client
# CRITICAL: Must get OAuth access_token first, then use Bearer token
# =========================

import os
import requests
from typing import Optional, Dict, Any, List

SNOV_BASE = "https://api.snov.io"

def snov_get_access_token(timeout: int = 20) -> Optional[str]:
    client_id = os.getenv("SNOV_CLIENT_ID", "").strip()
    client_secret = os.getenv("SNOV_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    url = f"{SNOV_BASE}/v1/oauth/access_token"
    payload = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}

    try:
        r = requests.post(url, data=payload, timeout=timeout)
        if r.status_code != 200:
            return None
        try:
            data = r.json()
            return data.get("access_token")
        except Exception:
            return None
    except Exception:
        return None

def snov_headers(token: str) -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

def snov_domain_emails(domain: str, timeout: int = 25) -> List[Dict[str, Any]]:
    """
    Returns a list of email objects (may be empty).
    Uses OAuth access_token then calls v2 endpoint.
    """
    token = snov_get_access_token(timeout=timeout)
    if not token:
        return []

    domain = (domain or "").strip().lower()
    if not domain:
        return []

    # Snov v2 domain-emails endpoint (works with Bearer token).
    # If Snov changes this path later, this is the ONLY place to adjust.
    url = f"{SNOV_BASE}/v2/domain/emails"
    params = {"domain": domain}

    try:
        r = requests.get(url, headers=snov_headers(token), params=params, timeout=timeout)
        if r.status_code != 200:
            return []

        try:
            data = r.json()
        except Exception:
            return []

        # Common shapes: {"data":[...]} or {"emails":[...]} or direct list
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                return data["data"]
            if isinstance(data.get("emails"), list):
                return data["emails"]
        return []
    except Exception:
        return []
