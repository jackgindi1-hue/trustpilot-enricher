# tp_enrich/email_enrichment.py
import os
import re
import json
import requests
from typing import Any, Dict, Optional, List

_GENERIC_PREFIXES = ("info@", "support@", "sales@", "hello@", "contact@", "admin@", "billing@", "team@")

def _clean_email(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", s, re.I):
        return s.lower()
    return None

def _split_emails(emails: List[str]) -> Dict[str, List[str]]:
    out = []
    for e in emails:
        e2 = _clean_email(e)
        if e2 and e2 not in out:
            out.append(e2)
    generic = [e for e in out if e.startswith(_GENERIC_PREFIXES)]
    person = [e for e in out if e not in generic]
    return {"generic": generic, "person": person, "catchall": []}

def _pick_primary(source: str, confidence: Optional[str], emails: Dict[str, List[str]]) -> Dict[str, Any]:
    generic = emails.get("generic") or []
    person = emails.get("person") or []
    catchall = emails.get("catchall") or []
    ordered = generic + person + catchall
    primary = ordered[0] if ordered else None
    if not primary:
        return {
            "primary_email": None,
            "primary_email_type": None,
            "primary_email_source": None,
            "primary_email_confidence": None,
            "generic_emails_json": "[]",
            "person_emails_json": "[]",
            "catchall_emails_json": "[]",
        }
    if primary in generic:
        t = "generic"
    elif primary in person:
        t = "person"
    else:
        t = "catchall"
    return {
        "primary_email": primary,
        "primary_email_type": t,
        "primary_email_source": source,
        "primary_email_confidence": confidence,
        "generic_emails_json": json.dumps(generic),
        "person_emails_json": json.dumps(person),
        "catchall_emails_json": json.dumps(catchall),
    }

def _hunter(domain: str) -> Dict[str, Any]:
    key = os.getenv("HUNTER_API_KEY") or os.getenv("HUNTER_KEY")
    if not key:
        return {"attempted": False, "ok": False, "reason": "missing HUNTER_API_KEY", "emails": []}
    try:
        r = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": key},
            timeout=20,
        )
        if r.status_code != 200:
            return {"attempted": True, "ok": False, "reason": f"HTTP {r.status_code}: {r.text[:200]}", "emails": []}
        js = r.json() or {}
        items = (js.get("data", {}) or {}).get("emails") or []
        emails = [i.get("value") for i in items if i.get("value")]
        return {"attempted": True, "ok": True, "reason": None, "emails": emails}
    except Exception as e:
        return {"attempted": True, "ok": False, "reason": f"exception: {repr(e)}", "emails": []}

def _apollo(domain: str) -> Dict[str, Any]:
    key = os.getenv("APOLLO_API_KEY")
    if not key:
        return {"attempted": False, "ok": False, "reason": "missing APOLLO_API_KEY", "emails": []}

    # Apollo docs say "include API key in header"; your logs said X-Api-Key.
    # So we try BOTH common patterns to avoid silent failure:
    headers_variants = [
        {"X-Api-Key": key, "Content-Type": "application/json"},
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    ]

    # NOTE: Org enrichment often does NOT return emails.
    # But if Apollo returns any org-level email fields, we'll take them.
    url = "https://api.apollo.io/v1/organizations/enrich"

    last_reason = None
    for headers in headers_variants:
        try:
            r = requests.post(url, headers=headers, json={"domain": domain}, timeout=25)
            if r.status_code != 200:
                last_reason = f"HTTP {r.status_code}: {r.text[:200]}"
                continue

            js = r.json() or {}
            org = js.get("organization") or {}

            candidates = []
            for k in ("email", "public_email", "support_email", "contact_email"):
                if org.get(k):
                    candidates.append(org.get(k))

            # Some Apollo responses include "emails" arrays in some accounts
            if isinstance(org.get("emails"), list):
                candidates.extend(org.get("emails"))

            emails = [c for c in candidates if c]
            return {"attempted": True, "ok": True, "reason": None, "emails": emails}

        except Exception as e:
            last_reason = f"exception: {repr(e)}"
            continue

    return {"attempted": True, "ok": False, "reason": last_reason or "unknown", "emails": []}

