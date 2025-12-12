# ==========================
# tp_enrich/email_enrichment.py
# HUNTER-ONLY EMAIL ENRICHMENT (MINIMAL MVP)
# ==========================
import os
import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

HUNTER_API_KEY = (os.getenv("HUNTER_API_KEY") or "").strip()


# -------------------------
# HUNTER DOMAIN SEARCH
# -------------------------
def _hunter_domain_search(domain: str) -> Optional[Dict[str, Any]]:
    """
    Hunter.io domain search for emails.

    Args:
        domain: Domain to search

    Returns:
        Dict with primary_email, emails list, and email_source, or None
    """
    if not HUNTER_API_KEY:
        logger.warning("Hunter API key not set")
        return None

    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 10},
            timeout=20,
        )
        resp.raise_for_status()
        logger.info(f"Hunter API status {resp.status_code} for domain '{domain}'")
    except Exception as e:
        logger.error("Hunter error for %s: %s", domain, repr(e))
        return None

    data = (resp.json() or {}).get("data") or {}
    emails = data.get("emails") or []

    if not emails:
        logger.info(f"Hunter: no emails found for domain '{domain}'")
        return None

    # Find best email (highest confidence >= 80)
    best = None
    for e in emails:
        conf = int(e.get("confidence") or 0)
        if conf >= 80:
            best = e
            break

    best = best or emails[0]
    primary = best.get("value")

    if not primary:
        logger.warning(f"Hunter: returned emails but no valid primary for '{domain}'")
        return None

    logger.info(f"Hunter: found primary email '{primary}' for domain '{domain}'")

    return {
        "primary_email": primary,
        "emails": [x.get("value") for x in emails if x.get("value")],
        "email_source": "hunter",
    }


# -------------------------
# MASTER FUNCTION (HUNTER ONLY)
# -------------------------
def enrich_emails_for_domain(domain: Optional[str]) -> Dict[str, Any]:
    """
    Email enrichment using Hunter.io ONLY.

    This is the minimal MVP path:
    - No Snov (was causing 404 errors)
    - No Apollo (was causing 422 errors)
    - Just Hunter domain search

    Args:
        domain: Domain to search for emails

    Returns:
        Dict with primary_email, emails, email_source
    """
    result = {
        "primary_email": None,
        "emails": [],
        "email_source": None,
    }

    if not domain:
        logger.debug("No domain provided for email enrichment")
        return result

    logger.info(f"Email enrichment for domain: {domain}")

    # HUNTER ONLY
    hunter = _hunter_domain_search(domain)
    if hunter:
        logger.info(f"✓ Email enrichment successful via Hunter for '{domain}'")
        return hunter

    logger.warning(f"✗ No emails found for domain '{domain}'")
    return result
