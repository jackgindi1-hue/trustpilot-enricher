# ============================================================
# tp_enrich/email_enrichment.py
# Robust email waterfall: Hunter -> Snov -> Apollo -> FullEnrich
# - Never guesses emails
# - Never returns early just because Hunter is missing
# - Logs provider failures clearly
# ============================================================

from __future__ import annotations

import os
import json
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

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
            confidence = obj.get("confidence")
            # keep simple + conservative
            if etype in ("generic", "role"):
                generic.append(email)
            elif etype in ("personal", "person"):
                person.append(email)
            else:
                # unknown types go to person bucket
                person.append(email)

        # hunter "pattern"/"accept_all" could exist, but we do NOT generate emails from it.
        # If you ever add catchall, only do it if provider explicitly returns it.

    except Exception:
        pass

    return _uniq_emails(generic), _uniq_emails(person), _uniq_emails(catchall)


# ============================================================
# SNOV (tries oauth + v1 + v2 styles; whichever works)
# ============================================================

def _snov_get_token() -> Dict[str, Any]:
    cid = os.getenv("SNOV_CLIENT_ID")
    csec = os.getenv("SNOV_CLIENT_SECRET")
    if not cid or not csec:
        return {"ok": False, "skipped": True, "reason": "SNOV_CLIENT_ID / SNOV_CLIENT_SECRET missing"}

    # Try v1 token endpoint first, then v2
    token_endpoints = [
        "https://api.snov.io/v1/oauth/access_token",
        "https://api.snov.io/v2/oauth/access_token",
    ]

    for url in token_endpoints:
        try:
            r = requests.post(
                url,
                data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csec},
                timeout=20,
            )
            if r.status_code != 200:
                logger.warning(f"Snov token endpoint failed {url} status={r.status_code} body={r.text[:200]}")
                continue
            data = r.json()
            token = data.get("access_token")
            if token:
                return {"ok": True, "token": token, "endpoint": url}
        except Exception as e:
            logger.warning(f"Snov token exception {url}: {e!r}")
            continue

    return {"ok": False, "reason": "Could not obtain Snov access_token from v1/v2 token endpoints"}


def _snov_domain_emails(domain: str) -> Dict[str, Any]:
    tok = _snov_get_token()
    if not tok.get("ok"):
        return tok

    access_token = tok["token"]

    # Try both styles. Some accounts/docs differ; we probe safely.
    candidates = [
        # v1 style seen in your logs
        ("POST", "https://api.snov.io/v1/get-domain-emails-with-info", {"access_token": access_token, "domain": domain}),
        # v2 style (probe)
        ("POST", "https://api.snov.io/v2/get-domain-emails-with-info", {"access_token": access_token, "domain": domain}),
    ]

    last_err = None
    for method, url, payload in candidates:
        try:
            r = requests.post(url, data=payload, timeout=25)
            if r.status_code != 200:
                last_err = {"ok": False, "status": r.status_code, "text": r.text[:300], "endpoint": url}
                logger.warning(f"Snov domain emails failed endpoint={url} status={r.status_code} body={r.text[:200]}")
                continue
            data = r.json()
            return {"ok": True, "data": data, "endpoint": url}
        except Exception as e:
            last_err = {"ok": False, "error": repr(e), "endpoint": url}
            logger.warning(f"Snov domain emails exception endpoint={url}: {e!r}")
            continue

    return last_err or {"ok": False, "reason": "Snov failed for unknown reason"}


