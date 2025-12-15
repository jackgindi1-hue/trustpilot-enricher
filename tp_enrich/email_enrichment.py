# tp_enrich/email_enrichment.py
import os
import time
import json
import requests
from typing import Dict, Any, Optional, List, Tuple

# NOTE:
# - Apollo requires X-Api-Key header (your logs show INVALID_API_KEY when not using header).
# - Snov Domain Search API is v2 task-based (start -> result/{task_hash}); old get-domain-emails-with-info returns 404 now.
#   https://api.snov.io/v2/domain-search/domain-emails/start?domain=...
#   https://api.snov.io/v2/domain-search/domain-emails/result/{task_hash}
#
# This module exposes: enrich_emails_for_domain(domain, company_name=None, logger=None)

def _is_bad_domain(domain: str) -> bool:
    if not domain:
        return True
    d = domain.strip().lower()
    # Skip social / link hubs / non-company domains
    bad = (
        d.endswith("facebook.com")
        or d.endswith("instagram.com")
        or d.endswith("linkedin.com")
        or d.endswith("twitter.com")
        or d.endswith("x.com")
        or d.endswith("yelp.com")
        or d.endswith("tiktok.com")
        or d.endswith("google.com")
        or d.endswith("goo.gl")
        or d.endswith("linktr.ee")
    )
    return bad

def _pick_primary_email(emails: List[str]) -> Optional[str]:
    if not emails:
        return None
    # Prefer generic inboxes first (better for businesses)
    preferred_prefixes = ["info@", "support@", "sales@", "hello@", "contact@", "office@", "admin@", "billing@"]
    lowered = [e.strip() for e in emails if e and "@" in e]
    lowered_unique = []
    seen = set()
    for e in lowered:
        el = e.lower()
        if el not in seen:
            seen.add(el)
            lowered_unique.append(e)

    for pref in preferred_prefixes:
        for e in lowered_unique:
            if e.lower().startswith(pref):
                return e
    # Otherwise first email
    return lowered_unique[0] if lowered_unique else None

# ---------------------------
# HUNTER
# ---------------------------
def _hunter_domain_search(domain: str, logger=None) -> Tuple[Optional[str], Dict[str, Any]]:
    api_key = os.getenv("HUNTER_API_KEY", "").strip()
    if not api_key:
        return None, {"ok": False, "error": "HUNTER_API_KEY missing"}
    url = "https://api.hunter.io/v2/domain-search"
    try:
        r = requests.get(url, params={"domain": domain, "api_key": api_key}, timeout=20)
        if r.status_code != 200:
            return None, {"ok": False, "status": r.status_code, "body": r.text[:500]}
        data = r.json() or {}
        emails = []
        for item in (data.get("data", {}).get("emails") or []):
            v = item.get("value")
            if v:
                emails.append(v)
        primary = _pick_primary_email(emails)
        return primary, {"ok": True, "emails": emails}
    except Exception as e:
        return None, {"ok": False, "error": repr(e)}

# ---------------------------
# APOLLO (FIXED: X-Api-Key header)
# ---------------------------
def _apollo_org_search(domain: str, logger=None) -> Tuple[Optional[str], Dict[str, Any]]:
    api_key = os.getenv("APOLLO_API_KEY", "").strip()
    if not api_key:
        return None, {"ok": False, "error": "APOLLO_API_KEY missing"}

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Api-Key": api_key,   # IMPORTANT (your logs explicitly require this)
    }

    # Apollo v1 org search - we try to find an organization by domain.
    # If your account/plan requires different endpoints, we'll see it in logs as non-200.
    url = "https://api.apollo.io/v1/organizations/search"
    payload = {"q_organization_domains": domain, "page": 1}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=25)
        if r.status_code != 200:
            return None, {"ok": False, "status": r.status_code, "body": r.text[:800]}
        data = r.json() or {}
        orgs = data.get("organizations") or data.get("organizations", [])
        # Apollo doesn't reliably return public emails at org level.
        # We treat Apollo as a fallback for "generic email" if present in response fields.
        found = []

        # Some responses may include fields like: "email", "emails", "public_email"
        for o in (orgs or []):
            for key in ["email", "public_email"]:
                v = o.get(key)
                if isinstance(v, str) and "@" in v:
                    found.append(v)
            v2 = o.get("emails")
            if isinstance(v2, list):
                for e in v2:
                    if isinstance(e, str) and "@" in e:
                        found.append(e)

        primary = _pick_primary_email(found)
        return primary, {"ok": True, "emails": found, "organizations_found": len(orgs or [])}
    except Exception as e:
        return None, {"ok": False, "error": repr(e)}

# ---------------------------
# SNOV (FIXED: v2 task flow)
# ---------------------------
def _snov_get_access_token(logger=None) -> Tuple[Optional[str], Dict[str, Any]]:
    client_id = os.getenv("SNOV_CLIENT_ID", "").strip()
    client_secret = os.getenv("SNOV_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None, {"ok": False, "error": "SNOV_CLIENT_ID or SNOV_CLIENT_SECRET missing"}

    url = "https://api.snov.io/v1/oauth/access_token"
    try:
        r = requests.post(url, data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}, timeout=20)
        if r.status_code != 200:
            return None, {"ok": False, "status": r.status_code, "body": r.text[:800]}
        token = (r.json() or {}).get("access_token")
        return token, {"ok": True}
    except Exception as e:
        return None, {"ok": False, "error": repr(e)}