def _snov(domain: str) -> Dict[str, Any]:
    # Snov needs OAuth token. We support either:
    # - SNOV_ACCESS_TOKEN already set
    # - OR SNOV_CLIENT_ID + SNOV_CLIENT_SECRET to fetch one
    access_token = os.getenv("SNOV_ACCESS_TOKEN")
    client_id = os.getenv("SNOV_CLIENT_ID")
    client_secret = os.getenv("SNOV_CLIENT_SECRET")

    if not access_token and not (client_id and client_secret):
        return {"attempted": False, "ok": False, "reason": "missing SNOV auth env", "emails": []}

    try:
        if not access_token:
            tok = requests.post(
                "https://api.snov.io/v1/oauth/access_token",
                data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
                timeout=20,
            )
            if tok.status_code != 200:
                return {"attempted": True, "ok": False, "reason": f"token HTTP {tok.status_code}: {tok.text[:200]}", "emails": []}
            access_token = (tok.json() or {}).get("access_token")
            if not access_token:
                return {"attempted": True, "ok": False, "reason": "token missing access_token", "emails": []}

        # IMPORTANT:
        # Your logs showed you were calling:
        #   /v1/get-domain-emails-with-info (404)
        #   /v2/get-domain-emails-with-info (404)
        # This uses the common v2 path WITHOUT "get-".
        r = requests.post(
            "https://api.snov.io/v2/domain-emails-with-info",
            data={"access_token": access_token, "domain": domain},
            timeout=25,
        )
        if r.status_code != 200:
            return {"attempted": True, "ok": False, "reason": f"HTTP {r.status_code}: {r.text[:200]}", "emails": []}

        js = r.json() or {}
        # Snov commonly returns: {"emails":[{"email":"..."}, ...]}
        items = js.get("emails") or []
        emails = []
        for it in items:
            if isinstance(it, dict) and it.get("email"):
                emails.append(it.get("email"))
            elif isinstance(it, str):
                emails.append(it)
        return {"attempted": True, "ok": True, "reason": None, "emails": emails}

    except Exception as e:
        return {"attempted": True, "ok": False, "reason": f"exception: {repr(e)}", "emails": []}

def _fullenrich(domain: str) -> Dict[str, Any]:
    key = os.getenv("FULLENRICH_API_KEY")
    if not key:
        return {"attempted": False, "ok": False, "reason": "missing FULLENRICH_API_KEY", "emails": []}
    try:
        # Keep minimal + safe: if your FullEnrich endpoint differs, it will show as provider_status_json reason.
        r = requests.post(
            "https://api.fullenrich.com/v1/domain",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"domain": domain},
            timeout=25,
        )
        if r.status_code != 200:
            return {"attempted": True, "ok": False, "reason": f"HTTP {r.status_code}: {r.text[:200]}", "emails": []}
        js = r.json() or {}
        emails = js.get("emails") or []
        return {"attempted": True, "ok": True, "reason": None, "emails": emails if isinstance(emails, list) else []}
    except Exception as e:
        return {"attempted": True, "ok": False, "reason": f"exception: {repr(e)}", "emails": []}

def run_email_waterfall(
    domain: Optional[str],
    company_name: Optional[str] = None,
    logger: Any = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Returns:
      primary_email, primary_email_source, primary_email_confidence,
      generic/person/catchall json columns,
      provider_status_json (self-proving: shows each provider attempted + reason)
    """
    if not domain:
        out = _pick_primary(source=None, confidence=None, emails={"generic": [], "person": [], "catchall": []})
        out["provider_status_json"] = "{}"
        return out

    status: Dict[str, Any] = {}

    def _log(msg: str):
        if logger:
            logger.info(msg)

    _log(f"EMAIL WATERFALL START: domain={domain} company={company_name}")

    # Order: Hunter → Apollo → Snov → FullEnrich (easy to change later)
    # (Apollo before Snov is fine because Apollo credit usage can be higher; we'll decide later.)
    for name, fn in [
        ("hunter", _hunter),
        ("apollo", _apollo),
        ("snov", _snov),
        ("fullenrich", _fullenrich),
    ]:
        res = fn(domain)
        status[name] = {
            "attempted": res.get("attempted"),
            "ok": res.get("ok"),
            "reason": res.get("reason"),
            "email_count": len(res.get("emails") or []),
        }

        emails_raw = res.get("emails") or []
        emails_split = _split_emails(emails_raw)

        if (emails_split["generic"] or emails_split["person"] or emails_split["catchall"]):
            conf = "high" if emails_split["generic"] else "medium"
            out = _pick_primary(source=name, confidence=conf, emails=emails_split)
            out["provider_status_json"] = json.dumps(status)
            _log(f"EMAIL WATERFALL WINNER: {name} primary={out.get('primary_email')}")
            return out

        _log(f"EMAIL WATERFALL: {name} found 0 emails ({status[name].get('reason')})")

    out = _pick_primary(source=None, confidence=None, emails={"generic": [], "person": [], "catchall": []})
    out["provider_status_json"] = json.dumps(status)
    _log("EMAIL WATERFALL END: no email found from any provider")
    return out

# Backward-compat aliases (prevents "cannot import name …" crashes)
def enrich_emails_for_domain(domain: Optional[str], company_name: Optional[str] = None, logger: Any = None, **kwargs) -> Dict[str, Any]:
    return run_email_waterfall(domain=domain, company_name=company_name, logger=logger, **kwargs)

def enrich_domain_emails(domain: Optional[str], company_name: Optional[str] = None, logger: Any = None, **kwargs) -> Dict[str, Any]:
    return run_email_waterfall(domain=domain, company_name=company_name, logger=logger, **kwargs)