def _parse_snov_emails(payload: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    generic, person, catchall = [], [], []
    try:
        # Snov often returns "emails" list with objects
        emails = (payload or {}).get("emails") or (payload or {}).get("data") or []
        if isinstance(emails, dict):
            emails = emails.get("emails") or []
        for obj in emails or []:
            email = _norm_email(obj.get("email") or obj.get("value") or "")
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
# APOLLO (try org enrichment first; then fallback searches)
# ============================================================

def _apollo_org_enrich(domain: str, company_name: Optional[str] = None) -> Dict[str, Any]:
    api_key = os.getenv("APOLLO_API_KEY")
    if not api_key:
        return {"ok": False, "skipped": True, "reason": "APOLLO_API_KEY missing"}

    # Try the common enrichment endpoint shape first.
    # If Apollo changes, we still log and continue to other providers.
    endpoints = [
        ("POST", "https://api.apollo.io/v1/organizations/enrich", {"api_key": api_key, "domain": domain}),
    ]

    # Fallback: search endpoint (payloads vary by plan/account; we try minimal)
    endpoints += [
        ("POST", "https://api.apollo.io/v1/organizations/search", {"api_key": api_key, "q_organization_domains": domain}),
        ("POST", "https://api.apollo.io/v1/organizations/search", {"api_key": api_key, "q_organization_name": (company_name or "").strip()}),
    ]

    last_err = None
    for method, url, body in endpoints:
        try:
            r = requests.post(url, json=body, timeout=25)
            if r.status_code != 200:
                last_err = {"ok": False, "status": r.status_code, "text": r.text[:300], "endpoint": url, "body": body}
                logger.warning(f"Apollo failed endpoint={url} status={r.status_code} body={r.text[:200]}")
                continue
            data = r.json()
            return {"ok": True, "data": data, "endpoint": url}
        except Exception as e:
            last_err = {"ok": False, "error": repr(e), "endpoint": url, "body": body}
            logger.warning(f"Apollo exception endpoint={url}: {e!r}")
            continue

    return last_err or {"ok": False, "reason": "Apollo failed for unknown reason"}


def _parse_apollo_emails(payload: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    generic, person, catchall = [], [], []
    try:
        # Apollo org enrich may return organization object; emails may not be present on org.
        # If you later add people enrichment, this will start returning person emails.
        org = payload.get("organization") or payload.get("data") or payload
        # Best-effort: some responses include "email" fields; we only take explicit ones.
        if isinstance(org, dict):
            for k in ("email", "company_email", "contact_email"):
                v = _norm_email(org.get(k) or "")
                if v:
                    generic.append(v)
    except Exception:
        pass
    return _uniq_emails(generic), _uniq_emails(person), _uniq_emails(catchall)


# ============================================================
# FULLENRICH (optional; only if you already have it wired)
# ============================================================

def _fullenrich_domain(domain: str) -> Dict[str, Any]:
    api_key = os.getenv("FULLENRICH_API_KEY")
    if not api_key:
        return {"ok": False, "skipped": True, "reason": "FULLENRICH_API_KEY missing"}

    # NOTE: endpoint may differ by your FullEnrich plan.
    # This is a safe probe; failures are logged and we move on.
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
        # We only accept explicit emails returned by API.
        emails = payload.get("emails") or payload.get("data") or []
        if isinstance(emails, dict):
            emails = emails.get("emails") or []
        for obj in emails or []:
            if isinstance(obj, str):
                generic.append(_norm_email(obj))
            elif isinstance(obj, dict):
                v = _norm_email(obj.get("email") or obj.get("value") or "")
                if v:
                    # if provider labels type, respect it; else generic
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
    Returns a dict with:
      primary_email, primary_email_type, primary_email_source, primary_email_confidence
      generic_emails_json, person_emails_json, catchall_emails_json
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

    # -------- Snov --------
    snov = _snov_domain_emails(domain)
    provider_attempts.append({"provider": "snov", **{k: snov.get(k) for k in ("ok","skipped","status","reason","error","endpoint")}})
    if snov.get("ok"):
        g, p, c = _parse_snov_emails(snov.get("data") or {})
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

    # -------- Apollo --------
    apollo = _apollo_org_enrich(domain, company_name=company_name)
    provider_attempts.append({"provider": "apollo", **{k: apollo.get(k) for k in ("ok","skipped","status","reason","error","endpoint")}})
    if apollo.get("ok"):
        g, p, c = _parse_apollo_emails(apollo.get("data") or {})
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