def _snov_domain_emails(domain: str, logger=None) -> Tuple[Optional[str], Dict[str, Any]]:
    token, meta = _snov_get_access_token(logger=logger)
    if not token:
        return None, {"ok": False, "auth": meta}

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # v2 start -> result/{task_hash}
    start_url = f"https://api.snov.io/v2/domain-search/domain-emails/start?domain={domain}"
    try:
        start = requests.post(start_url, headers=headers, timeout=25)
        if start.status_code != 200:
            return None, {"ok": False, "stage": "start", "status": start.status_code, "body": start.text[:800]}

        start_json = start.json() or {}
        task_hash = start_json.get("task_hash")
        if not task_hash:
            return None, {"ok": False, "stage": "start", "body": json.dumps(start_json)[:800]}

        result_url = f"https://api.snov.io/v2/domain-search/domain-emails/result/{task_hash}"

        # poll a few times
        emails = []
        for _ in range(8):
            res = requests.get(result_url, headers=headers, timeout=25)
            if res.status_code != 200:
                return None, {"ok": False, "stage": "result", "status": res.status_code, "body": res.text[:800]}

            res_json = res.json() or {}
            status = res_json.get("status")
            if status == "completed":
                for item in (res_json.get("emails") or []):
                    # docs show list of emails; sometimes objects, sometimes strings
                    if isinstance(item, str) and "@" in item:
                        emails.append(item)
                    elif isinstance(item, dict):
                        v = item.get("email")
                        if isinstance(v, str) and "@" in v:
                            emails.append(v)
                primary = _pick_primary_email(emails)
                return primary, {"ok": True, "emails": emails}
            if status == "in_progress":
                time.sleep(0.7)
                continue

            # unexpected status
            return None, {"ok": False, "stage": "result", "unexpected_status": status, "body": json.dumps(res_json)[:800]}

        return None, {"ok": False, "stage": "result", "error": "timeout_waiting_for_completion"}
    except Exception as e:
        return None, {"ok": False, "error": repr(e)}

# ---------------------------
# FULLENRICH (placeholder hook)
# ---------------------------
def _fullenrich_domain(domain: str, logger=None) -> Tuple[Optional[str], Dict[str, Any]]:
    # Keep this as a no-op unless you already have an endpoint + key wired.
    # This prevents "fake success" and makes it obvious in logs if it's not configured.
    key = os.getenv("FULLENRICH_API_KEY", "").strip()
    if not key:
        return None, {"ok": False, "error": "FULLENRICH_API_KEY missing (not configured)"}
    # If you have a real endpoint, replace this implementation.
    return None, {"ok": False, "error": "FullEnrich not yet implemented in code"}

# ============================================================
# PUBLIC API USED BY PIPELINE
# ============================================================
def enrich_emails_for_domain(domain: str, company_name: Optional[str] = None, logger=None) -> Dict[str, Any]:
    """
    Waterfall:
      Hunter -> Snov -> Apollo -> FullEnrich
    Returns a dict containing:
      primary_email, primary_email_source, primary_email_confidence, generic_emails_json, person_emails_json, catchall_emails_json
    """
    out: Dict[str, Any] = {
        "primary_email": None,
        "primary_email_source": None,
        "primary_email_confidence": None,
        "generic_emails_json": None,
        "person_emails_json": None,
        "catchall_emails_json": None,
    }

    if not domain or _is_bad_domain(domain):
        if logger:
            logger.info(f"Email enrichment skipped (bad/empty domain): {domain}")
        return out

    # 1) Hunter
    email, meta = _hunter_domain_search(domain, logger=logger)
    if logger:
        logger.info(f"Email provider=Hunter domain={domain} ok={meta.get('ok')} status={meta.get('status')}")
    if email:
        out["primary_email"] = email
        out["primary_email_source"] = "hunter"
        out["primary_email_confidence"] = "high"
        out["generic_emails_json"] = json.dumps(meta.get("emails") or [])
        return out

    # 2) Snov
    email, meta = _snov_domain_emails(domain, logger=logger)
    if logger:
        logger.info(f"Email provider=Snov domain={domain} ok={meta.get('ok')} stage={meta.get('stage')} status={meta.get('status')}")
    if email:
        out["primary_email"] = email
        out["primary_email_source"] = "snov"
        out["primary_email_confidence"] = "medium"
        out["generic_emails_json"] = json.dumps(meta.get("emails") or [])
        return out

    # 3) Apollo
    email, meta = _apollo_org_search(domain, logger=logger)
    if logger:
        logger.info(f"Email provider=Apollo domain={domain} ok={meta.get('ok')} status={meta.get('status')}")
    if email:
        out["primary_email"] = email
        out["primary_email_source"] = "apollo"
        out["primary_email_confidence"] = "medium"
        out["generic_emails_json"] = json.dumps(meta.get("emails") or [])
        return out

    # 4) FullEnrich
    email, meta = _fullenrich_domain(domain, logger=logger)
    if logger:
        logger.info(f"Email provider=FullEnrich domain={domain} ok={meta.get('ok')} status={meta.get('status')}")
    if email:
        out["primary_email"] = email
        out["primary_email_source"] = "fullenrich"
        out["primary_email_confidence"] = "medium"
        return out

    return out
