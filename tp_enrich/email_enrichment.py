import os
import re
import json
import requests
from typing import Any, Dict, Optional, List, Tuple

GENERIC_PREFIXES = ("info@", "support@", "sales@", "hello@", "contact@", "admin@", "office@", "team@")

# Disallow social/profile domains for email enrichment (these will never yield company emails)
BLOCKED_EMAIL_DOMAINS = {
    "facebook.com", "m.facebook.com", "instagram.com", "linkedin.com",
    "tiktok.com", "yelp.com", "goo.gl", "maps.app.goo.gl"
}

def _clean_email(s: Any) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", s, re.I):
        return s.lower()
    return None

def _split_generic_person(emails: List[str]) -> Tuple[List[str], List[str]]:
    generic = [e for e in emails if e.startswith(GENERIC_PREFIXES)]
    person = [e for e in emails if e not in generic]
    return generic, person

def _pick_primary(source: Optional[str], confidence: Optional[str], generic: List[str], person: List[str], catchall: List[str]) -> Dict[str, Any]:
    # prefer generic -> person -> catchall
    for e in (generic + person + catchall):
        e2 = _clean_email(e)
        if e2:
            email_type = "generic" if e in generic else ("person" if e in person else "catchall")
            return {
                "primary_email": e2,
                "primary_email_type": email_type,
                "primary_email_source": source,
                "primary_email_confidence": confidence,
                "generic_emails_json": json.dumps(generic),
                "person_emails_json": json.dumps(person),
                "catchall_emails_json": json.dumps(catchall),
            }
    return {
        "primary_email": None,
        "primary_email_type": None,
        "primary_email_source": None,
        "primary_email_confidence": None,
        "generic_emails_json": "[]",
        "person_emails_json": "[]",
        "catchall_emails_json": "[]",
    }

def _hunter(domain: str) -> Tuple[Optional[Dict[str, Any]], str]:
    key = os.getenv("HUNTER_API_KEY") or os.getenv("HUNTER_KEY")
    if not key:
        return None, "missing HUNTER_API_KEY"
    url = "https://api.hunter.io/v2/domain-search"
    r = requests.get(url, params={"domain": domain, "api_key": key}, timeout=25)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    data = r.json() or {}
    emails = []
    for item in (data.get("data", {}).get("emails") or []):
        e = _clean_email(item.get("value"))
        if e:
            emails.append(e)
    generic, person = _split_generic_person(emails)
    return {
        "source": "hunter",
        "confidence": "high" if generic else ("medium" if person else None),
        "generic": generic,
        "person": person,
        "catchall": [],
    }, "ok"

def _apollo(domain: str) -> Tuple[Optional[Dict[str, Any]], str]:
    api_key = os.getenv("APOLLO_API_KEY")
    if not api_key:
        return None, "missing APOLLO_API_KEY"

    # Apollo requires X-Api-Key header (your logs showed INVALID_API_KEY_LOCATION when not used)
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    # NOTE: org enrich often returns no emails. We still run it, but treat it as "best effort".
    r = requests.post("https://api.apollo.io/v1/organizations/enrich", headers=headers, json={"domain": domain}, timeout=25)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"

    js = r.json() or {}
    org = js.get("organization") or {}

    # Rarely present, but capture if it exists
    emails = []
    e = _clean_email(org.get("email"))
    if e:
        emails.append(e)

    generic, person = _split_generic_person(emails)
    return {
        "source": "apollo",
        "confidence": "medium" if emails else None,
        "generic": generic,
        "person": person,
        "catchall": [],
    }, "ok"

def _snov(domain: str) -> Tuple[Optional[Dict[str, Any]], str]:
    # Minimal SNOV auth flow. If your Railway env uses a stored token, set SNOV_ACCESS_TOKEN.
    access_token = os.getenv("SNOV_ACCESS_TOKEN")
    client_id = os.getenv("SNOV_CLIENT_ID")
    client_secret = os.getenv("SNOV_CLIENT_SECRET")

    if not access_token and not (client_id and client_secret):
        return None, "missing SNOV_ACCESS_TOKEN or SNOV_CLIENT_ID/SNOV_CLIENT_SECRET"

    if not access_token:
        tok = requests.post(
            "https://api.snov.io/v1/oauth/access_token",
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=25,
        )
        if tok.status_code != 200:
            return None, f"token HTTP {tok.status_code}: {tok.text[:200]}"
        access_token = (tok.json() or {}).get("access_token")
        if not access_token:
            return None, "token response had no access_token"

    # SNOV domain emails w/ info (v2) expects POST with access_token + domain
    # (Your earlier code was hitting endpoints that 404'd)
    r = requests.post(
        "https://api.snov.io/v2/domain-emails-with-info",
        data={"access_token": access_token, "domain": domain},
        timeout=30,
    )
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"

    js = r.json() or {}
    emails = []
    for item in (js.get("emails") or []):
        e = _clean_email(item.get("email"))
        if e:
            emails.append(e)

    generic, person = _split_generic_person(emails)
    return {
        "source": "snov",
        "confidence": "medium" if emails else None,
        "generic": generic,
        "person": person,
        "catchall": [],
    }, "ok"

