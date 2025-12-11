"""
Minimal email enrichment - Hunter.io ONLY
"""

import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger(__name__)


def enrich_from_hunter(domain: str, api_key: str) -> Dict[str, Any]:
    """
    Minimal Hunter domain search. Returns a dict with primary_email and related fields.
    """
    base = {
        "primary_email": None,
        "primary_email_type": None,
        "primary_email_source": None,
        "primary_email_confidence": "none",
        "generic_emails": [],
        "person_emails": [],
        "catchall_emails": [],
    }

    if not domain or not domain.strip():
        return base

    if not api_key:
        logger.warning("Hunter API key missing; skipping Hunter enrichment.")
        return base

    url = "https://api.hunter.io/v2/domain-search"
    params = {
        "domain": domain.strip(),
        "api_key": api_key,
    }

    logger.info("Hunter: searching domain '%s'", domain)

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.exception("Hunter domain search error for '%s': %s", domain, e)
        return base

    results = data.get("data", {})
    emails = results.get("emails", []) or []
    if not emails:
        logger.info("Hunter: no emails found for domain '%s'", domain)
        return base

    # pick the first email as primary for now
    primary = emails[0]
    address = primary.get("value")
    email_type = primary.get("type")
    confidence = primary.get("confidence")

    base["primary_email"] = address
    base["primary_email_type"] = email_type
    base["primary_email_source"] = "hunter"
    base["primary_email_confidence"] = "high" if confidence and confidence >= 80 else "medium"

    logger.info(
        "Hunter result for '%s': primary_email=%s, type=%s, confidence=%s",
        domain, address, email_type, confidence
    )

    # Optionally store lists
    generic = []
    person = []
    catchall = []

    for e in emails:
        addr = e.get("value")
        if not addr:
            continue
        et = e.get("type")
        if et == "generic":
            generic.append(addr)
        elif et == "personal":
            person.append(addr)
        else:
            catchall.append(addr)

    base["generic_emails"] = generic
    base["person_emails"] = person
    base["catchall_emails"] = catchall

    return base


def enrich_emails_minimal(domain: Optional[str], hunter_api_key: Optional[str]) -> Dict[str, Any]:
    """
    Minimal email enrichment wrapper - Hunter ONLY, domain required.
    """
    empty = {
        "primary_email": None,
        "primary_email_type": None,
        "primary_email_source": None,
        "primary_email_confidence": "none",
        "generic_emails": [],
        "person_emails": [],
        "catchall_emails": [],
    }

    if not domain or not str(domain).strip():
        logger.warning("Skipping email enrichment: domain missing or empty.")
        return empty

    if not hunter_api_key:
        logger.warning("Hunter API key missing; cannot perform email enrichment.")
        return empty

    return enrich_from_hunter(domain, hunter_api_key)
