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


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")

def _safe_txt(s: str, n: int = 200) -> str:
    try:
        return (s or "")[:n]
    except Exception:
        return ""

def _snov_get_access_token(logger=None) -> Optional[str]:
    """
    Supports either:
      - SNOV_ACCESS_TOKEN (preferred if you already have it)
      - SNOV_CLIENT_ID + SNOV_CLIENT_SECRET -> token request
    """
    access_token = (os.getenv("SNOV_ACCESS_TOKEN") or "").strip()
    if access_token:
        return access_token

    client_id = (os.getenv("SNOV_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("SNOV_CLIENT_SECRET") or "").strip()
    if not (client_id and client_secret):
        return None

    token_url = os.getenv("SNOV_TOKEN_URL", "https://api.snov.io/v1/oauth/access_token").strip()
    if logger:
        logger.info(f"SNOV token URL: {token_url}")

    try:
        r = requests.post(
            token_url,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=25,
        )
        if r.status_code != 200:
            if logger:
                logger.info(f"   -> Snov token failed: HTTP {r.status_code}: {_safe_txt(r.text)}")
            return None
        js = r.json() or {}
        return (js.get("access_token") or "").strip() or None
    except Exception as e:
        if logger:
            logger.info(f"   -> Snov token exception: {repr(e)}")
        return None


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

def _snov(domain: str, logger=None) -> Dict[str, Any]:
    """
    SNOV FIX:
      - Your logs show 404 on several endpoints.
      - Different SNOV accounts / docs / versions may support different routes.
      - So: try a list of endpoints (env-configurable), and log each attempt clearly.

    Env options:
      - SNOV_DOMAIN_EMAIL_URLS  (comma-separated list)
        Example:
          https://api.snov.io/v2/domain-emails-with-info,
          https://api.snov.io/v2/get-domain-emails-with-info,
          https://api.snov.io/v1/get-domain-emails-with-info

      - SNOV_ACCESS_TOKEN  OR  SNOV_CLIENT_ID + SNOV_CLIENT_SECRET
    """
    if _env_flag("SNOV_DISABLED", "0"):
        return {"_attempted": False, "_reason": "SNOV_DISABLED=1"}

    token = _snov_get_access_token(logger=logger)
    if not token:
        return {"_attempted": False, "_reason": "missing SNOV auth (SNOV_ACCESS_TOKEN or SNOV_CLIENT_ID/SNOV_CLIENT_SECRET)"}

    # Default list (can be overridden without code changes)
    urls_raw = os.getenv(
        "SNOV_DOMAIN_EMAIL_URLS",
        "https://api.snov.io/v2/domain-emails-with-info,"
        "https://api.snov.io/v2/get-domain-emails-with-info,"
        "https://api.snov.io/v1/get-domain-emails-with-info",
    )
    urls = [u.strip() for u in urls_raw.split(",") if u.strip()]

    last_err = None
    for url in urls:
        try:
            if logger:
                logger.info(f"SNOV domain email URL: {url} (domain={domain})")

            # Many SNOV endpoints are form-encoded POSTs
            r = requests.post(url, data={"access_token": token, "domain": domain}, timeout=30)

            if r.status_code == 403:
                last_err = f"HTTP 403 (permission): {_safe_txt(r.text)}"
                if logger:
                    logger.info(f"   -> Snov: HTTP 403: {_safe_txt(r.text)} (no emails)")
                # 403 usually means your SNOV plan/key lacks that method — stop early
                break

            if r.status_code == 404:
                last_err = f"HTTP 404 (endpoint not found): {_safe_txt(r.text)}"
                # try next URL
                continue

            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {_safe_txt(r.text)}"
                # try next URL
                continue

            js = r.json() or {}

            # Be flexible: SNOV sometimes returns different shapes
            candidates = []
            if isinstance(js, dict):
                if isinstance(js.get("emails"), list):
                    candidates = js.get("emails") or []
                elif isinstance(js.get("data"), dict) and isinstance(js["data"].get("emails"), list):
                    candidates = js["data"].get("emails") or []

            emails = []
            for item in candidates:
                if isinstance(item, str):
                    emails.append(item)
                elif isinstance(item, dict):
                    e = item.get("email") or item.get("value") or item.get("address")
                    if e:
                        emails.append(str(e).strip())

            emails = [e for e in emails if "@" in e]  # basic sanity

            generic = [e for e in emails if any(p in e.lower() for p in ["info@", "support@", "sales@", "hello@", "contact@", "admin@"])]
            person = [e for e in emails if e not in generic]

            return {
                "_attempted": True,
                "source": "snov",
                "confidence": "medium" if emails else None,
                "generic": generic,
                "person": person,
                "catchall": [],
            }

        except Exception as e:
            last_err = f"exception: {repr(e)}"
            continue

    return {"_attempted": True, "_reason": f"snov exhausted; last_err={last_err}"}



def _fullenrich(domain: str, logger=None) -> Dict[str, Any]:
    """
    FULLENRICH FIX:
      - Your logs show DNS failures for api.fullenrich.com
      - FullEnrich docs use app.fullenrich.com/api/v1/...

    IMPORTANT:
      FullEnrich's "domain -> emails" may require a different endpoint depending on your account/product.
      So we make the endpoint configurable and log it clearly.

    Env options:
      - FULLENRICH_API_KEY  (Bearer token)
      - FULLENRICH_DOMAIN_URL
          default: https://app.fullenrich.com/api/v1/contact/enrich/bulk   (CONFIGURE IF NEEDED)
      - FULLENRICH_DISABLED=1 to skip
    """
    if _env_flag("FULLENRICH_DISABLED", "0"):
        return {"_attempted": False, "_reason": "FULLENRICH_DISABLED=1"}

    key = (os.getenv("FULLENRICH_API_KEY") or "").strip()
    if not key:
        return {"_attempted": False, "_reason": "missing FULLENRICH_API_KEY"}

    # NOTE: this default may not match your purchased FullEnrich product;
    # but it will at least hit the *correct host* and give a real HTTP response (not DNS fail).
    url = os.getenv("FULLENRICH_DOMAIN_URL", "https://app.fullenrich.com/api/v1/contact/enrich/bulk").strip()
    if logger:
        logger.info(f"FULLENRICH URL: {url} (domain={domain})")

    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "name": "tp_enrich_domain_lookup",
                "data": [{"domain": domain}],
            },
            timeout=35,
        )

        # Even if this endpoint isn't the right one for your plan, you should now get a real status code.
        if r.status_code != 200:
            if logger:
                logger.info(f"   -> FullEnrich: HTTP {r.status_code}: {_safe_txt(r.text)} (no emails)")
            return {"_attempted": True, "_reason": f"HTTP {r.status_code}: {_safe_txt(r.text)}"}

        js = r.json() or {}

        # If this endpoint returns an async enrichment_id, you'll need to poll "Get Enrichment Result".
        # For now, we just log and return empty if emails aren't directly present.
        emails = []
        if isinstance(js, dict):
            # try common locations
            if isinstance(js.get("emails"), list):
                emails = js.get("emails") or []
            elif isinstance(js.get("data"), list) and js["data"] and isinstance(js["data"][0], dict):
                maybe_emails = js["data"][0].get("emails")
                if isinstance(maybe_emails, list):
                    emails = maybe_emails

        emails = [str(e).strip() for e in emails if isinstance(e, (str,)) and "@" in e]

        generic = [e for e in emails if any(p in e.lower() for p in ["info@", "support@", "sales@", "hello@", "contact@", "admin@"])]
        person = [e for e in emails if e not in generic]

        return {
            "_attempted": True,
            "source": "fullenrich",
            "confidence": "medium" if emails else None,
            "generic": generic,
            "person": person,
            "catchall": [],
        }

    except Exception as e:
        if logger:
            logger.info(f"   -> FullEnrich exception: {repr(e)}")
        return {"_attempted": True, "_reason": f"exception: {repr(e)}"}