def _fullenrich(domain: str) -> Tuple[Optional[Dict[str, Any]], str]:
    key = os.getenv("FULLENRICH_API_KEY")
    if not key:
        return None, "missing FULLENRICH_API_KEY"

    # If your FullEnrich integration uses a different endpoint already, update this block to match it.
    r = requests.post(
        "https://api.fullenrich.com/v1/domain",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"domain": domain},
        timeout=30,
    )
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"

    js = r.json() or {}
    emails = []
    for e in (js.get("emails") or []):
        e2 = _clean_email(e)
        if e2:
            emails.append(e2)

    generic, person = _split_generic_person(emails)
    return {
        "source": "fullenrich",
        "confidence": "medium" if emails else None,
        "generic": generic,
        "person": person,
        "catchall": [],
    }, "ok"

def run_email_waterfall(domain: Optional[str], company_name: Optional[str] = None, logger: Any = None, **kwargs) -> Dict[str, Any]:
    """
    Guaranteed stable API for pipeline:
      - returns primary_email fields + *_emails_json
      - returns provider_status_json so you can PROVE which providers ran and why they failed
    """
    if not domain:
        return {**_pick_primary(None, None, [], [], []), "provider_status_json": "{}"}

    d = str(domain).strip().lower()
    if any(d == bad or d.endswith("." + bad) for bad in BLOCKED_EMAIL_DOMAINS):
        # Don't waste credits on facebook/linkedin etc.
        out = _pick_primary(None, None, [], [], [])
        out["provider_status_json"] = json.dumps({"blocked_domain": d})
        return out

    status = {}

    def _log(msg: str):
        if logger:
            logger.info(msg)

    _log(f"EMAIL WATERFALL: domain={d} company={company_name}")

    # Explicit sequential waterfall: Hunter → Snov → Apollo → FullEnrich
    # Each provider runs independently with no early exits except on email success.

    # 1. Try Hunter
    try:
        payload, note = _hunter(d)
        status["hunter"] = {"attempted": True, "note": note}
        if payload and (payload["generic"] or payload["person"] or payload["catchall"]):
            out = _pick_primary(payload["source"], payload["confidence"], payload["generic"], payload["person"], payload["catchall"])
            out["provider_status_json"] = json.dumps(status)
            return out
    except Exception as e:
        status["hunter"] = {"attempted": True, "note": f"exception: {repr(e)}"}

    # 2. Try Snov
    try:
        payload, note = _snov(d)
        status["snov"] = {"attempted": True, "note": note}
        if payload and (payload["generic"] or payload["person"] or payload["catchall"]):
            out = _pick_primary(payload["source"], payload["confidence"], payload["generic"], payload["person"], payload["catchall"])
            out["provider_status_json"] = json.dumps(status)
            return out
    except Exception as e:
        status["snov"] = {"attempted": True, "note": f"exception: {repr(e)}"}

    # 3. Try Apollo
    try:
        payload, note = _apollo(d)
        status["apollo"] = {"attempted": True, "note": note}
        if payload and (payload["generic"] or payload["person"] or payload["catchall"]):
            out = _pick_primary(payload["source"], payload["confidence"], payload["generic"], payload["person"], payload["catchall"])
            out["provider_status_json"] = json.dumps(status)
            return out
    except Exception as e:
        status["apollo"] = {"attempted": True, "note": f"exception: {repr(e)}"}

    # 4. Try FullEnrich
    try:
        payload, note = _fullenrich(d)
        status["fullenrich"] = {"attempted": True, "note": note}
        if payload and (payload["generic"] or payload["person"] or payload["catchall"]):
            out = _pick_primary(payload["source"], payload["confidence"], payload["generic"], payload["person"], payload["catchall"])
            out["provider_status_json"] = json.dumps(status)
            return out
    except Exception as e:
        status["fullenrich"] = {"attempted": True, "note": f"exception: {repr(e)}"}

    out = _pick_primary(None, None, [], [], [])
    out["provider_status_json"] = json.dumps(status)
    return out


# ============================================================
# BACKWARD COMPATIBILITY: Maintain enrich_emails_for_domain
# ============================================================
def enrich_emails_for_domain(domain: str, company_name: Optional[str] = None, logger=None) -> Dict[str, Any]:
    """
    Legacy function name - calls run_email_waterfall for backward compatibility
    """
    return run_email_waterfall(domain=domain, company_name=company_name, logger=logger)
