import os
import re
import json
import requests
from typing import Any, Dict, Optional, Tuple

# domains where "email by domain" is usually garbage / not meaningful
SKIP_EMAIL_DOMAINS = {
    "facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com",
    "linkedin.com", "www.linkedin.com",
    "yelp.com", "www.yelp.com",
    "maps.google.com", "google.com",
    "tiktok.com", "www.tiktok.com",
    "x.com", "twitter.com", "www.twitter.com",
}

def _norm_domain(domain: Optional[str]) -> Optional[str]:
    if not domain:
        return None
    d = str(domain).strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0]
    d = d.replace("www.", "")
    return d or None

def _pick_best_email(emails: list) -> Optional[str]:
    # Simple, safe selection: prefer non-noreply, non-example, basic validity
    if not emails:
        return None
    def score(e: str) -> int:
        e = (e or "").strip().lower()
        if "@" not in e: return -999
        if any(bad in e for bad in ["noreply", "no-reply", "donotreply", "example.com"]): return -50
        if e.startswith("info@"): return 10
        if e.startswith("support@"): return 9
        if e.startswith("contact@"): return 8
        return 5
    emails = [e for e in emails if isinstance(e, str)]
    emails = sorted(set([e.strip() for e in emails if e.strip()]), key=score, reverse=True)
    return emails[0] if emails else None

def _hunter_domain_search(domain: str) -> Tuple[Optional[str], Dict[str, Any]]:
    key = os.getenv("HUNTER_API_KEY") or os.getenv("HUNTER_KEY")
    if not key:
        return None, {"ok": False, "provider": "hunter", "error": "missing_api_key"}
    try:
        url = "https://api.hunter.io/v2/domain-search"
        params = {"domain": domain, "api_key": key, "limit": 50}
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return None, {"ok": False, "provider": "hunter", "status": r.status_code, "body": r.text[:500]}
        data = r.json() or {}
        emails = []
        for item in (data.get("data", {}).get("emails") or []):
            val = item.get("value")
            if val: emails.append(val)
        best = _pick_best_email(emails)
        return best, {"ok": True, "provider": "hunter", "count": len(emails)}
    except Exception as e:
        return None, {"ok": False, "provider": "hunter", "error": repr(e)}

def _apollo_org_search(domain: str) -> Tuple[Optional[str], Dict[str, Any]]:
    # IMPORTANT: Apollo requires X-Api-Key header (your logs literally say this)
    key = os.getenv("APOLLO_API_KEY") or os.getenv("APOLLO_KEY")
    if not key:
        return None, {"ok": False, "provider": "apollo", "error": "missing_api_key"}
    try:
        url = "https://api.apollo.io/v1/organizations/search"
        headers = {"X-Api-Key": key, "Content-Type": "application/json"}
        payload = {"q_organization_domains": domain, "page": 1}
        r = requests.post(url, headers=headers, json=payload, timeout=25)
        if r.status_code != 200:
            return None, {"ok": False, "provider": "apollo", "status": r.status_code, "body": r.text[:500]}
        data = r.json() or {}
        # Apollo org search often doesn't return emails directly; but sometimes includes "email" fields in org/person results.
        # We safely scan for any obvious email strings in the response.
        blob = json.dumps(data)
        found = sorted(set(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", blob, flags=re.I)))
        best = _pick_best_email(found)
        return best, {"ok": True, "provider": "apollo", "count": len(found)}
    except Exception as e:
        return None, {"ok": False, "provider": "apollo", "error": repr(e)}

def _snov_domain_search(domain: str) -> Tuple[Optional[str], Dict[str, Any]]:
    # Your logs show 404 on get-domain-emails-with-info endpoints.
    # So we DO NOT hard-fail the pipeline; we just report the failure cleanly.
    client_id = os.getenv("SNOV_CLIENT_ID")
    client_secret = os.getenv("SNOV_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None, {"ok": False, "provider": "snov", "error": "missing_client_id_or_secret"}

    # If your existing project already has a working Snov implementation elsewhere,
    # keep it there. This function is intentionally conservative to avoid breaking deploys.
    try:
        # NOTE: leaving as "attempted but non-blocking" until you confirm the correct endpoint for your account plan.
        return None, {"ok": False, "provider": "snov", "error": "endpoint_not_configured_confirm_snov_api_path"}
    except Exception as e:
        return None, {"ok": False, "provider": "snov", "error": repr(e)}

def _fullenrich_domain_search(domain: str) -> Tuple[Optional[str], Dict[str, Any]]:
    key = os.getenv("FULLENRICH_API_KEY") or os.getenv("FULL_ENRICH_API_KEY")
    if not key:
        return None, {"ok": False, "provider": "fullenrich", "error": "missing_api_key"}
    try:
        # IMPORTANT: endpoint varies by vendor/account. Keep non-blocking until confirmed.
        return None, {"ok": False, "provider": "fullenrich", "error": "endpoint_not_configured_confirm_fullenrich_api_path"}
    except Exception as e:
        return None, {"ok": False, "provider": "fullenrich", "error": repr(e)}

def run_email_waterfall(domain: Optional[str], logger=None) -> Dict[str, Any]:
    """
    Returns a stable shape that pipeline can always merge:
    - primary_email
    - primary_email_source
    - primary_email_confidence
    - provider_debug (list of provider attempt summaries)
    """
    d = _norm_domain(domain)
    out: Dict[str, Any] = {
        "primary_email": None,
        "primary_email_source": None,
        "primary_email_confidence": None,
        "provider_debug": [],
    }

    if not d or d in SKIP_EMAIL_DOMAINS:
        out["provider_debug"].append({"provider": "skip", "domain": d, "reason": "missing_or_low_value_domain"})
        return out

    # Hunter → Snov → Apollo → FullEnrich (your desired waterfall)
    attempts = []

    email, meta = _hunter_domain_search(d)
    attempts.append(meta)
    if email:
        out.update({"primary_email": email, "primary_email_source": "hunter", "primary_email_confidence": "medium"})
        out["provider_debug"] = attempts
        return out

    email, meta = _snov_domain_search(d)
    attempts.append(meta)
    if email:
        out.update({"primary_email": email, "primary_email_source": "snov", "primary_email_confidence": "medium"})
        out["provider_debug"] = attempts
        return out

    email, meta = _apollo_org_search(d)
    attempts.append(meta)
    if email:
        out.update({"primary_email": email, "primary_email_source": "apollo", "primary_email_confidence": "low"})
        out["provider_debug"] = attempts
        return out

    email, meta = _fullenrich_domain_search(d)
    attempts.append(meta)
    if email:
        out.update({"primary_email": email, "primary_email_source": "fullenrich", "primary_email_confidence": "low"})
        out["provider_debug"] = attempts
        return out

    out["provider_debug"] = attempts
    return out