def run_email_waterfall(domain: Optional[str], company_name: Optional[str] = None, logger: Any = None, **kwargs) -> Dict[str, Any]:
    """
    STRICT sequential waterfall with self-proving tracking.
    
    Always returns:
      - primary_email fields + *_emails_json
      - email_waterfall_tried: comma-separated list of providers attempted
      - email_waterfall_winner: provider that found emails (or None)
      - provider_status_json: JSON string with attempt details
    
    Logs cannot lie - these fields are set by the function itself.
    """
    
    tried = []
    winner = None
    status = {}
    
    def _log(msg: str):
        if logger:
            logger.info(msg)
    
    _log(f"EMAIL WATERFALL: domain={domain} company={company_name}")
    
    # Handle empty domain
    if not domain:
        out = _pick_primary(None, None, [], [], [])
        out["email_waterfall_tried"] = ""
        out["email_waterfall_winner"] = None
        out["provider_status_json"] = "{}"
        return out
    
    d = str(domain).strip().lower()
    
    # Handle blocked domains
    if any(d == bad or d.endswith("." + bad) for bad in BLOCKED_EMAIL_DOMAINS):
        out = _pick_primary(None, None, [], [], [])
        out["email_waterfall_tried"] = ""
        out["email_waterfall_winner"] = None
        out["provider_status_json"] = json.dumps({"blocked_domain": d})
        return out
    
    # STRICT SEQUENTIAL WATERFALL: Hunter → Snov → Apollo → FullEnrich
    # Each provider runs independently. Track ALL attempts.
    
    # 1. Try Hunter
    tried.append("hunter")
    try:
        payload, note = _hunter(d)
        status["hunter"] = {"attempted": True, "note": note}
        if payload and (payload.get("generic") or payload.get("person") or payload.get("catchall")):
            winner = "hunter"
            out = _pick_primary(payload["source"], payload["confidence"], payload["generic"], payload["person"], payload["catchall"])
            out["email_waterfall_tried"] = ",".join(tried)
            out["email_waterfall_winner"] = winner
            out["provider_status_json"] = json.dumps(status)
            _log(f"   -> Hunter SUCCESS: found {len(payload.get('generic', []))} generic, {len(payload.get('person', []))} person emails")
            return out
        _log(f"   -> Hunter: {note} (no emails)")
    except Exception as e:
        status["hunter"] = {"attempted": True, "note": f"exception: {repr(e)}"}
        _log(f"   -> Hunter exception: {repr(e)}")
    
    # 2. Try Snov
    tried.append("snov")
    try:
        result = _snov(d, logger=logger)
        status["snov"] = {"attempted": result.get("_attempted", True), "note": result.get("_reason", "ok")}
        if result.get("_attempted") and not result.get("_reason") and (result.get("generic") or result.get("person") or result.get("catchall")):
            payload = result
            winner = "snov"
            out = _pick_primary(result["source"], result["confidence"], result["generic"], result["person"], result["catchall"])
            out["email_waterfall_tried"] = ",".join(tried)
            out["email_waterfall_winner"] = winner
            out["provider_status_json"] = json.dumps(status)
            _log(f"   -> Snov SUCCESS: found {len(result.get('generic', []))} generic, {len(result.get('person', []))} person emails")
            return out
        _log(f"   -> Snov: {result.get('_reason', 'no emails')}")
    except Exception as e:
        status["snov"] = {"attempted": True, "note": f"exception: {repr(e)}"}
        _log(f"   -> Snov exception: {repr(e)}")
    
    # 3. Try Apollo
    tried.append("apollo")
    try:
        payload, note = _apollo(d)
        status["apollo"] = {"attempted": True, "note": note}
        if payload and (payload.get("generic") or payload.get("person") or payload.get("catchall")):
            winner = "apollo"
            out = _pick_primary(payload["source"], payload["confidence"], payload["generic"], payload["person"], payload["catchall"])
            out["email_waterfall_tried"] = ",".join(tried)
            out["email_waterfall_winner"] = winner
            out["provider_status_json"] = json.dumps(status)
            _log(f"   -> Apollo SUCCESS: found {len(payload.get('generic', []))} generic, {len(payload.get('person', []))} person emails")
            return out
        _log(f"   -> Apollo: {note} (no emails)")
    except Exception as e:
        status["apollo"] = {"attempted": True, "note": f"exception: {repr(e)}"}
        _log(f"   -> Apollo exception: {repr(e)}")
    
    # 4. Try FullEnrich
    tried.append("fullenrich")
    try:
        result = _fullenrich(d, logger=logger)
        status["fullenrich"] = {"attempted": result.get("_attempted", True), "note": result.get("_reason", "ok")}
        if result.get("_attempted") and not result.get("_reason") and (result.get("generic") or result.get("person") or result.get("catchall")):
            payload = result
            winner = "fullenrich"
            out = _pick_primary(result["source"], result["confidence"], result["generic"], result["person"], result["catchall"])
            out["email_waterfall_tried"] = ",".join(tried)
            out["email_waterfall_winner"] = winner
            out["provider_status_json"] = json.dumps(status)
            _log(f"   -> FullEnrich SUCCESS: found {len(result.get('generic', []))} generic, {len(result.get('person', []))} person emails")
            return out
        _log(f"   -> FullEnrich: {result.get('_reason', 'no emails')}")
    except Exception as e:
        status["fullenrich"] = {"attempted": True, "note": f"exception: {repr(e)}"}
        _log(f"   -> FullEnrich exception: {repr(e)}")
    
    # All providers failed or returned no emails
    _log(f"   -> All providers exhausted. Tried: {','.join(tried)}")
    out = _pick_primary(None, None, [], [], [])
    out["email_waterfall_tried"] = ",".join(tried)
    out["email_waterfall_winner"] = None
    out["provider_status_json"] = json.dumps(status)
    return out


def enrich_emails_for_domain(domain: str, company_name: Optional[str] = None, logger=None) -> Dict[str, Any]:
    """
    Legacy function name - calls run_email_waterfall for backward compatibility
    """
    return run_email_waterfall(domain=domain, company_name=company_name, logger=logger)
