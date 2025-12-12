# ============================================================
# tp_enrich/email_enrichment.py
# PHASE 1 FIX: Email waterfall with proper Apollo/Snov auth
# Hunter -> Snov -> Apollo -> FullEnrich
# ============================================================

from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

# Import new provider clients
from .providers.apollo_client import apollo_enrich_org_by_domain
from .providers.snov_client import snov_domain_emails

logger = logging.getLogger(__name__)


# ----------------------------
# Small helpers
# ----------------------------

def _norm_email(e: str) -> str:
    return (e or "").strip().lower()

def _uniq_emails(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        x = _norm_email(x)
        if x and "@" in x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _pick_primary_email(generic: List[str], person: List[str], catchall: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Priority: generic -> person -> catchall
    Returns: (email, type)
    """
    if generic:
        return generic[0], "generic"
    if person:
        return person[0], "person"
    if catchall:
        return catchall[0], "catchall"
    return None, None


# ============================================================
# HUNTER
# ============================================================

def _hunter_domain_search(domain: str) -> Dict[str, Any]:
    api_key = os.getenv("HUNTER_API_KEY") or os.getenv("HUNTERIO_API_KEY")
    if not api_key:
        return {"ok": False, "skipped": True, "reason": "HUNTER_API_KEY missing"}

    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": api_key}
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "text": r.text[:300]}
        data = r.json()
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def _parse_hunter_emails(payload: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    """
    Returns: generic_emails, person_emails, catchall_emails
    """
    generic, person, catchall = [], [], []

    try:
        emails = (((payload or {}).get("data") or {}).get("data") or {}).get("emails") or []
        for obj in emails:
            email = _norm_email(obj.get("value") or "")
            if not email:
                continue
            etype = (obj.get("type") or "").lower()
            # keep simple + conservative
            if etype in ("generic", "role"):
                generic.append(email)
            elif etype in ("personal", "person"):
                person.append(email)
            else:
                # unknown types go to person bucket
                person.append(email)
    except Exception:
        pass

    return _uniq_emails(generic), _uniq_emails(person), _uniq_emails(catchall)


# ============================================================
# SNOV (using new provider client with OAuth + Bearer token)
# ============================================================

def _snov_get_emails(domain: str) -> Dict[str, Any]:
    """
    Uses new snov_client with proper OAuth flow.
    Returns: {"ok": bool, "data": [...]}
    """
    try:
        emails = snov_domain_emails(domain, timeout=25)
        if emails:
            return {"ok": True, "data": emails}
        else:
            return {"ok": False, "reason": "No emails returned from Snov"}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def _parse_snov_emails(payload: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    generic, person, catchall = [], [], []
    try:
        # Snov returns list of email objects
        emails = payload.get("data") or []
        for obj in emails:
            if isinstance(obj, str):
                # Direct email string
                generic.append(_norm_email(obj))
            elif isinstance(obj, dict):
                # Email object with metadata
                email = _norm_email(obj.get("email") or obj.get("emailAddress") or obj.get("email_address") or "")
                if not email:
                    continue
                etype = (obj.get("type") or "").lower()
                if etype in ("generic", "role"):
                    generic.append(email)
                elif etype in ("personal", "person"):
                    person.append(email)
                elif etype in ("catchall", "accept_all"):
                    catchall.append(email)
                else:
                    person.append(email)
    except Exception:
        pass

    return _uniq_emails(generic), _uniq_emails(person), _uniq_emails(catchall)


# ============================================================
# APOLLO (using new provider client with X-Api-Key header)
# ============================================================

def _apollo_get_emails(domain: str) -> Dict[str, Any]:
    """
    Uses new apollo_client with proper X-Api-Key header.
    Returns: {"ok": bool, "data": {...}}
    """
    try:
        data = apollo_enrich_org_by_domain(domain, timeout=20)
        if data:
            return {"ok": True, "data": data}
        else:
            return {"ok": False, "reason": "No data returned from Apollo"}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def _parse_apollo_emails(payload: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    generic, person, catchall = [], [], []
    try:
        # Apollo org enrich may return organization object
        data = payload.get("data") or {}
        org = data.get("organization") or data.get("org") or data

        if isinstance(org, dict):
            # Try common email fields
            for k in ("email", "company_email", "contact_email", "general_email"):
                v = _norm_email(org.get(k) or "")
                if v:
                    generic.append(v)

        # Also check if there's an organizations array (from search endpoint)
        orgs = data.get("organizations") or []
        if isinstance(orgs, list) and orgs:
            first_org = orgs[0]
            if isinstance(first_org, dict):
                for k in ("email", "company_email", "contact_email"):
                    v = _norm_email(first_org.get(k) or "")
                    if v:
                        generic.append(v)
    except Exception:
        pass

    return _uniq_emails(generic), _uniq_emails(person), _uniq_emails(catchall)


# ============================================================
# FULLENRICH (optional)
# ============================================================

def _fullenrich_domain(domain: str) -> Dict[str, Any]:
    api_key = os.getenv("FULLENRICH_API_KEY")
    if not api_key:
        return {"ok": False, "skipped": True, "reason": "FULLENRICH_API_KEY missing"}

    url = os.getenv("FULLENRICH_ENDPOINT", "").strip() or "https://api.fullenrich.com/v1/enrich"
    try:
        r = requests.post(url, json={"api_key": api_key, "domain": domain}, timeout=25)
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "text": r.text[:300], "endpoint": url}
        data = r.json()
        return {"ok": True, "data": data, "endpoint": url}
    except Exception as e:
        return {"ok": False, "error": repr(e), "endpoint": url}


def _parse_fullenrich_emails(payload: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    generic, person, catchall = [], [], []
    try:
        emails = payload.get("emails") or payload.get("data") or []
        if isinstance(emails, dict):
            emails = emails.get("emails") or []
        for obj in emails or []:
            if isinstance(obj, str):
                generic.append(_norm_email(obj))
            elif isinstance(obj, dict):
                v = _norm_email(obj.get("email") or obj.get("value") or "")
                if v:
                    et = (obj.get("type") or "").lower()
                    if et in ("personal", "person"):
                        person.append(v)
                    elif et in ("catchall", "accept_all"):
                        catchall.append(v)
                    else:
                        generic.append(v)
    except Exception:
        pass
    return _uniq_emails(generic), _uniq_emails(person), _uniq_emails(catchall)


# ============================================================
# PUBLIC API USED BY PIPELINE
# ============================================================

def enrich_emails_for_domain(domain: str, company_name: Optional[str] = None) -> Dict[str, Any]:
    """
    PHASE 1 FIX: Email waterfall with proper Apollo/Snov auth
    
    Returns a dict with:
      primary_email, primary_email_type, primary_email_source, primary_email_confidence
      generic_emails_json, person_emails_json, catchall_emails_json
      email_waterfall_debug
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return {
            "primary_email": None,
            "primary_email_type": None,
            "primary_email_source": None,
            "primary_email_confidence": None,
            "generic_emails_json": "[]",
            "person_emails_json": "[]",
            "catchall_emails_json": "[]",
        }

    provider_attempts = []

    # -------- Hunter --------
    hunter = _hunter_domain_search(domain)
    provider_attempts.append({"provider": "hunter", **{k: hunter.get(k) for k in ("ok","skipped","status","reason","error")}})
    if hunter.get("ok"):
        g, p, c = _parse_hunter_emails(hunter.get("data") or {})
        primary, etype = _pick_primary_email(g, p, c)
        if primary:
            logger.info(f"Email winner: hunter -> {primary}")
            return {
                "primary_email": primary,
                "primary_email_type": etype,
                "primary_email_source": "hunter",
                "primary_email_confidence": "medium",
                "generic_emails_json": json.dumps(g),
                "person_emails_json": json.dumps(p),
                "catchall_emails_json": json.dumps(c),
                "email_waterfall_debug": json.dumps(provider_attempts),
            }

    # -------- Snov (with new OAuth + Bearer token flow) --------
    snov = _snov_get_emails(domain)
    provider_attempts.append({"provider": "snov", **{k: snov.get(k) for k in ("ok","skipped","status","reason","error")}})
    if snov.get("ok"):
        g, p, c = _parse_snov_emails(snov)
        primary, etype = _pick_primary_email(g, p, c)
        if primary:
            logger.info(f"Email winner: snov -> {primary}")
            return {
                "primary_email": primary,
                "primary_email_type": etype,
                "primary_email_source": "snov",
                "primary_email_confidence": "medium",
                "generic_emails_json": json.dumps(g),
                "person_emails_json": json.dumps(p),
                "catchall_emails_json": json.dumps(c),
                "email_waterfall_debug": json.dumps(provider_attempts),
            }

    # -------- Apollo (with new X-Api-Key header auth) --------
    apollo = _apollo_get_emails(domain)
    provider_attempts.append({"provider": "apollo", **{k: apollo.get(k) for k in ("ok","skipped","status","reason","error")}})
    if apollo.get("ok"):
        g, p, c = _parse_apollo_emails(apollo)
        primary, etype = _pick_primary_email(g, p, c)
        if primary:
            logger.info(f"Email winner: apollo -> {primary}")
            return {
                "primary_email": primary,
                "primary_email_type": etype,
                "primary_email_source": "apollo",
                "primary_email_confidence": "low",
                "generic_emails_json": json.dumps(g),
                "person_emails_json": json.dumps(p),
                "catchall_emails_json": json.dumps(c),
                "email_waterfall_debug": json.dumps(provider_attempts),
            }

    # -------- FullEnrich --------
    fe = _fullenrich_domain(domain)
    provider_attempts.append({"provider": "fullenrich", **{k: fe.get(k) for k in ("ok","skipped","status","reason","error","endpoint")}})
    if fe.get("ok"):
        g, p, c = _parse_fullenrich_emails(fe.get("data") or {})
        primary, etype = _pick_primary_email(g, p, c)
        if primary:
            logger.info(f"Email winner: fullenrich -> {primary}")
            return {
                "primary_email": primary,
                "primary_email_type": etype,
                "primary_email_source": "fullenrich",
                "primary_email_confidence": "medium",
                "generic_emails_json": json.dumps(g),
                "person_emails_json": json.dumps(p),
                "catchall_emails_json": json.dumps(c),
                "email_waterfall_debug": json.dumps(provider_attempts),
            }

    # none found
    logger.info(f"No emails found for domain={domain}. Waterfall={provider_attempts}")
    return {
        "primary_email": None,
        "primary_email_type": None,
        "primary_email_source": None,
        "primary_email_confidence": None,
        "generic_emails_json": "[]",
        "person_emails_json": "[]",
        "catchall_emails_json": "[]",
        "email_waterfall_debug": json.dumps(provider_attempts),
    }
