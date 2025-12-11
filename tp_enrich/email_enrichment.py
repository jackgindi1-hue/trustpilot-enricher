import os
import logging
from typing import Optional, Dict, Any, List

import requests
from requests.exceptions import HTTPError

logger = logging.getLogger(__name__)

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
SNOV_CLIENT_ID = os.getenv("SNOV_CLIENT_ID")
SNOV_CLIENT_SECRET = os.getenv("SNOV_CLIENT_SECRET")


def _pick_best_email_from_hunter(emails: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Given Hunter `emails` list, pick the best one.
    Preference:
      1) highest confidence_score
      2) has 'type' == 'generic' or 'personal' (doesn't really matter, we just pick something sane)
    """
    if not emails:
        return None

    def score(e: Dict[str, Any]) -> float:
        # Hunter usually has confidence_score 0–100.
        return float(e.get("confidence_score") or 0.0)

    best = max(emails, key=score)
    if not best.get("value"):
        return None
    return best


def enrich_from_hunter(domain: str, business_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Try to get an email from Hunter for a given domain.
    Returns a dict with keys like:
      - primary_email
      - email_source
      - email_confidence
      - emails_raw
    or None if nothing useful.
    """
    if not HUNTER_API_KEY:
        logger.warning("Hunter: HUNTER_API_KEY not set; skipping Hunter enrichment for domain '%s'", domain)
        return None

    params = {
        "domain": domain,
        "api_key": HUNTER_API_KEY,
    }

    try:
        logger.info("Hunter: starting domain search for '%s' (business=%s)", domain, business_name)
        resp = requests.get("https://api.hunter.io/v2/domain-search", params=params, timeout=15)
        try:
            resp.raise_for_status()
        except HTTPError as e:
            status = resp.status_code
            if status == 429:
                logger.warning("Hunter: rate limited (429) for domain '%s'", domain)
            else:
                logger.error("Hunter: HTTP %s for domain '%s': %s", status, domain, e)
            return None

        data = resp.json() or {}
        domain_data = data.get("data") or {}
        emails = domain_data.get("emails") or []

        if not emails:
            logger.info("Hunter: no emails found for domain '%s'", domain)
            return None

        best = _pick_best_email_from_hunter(emails)
        if not best:
            logger.info("Hunter: could not pick a best email for domain '%s'", domain)
            return None

        email_addr = best.get("value")
        confidence = best.get("confidence_score")

        logger.info(
            "Hunter: selected email '%s' (confidence=%s) for domain '%s'",
            email_addr,
            confidence,
            domain,
        )

        return {
            "primary_email": email_addr,
            "email_source": "hunter",
            "email_confidence": confidence,
            "emails_raw": emails,
        }

    except Exception as e:
        logger.error("Hunter domain search error for '%s': %s", domain, e)
        return None


def _get_snov_access_token() -> Optional[str]:
    """
    Get a Snov access token via client_credentials.
    Uses:
      - SNOV_CLIENT_ID
      - SNOV_CLIENT_SECRET
    If not configured or fails, return None.
    """
    if not SNOV_CLIENT_ID or not SNOV_CLIENT_SECRET:
        logger.warning("Snov: SNOV_CLIENT_ID / SNOV_CLIENT_SECRET not set; skipping Snov enrichment.")
        return None

    try:
        resp = requests.post(
            "https://api.snov.io/v1/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": SNOV_CLIENT_ID,
                "client_secret": SNOV_CLIENT_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        token = data.get("access_token")
        if not token:
            logger.error("Snov: no access_token in response: %s", data)
            return None
        return token
    except Exception as e:
        logger.error("Snov: error getting access token: %s", e)
        return None


def _pick_best_email_from_snov(emails: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Snov `emails` objects usually have fields like 'email', 'confidence', 'firstName', 'lastName', etc.
    We'll pick the highest confidence.
    """
    if not emails:
        return None

    def score(e: Dict[str, Any]) -> float:
        return float(e.get("confidence") or 0.0)

    best = max(emails, key=score)
    if not best.get("email"):
        return None
    return best


def enrich_from_snov(domain: str, business_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Use Snov's /v2/domain-emails-with-info endpoint to get emails for a domain.
    Returns same shape as Hunter's dict, or None.
    """
    token = _get_snov_access_token()
    if not token:
        return None

    try:
        params = {
            "access_token": token,
            "domain": domain,
            "type": "all",   # all types of emails
            "limit": 10,
        }
        logger.info("Snov: starting domain search for '%s' (business=%s)", domain, business_name)
        resp = requests.get(
            "https://api.snov.io/v2/domain-emails-with-info",
            params=params,
            timeout=20,
        )
        try:
            resp.raise_for_status()
        except HTTPError as e:
            status = resp.status_code
            if status == 429:
                logger.warning("Snov: rate limited (429) for domain '%s'", domain)
            else:
                logger.error("Snov: HTTP %s for domain '%s': %s", status, domain, e)
            return None

        data = resp.json() or {}
        emails = data.get("emails") or []
        if not emails:
            logger.info("Snov: no emails found for domain '%s'", domain)
            return None

        best = _pick_best_email_from_snov(emails)
        if not best:
            logger.info("Snov: could not pick best email for domain '%s'", domain)
            return None

        email_addr = best.get("email")
        confidence = best.get("confidence")

        logger.info(
            "Snov: selected email '%s' (confidence=%s) for domain '%s'",
            email_addr,
            confidence,
            domain,
        )

        return {
            "primary_email": email_addr,
            "email_source": "snov",
            "email_confidence": confidence,
            "emails_raw": emails,
        }

    except Exception as e:
        logger.error("Snov domain search error for '%s': %s", domain, e)
        return None


def enrich_email_for_domain(domain: Optional[str], business_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Main orchestrator used by the pipeline.

    Steps:
      1) If no domain -> skip & return empty.
      2) Try Hunter (if key present & not rate-limited).
      3) If Hunter fails or returns None -> try Snov.
      4) If both fail -> return empty, but NEVER raise.
    """
    result: Dict[str, Any] = {
        "primary_email": None,
        "email_source": None,
        "email_confidence": None,
        "emails_raw": None,
    }

    if not domain:
        logger.error("Skipping email enrichment: domain missing or empty.")
        return result

    # 1) Hunter
    hunter_res = enrich_from_hunter(domain, business_name=business_name)
    if hunter_res and hunter_res.get("primary_email"):
        result.update(hunter_res)
        return result

    # 2) Snov fallback
    snov_res = enrich_from_snov(domain, business_name=business_name)
    if snov_res and snov_res.get("primary_email"):
        result.update(snov_res)
        return result

    # No provider could find an email
    logger.info("Email enrichment: no email found for domain '%s' (Hunter + Snov).", domain)
    return result
