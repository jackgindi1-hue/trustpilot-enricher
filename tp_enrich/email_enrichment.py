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

def run_email_waterfall(domain: Optional[str], logger=None, company_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns a stable shape that pipeline can always merge:
    - primary_email
    - primary_email_source
    - primary_email_confidence
    - provider_debug (list of provider attempt summaries)
    - email_providers_tried (string: "hunter -> snov -> apollo")
    - email_provider_errors_json (json string with errors)
    - email_waterfall_winner (string: which provider won)
    """
    d = _norm_domain(domain)
    out: Dict[str, Any] = {
        "primary_email": None,
        "primary_email_source": None,
        "primary_email_confidence": None,
        "provider_debug": [],
        "email_providers_tried": "",
        "email_provider_errors_json": "{}",
        "email_waterfall_winner": None,
    }

    if not d or d in SKIP_EMAIL_DOMAINS:
        out["provider_debug"].append({"provider": "skip", "domain": d, "reason": "missing_or_low_value_domain"})
        out["email_providers_tried"] = "skip"
        out["email_provider_errors_json"] = json.dumps({"skip": "missing_or_low_value_domain"})
        return out

    # Track attempts and errors
    attempts = []
    tried = []
    errors = {}

    # Helper to record attempt
    def _record(provider: str, meta: Dict[str, Any], email: Optional[str]):
        tried.append(provider)
        attempts.append(meta)
        if not meta.get("ok") and meta.get("error"):
            errors[provider] = meta.get("error")
        return email

    # Hunter → Snov → Apollo → FullEnrich (your desired waterfall)
    # 1) HUNTER (optional skip via env var)
    if os.getenv("DISABLE_HUNTER_EMAIL", "").strip() == "1":
        tried.append("hunter")
        errors["hunter"] = "SKIPPED_BY_ENV(DISABLE_HUNTER_EMAIL=1)"
        attempts.append({"ok": False, "provider": "hunter", "error": "SKIPPED_BY_ENV"})
    else:
        email, meta = _hunter_domain_search(d)
        _record("hunter", meta, email)
        if email:
            out.update({
                "primary_email": email,
                "primary_email_source": "hunter",
                "primary_email_confidence": "medium",
                "provider_debug": attempts,
                "email_providers_tried": " -> ".join(tried),
                "email_provider_errors_json": json.dumps(errors),
                "email_waterfall_winner": "hunter",
            })
            return out

    # 2) SNOV
    email, meta = _snov_domain_search(d)
    _record("snov", meta, email)
    if email:
        out.update({
            "primary_email": email,
            "primary_email_source": "snov",
            "primary_email_confidence": "medium",
            "provider_debug": attempts,
            "email_providers_tried": " -> ".join(tried),
            "email_provider_errors_json": json.dumps(errors),
            "email_waterfall_winner": "snov",
        })
        return out

    # 3) APOLLO
    email, meta = _apollo_org_search(d)
    _record("apollo", meta, email)
    if email:
        out.update({
            "primary_email": email,
            "primary_email_source": "apollo",
            "primary_email_confidence": "low",
            "provider_debug": attempts,
            "email_providers_tried": " -> ".join(tried),
            "email_provider_errors_json": json.dumps(errors),
            "email_waterfall_winner": "apollo",
        })
        return out

    # 4) FULLENRICH
    email, meta = _fullenrich_domain_search(d)
    _record("fullenrich", meta, email)
    if email:
        out.update({
            "primary_email": email,
            "primary_email_source": "fullenrich",
            "primary_email_confidence": "low",
            "provider_debug": attempts,
            "email_providers_tried": " -> ".join(tried),
            "email_provider_errors_json": json.dumps(errors),
            "email_waterfall_winner": "fullenrich",
        })
        return out

    # Nobody found an email
    out["provider_debug"] = attempts
    out["email_providers_tried"] = " -> ".join(tried)
    out["email_provider_errors_json"] = json.dumps(errors)
    out["email_waterfall_winner"] = None
    return out


# ============================================================
# RESULT FINALIZATION HELPER
# ============================================================

def _finalize_email_result(result: dict) -> dict:
    """
    Ensures email result has all required fields populated correctly.

    Critical fixes:
    - If email exists but source is blank/None/NaN, set to "unknown"
    - Copy email_source to primary_email_source if needed
    - Ensure JSON fields never return None
    """
    email = result.get("primary_email")
    source = result.get("primary_email_source")

    # If we have an email but no source, mark it explicitly
    if email and (source is None or str(source).strip() == "" or str(source).lower() == "nan"):
        # Prefer explicit provider if present
        if result.get("email_source"):
            result["primary_email_source"] = result["email_source"]
        else:
            result["primary_email_source"] = "unknown"

    # Ensure JSON fields never return None
    for k in [
        "generic_emails_json",
        "person_emails_json",
        "catchall_emails_json"
    ]:
        if k not in result or result[k] is None:
            result[k] = "[]"

    # Ensure tracking fields are present
    if "email_providers_tried" not in result or result["email_providers_tried"] is None:
        result["email_providers_tried"] = ""
    if "email_provider_errors_json" not in result or result["email_provider_errors_json"] is None:
        result["email_provider_errors_json"] = "{}"
    if "email_waterfall_winner" not in result or result["email_waterfall_winner"] is None:
        result["email_waterfall_winner"] = result.get("primary_email_source")

    return result


# ============================================================
# BACKWARDS COMPATIBILITY WRAPPER
# ============================================================

def enrich_emails_for_domain(domain: str, logger=None, company_name: str = None) -> dict:
    """
    Backwards-compatible wrapper so existing pipeline imports keep working.

    Returns keys that pipeline expects:
      - primary_email
      - primary_email_source
      - primary_email_confidence
      - generic_emails_json
      - person_emails_json
      - catchall_emails_json
      - email_providers_tried (NEW)
      - email_provider_errors_json (NEW)
      - email_waterfall_winner (NEW)
    """
    # run_email_waterfall is the new implementation
    result = run_email_waterfall(domain, logger=logger, company_name=company_name)

    primary_email = result.get("primary_email")
    primary_email_source = result.get("primary_email_source")
    primary_email_confidence = result.get("primary_email_confidence")

    # Keep the existing CSV/debug columns populated in a safe way:
    provider_debug = result.get("provider_debug") or []
    # Put everything in generic_emails_json for now (so you can see attempts/results)
    generic_emails_json = json.dumps(provider_debug)

    email_result = {
        "primary_email": primary_email,
        "primary_email_source": primary_email_source,
        "primary_email_confidence": primary_email_confidence,
        "generic_emails_json": generic_emails_json,
        "person_emails_json": None,
        "catchall_emails_json": None,
        # NEW tracking fields
        "email_providers_tried": result.get("email_providers_tried", ""),
        "email_provider_errors_json": result.get("email_provider_errors_json", "{}"),
        "email_waterfall_winner": result.get("email_waterfall_winner"),
    }

    # Finalize and return
    return _finalize_email_result(email_result)

# Some codebases used this older name; alias it too just in case.
enrich_email_for_domain = enrich_emails_for_domain

# ============================================================
# END BACKWARDS COMPATIBILITY WRAPPER
# ============================================================
