# ==========================
# tp_enrich/email_enrichment.py
# FULL REPLACEMENT WITH APOLLO FALLBACK
# ==========================
import os
import logging
import requests
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

HUNTER_API_KEY = (os.getenv("HUNTER_API_KEY") or "").strip()
SNOV_CLIENT_ID = (os.getenv("SNOV_CLIENT_ID") or "").strip()
SNOV_CLIENT_SECRET = (os.getenv("SNOV_CLIENT_SECRET") or "").strip()
APOLLO_API_KEY = (os.getenv("APOLLO_API_KEY") or "").strip()


# -------------------------
# HUNTER
# -------------------------
def _hunter_domain_search(domain: str) -> Optional[Dict[str, Any]]:
    if not HUNTER_API_KEY:
        return None

    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 10},
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Hunter error for %s: %s", domain, repr(e))
        return None

    data = (resp.json() or {}).get("data") or {}
    emails = data.get("emails") or []
    if not emails:
        return None

    best = None
    for e in emails:
        conf = int(e.get("confidence") or 0)
        if conf >= 80:
            best = e
            break

    best = best or emails[0]
    primary = best.get("value")
    if not primary:
        return None

    return {
        "primary_email": primary,
        "emails": [x.get("value") for x in emails if x.get("value")],
        "email_source": "hunter",
    }


# -------------------------
# SNOV
# -------------------------
def _snov_get_token() -> Optional[str]:
    if not SNOV_CLIENT_ID or not SNOV_CLIENT_SECRET:
        return None

    try:
        resp = requests.post(
            "https://api.snov.io/v1/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": SNOV_CLIENT_ID,
                "client_secret": SNOV_CLIENT_SECRET,
            },
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Snov token error: %s", repr(e))
        return None

    return (resp.json() or {}).get("access_token")


def _snov_domain_search(domain: str) -> Optional[Dict[str, Any]]:
    token = _snov_get_token()
    if not token:
        return None

    try:
        resp = requests.post(
            "https://api.snov.io/v1/get-domain-emails-with-info",
            headers={"Authorization": f"Bearer {token}"},
            json={"domain": domain, "limit": 100, "type": "all", "lastId": 0},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Snov error for %s: %s", domain, repr(e))
        return None

    emails = (resp.json() or {}).get("emails") or []
    if not emails:
        return None

    all_values = []
    primary = None
    for e in emails:
        val = e.get("email") or e.get("value") or e.get("address")
        if not val:
            continue
        all_values.append(val)
        if not primary:
            primary = val

    if not primary:
        return None

    return {
        "primary_email": primary,
        "emails": all_values,
        "email_source": "snov",
    }


# -------------------------
# APOLLO
# -------------------------
def _apollo_domain_lookup(domain: str) -> Optional[Dict[str, Any]]:
    if not APOLLO_API_KEY:
        return None

    try:
        resp = requests.post(
            "https://api.apollo.io/v1/organizations/search",
            json={
                "api_key": APOLLO_API_KEY,
                "q_organization_domains": domain,
                "page": 1,
                "per_page": 5,
            },
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Apollo error for %s: %s", domain, repr(e))
        return None

    orgs = (resp.json() or {}).get("organizations") or []
    if not orgs:
        return None

    emails = []
    primary = None
    for org in orgs:
        people = org.get("current_employees", [])
        for p in people:
            email = p.get("email") or p.get("email_status")
            if email:
                emails.append(email)
                if not primary:
                    primary = email

    if not primary:
        return None

    return {
        "primary_email": primary,
        "emails": emails,
        "email_source": "apollo",
    }


# -------------------------
# MASTER WATERFALL
# -------------------------
def enrich_emails_for_domain(domain: Optional[str]) -> Dict[str, Any]:
    result = {
        "primary_email": None,
        "emails": [],
        "email_source": None,
    }

    if not domain:
        return result

    # 1) HUNTER
    hunter = _hunter_domain_search(domain)
    if hunter:
        return hunter

    # 2) SNOV
    snov = _snov_domain_search(domain)
    if snov:
        return snov

    # 3) APOLLO
    apollo = _apollo_domain_lookup(domain)
    if apollo:
        return apollo

    return result
