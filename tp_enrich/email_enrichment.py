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


def _get_snov_access_token(logger=None) -> Optional[str]:
    # Prefer a pre-set token if you store it
    tok = os.getenv("SNOV_ACCESS_TOKEN")
    if tok:
        return tok

    client_id = os.getenv("SNOV_CLIENT_ID")
    client_secret = os.getenv("SNOV_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None

    url = "https://api.snov.io/v1/oauth/access_token"
    if logger:
        logger.info(f"SNOV token URL: {url}")

    r = requests.post(
        url,
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=25,
    )
    if r.status_code != 200:
        if logger:
            logger.info(f"   -> Snov token failed: HTTP {r.status_code}: {r.text[:200]}")
        return None

    js = r.json() or {}
    return js.get("access_token")

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
    Uses SNOV new Domain Search endpoints:
      POST https://api.snov.io/v2/domain-search/domain-emails/start?domain=...
      GET  https://api.snov.io/v2/domain-search/domain-emails/result/{task_hash}
    (Docs show result contains: data: [{email: ...}, ...]) 
    """
    token = _get_snov_access_token(logger=logger)
    if not token:
        return {"_attempted": False, "_reason": "missing SNOV auth env (SNOV_ACCESS_TOKEN or SNOV_CLIENT_ID/SNOV_CLIENT_SECRET)"}

    headers = {"Authorization": f"Bearer {token}"}

    start_url = "https://api.snov.io/v2/domain-search/domain-emails/start"
    if logger:
        logger.info(f"SNOV domain email URL: {start_url} (domain={domain})")

    # NOTE: docs show POST with params=domain 
    r = requests.post(start_url, params={"domain": domain}, headers=headers, timeout=30)
    if r.status_code != 200:
        return {"_attempted": True, "_reason": f"HTTP {r.status_code}: {r.text[:200]}"}

    js = r.json() or {}
    result_url = (js.get("links") or {}).get("result")
    task_hash = (js.get("meta") or {}).get("task_hash")

    if not result_url and task_hash:
        result_url = f"https://api.snov.io/v2/domain-search/domain-emails/result/{task_hash}"

    if not result_url:
        return {"_attempted": True, "_reason": f"no result link/task_hash in response: {str(js)[:200]}"}

    # Poll a couple times in case it's "in progress"
    emails = []
    last_status = None
    for _ in range(3):
        rr = requests.get(result_url, headers=headers, timeout=30)
        if rr.status_code != 200:
            return {"_attempted": True, "_reason": f"result HTTP {rr.status_code}: {rr.text[:200]}"}
        out = rr.json() or {}
        last_status = out.get("status")

        for item in (out.get("data") or []):
            e = _clean_email(item.get("email"))
            if e:
                emails.append(e)

        if emails or last_status == "completed":
            break

        import time
        time.sleep(1.0)

    emails = sorted(set(emails))
    generic = [e for e in emails if any(p in e for p in ["info@", "support@", "sales@", "hello@", "contact@", "admin@", "billing@"])]
    person = [e for e in emails if e not in generic]

    return {
        "_attempted": True,
        "source": "snov",
        "confidence": "medium" if emails else None,
        "generic": generic,
        "person": person,
        "catchall": [],
    }



def _fullenrich(domain: str, company_name: Optional[str] = None, logger=None) -> Dict[str, Any]:
    """
    FullEnrich DOES NOT support domain-only.
    They require:
      - "name" (enrichment name)
      - "datas": [{ firstname, lastname, domain, enrich_fields }]
    Your errors (name.empty / data.empty) are exactly this missing payload requirement.
    """
    api_key = os.getenv("FULLENRICH_API_KEY")
    enrichment_name = os.getenv("FULLENRICH_ENRICHMENT_NAME")  # <-- YOU MUST SET THIS IN RAILWAY
    if not api_key:
        return {"_attempted": False, "_reason": "missing FULLENRICH_API_KEY"}
    if not enrichment_name:
        return {"_attempted": False, "_reason": "missing FULLENRICH_ENRICHMENT_NAME"}

    url = "https://app.fullenrich.com/api/v1/contact/enrich/bulk"
    if logger:
        logger.info(f"FULLENRICH URL: {url} (domain={domain})")

    # We don't have a real person name, so we send a generic contact placeholder.
    # This is the minimum shape their support described.
    payload = {
        "name": enrichment_name,
        "datas": [
            {
                "firstname": "Info",
                "lastname": "Team",
                "domain": domain,
                "enrich_fields": ["contact.emails"],
            }
        ],
    }

    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    )
    if r.status_code != 200:
        return {"_attempted": True, "_reason": f"HTTP {r.status_code}: {r.text[:200]}"}

    js = r.json() or {}

    # Try to extract emails from common shapes (keep this flexible)
    emails = []
    for row in (js.get("data") or js.get("datas") or js.get("results") or []):
        contact = row.get("contact") if isinstance(row, dict) else None
        if isinstance(contact, dict):
            for e in (contact.get("emails") or []):
                e2 = _clean_email(e if isinstance(e, str) else e.get("email"))
                if e2:
                    emails.append(e2)

    emails = sorted(set(emails))
    generic = [e for e in emails if any(p in e for p in ["info@", "support@", "sales@", "hello@", "contact@", "admin@", "billing@"])]
    person = [e for e in emails if e not in generic]

    return {
        "_attempted": True,
        "source": "fullenrich",
        "confidence": "medium" if emails else None,
        "generic": generic,
        "person": person,
        "catchall": [],
    }



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
