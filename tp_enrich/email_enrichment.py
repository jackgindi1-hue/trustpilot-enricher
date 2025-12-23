# ============================================================
# PHASE 1 FINAL PATCH
# Purpose:
# 1) Make email waterfall stable (no missing symbol crashes)
# 2) Make it scalable (SNOV cannot poll forever / kill runtime)
# 3) Keep Hunter primary; only try others if Hunter fails
# 4) Keep provider-proof logging + tried/winner tracking
# ============================================================
import os
import re
import json
import time
import logging
import requests
from typing import Any, Dict, Optional
GENERIC_PREFIXES = ("info@", "support@", "sales@", "hello@", "contact@", "admin@", "billing@", "help@")
# ============================================================
# BOOT-TIME ENV CHECK (logs once on container start)
# ============================================================
try:
    from .phase2_enrichment import hunter_env_debug
    hunter_env_debug(logging.getLogger("uvicorn"))
except ImportError:
    # Fallback boot check if phase2_enrichment not available
    hk = os.getenv('HUNTER_KEY') or os.getenv('HUNTER_API_KEY') or os.getenv('HUNTER_IO_KEY')
    logging.getLogger("uvicorn").info(
        f"BOOT ENV CHECK: "
        f"HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} "
        f"(len={len(os.getenv('HUNTER_KEY') or '')}) | "
        f"HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} "
        f"(len={len(os.getenv('HUNTER_API_KEY') or '')}) | "
        f"chosen={hk[:3] + '***' + hk[-3:] if hk and len(hk) > 6 else ('***' if hk else 'None')}"
    )
# ============================================================
# HELPER: Mask API keys for safe logging
# ============================================================
def _mask(s: Optional[str]) -> str:
    """Mask API key for safe logging (show first 3 + last 3 chars)"""
    if not s:
        return "None"
    s = str(s)
    if len(s) <= 6:
        return "***"
    return s[:3] + "***" + s[-3:]
def _clean_email(s: str) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", s, re.I):
        return s.lower()
    return None
def _split_generic_person(emails):
    emails = [e for e in (emails or []) if _clean_email(e)]
    generic = [e for e in emails if e.startswith(GENERIC_PREFIXES)]
    person = [e for e in emails if e not in generic]
    return generic, person
def _pick_primary_email(source: Optional[str], confidence: Optional[str], generic, person, catchall):
    generic = generic or []
    person = person or []
    catchall = catchall or []
    # Prefer generic -> person -> catchall
    for e in list(generic) + list(person) + list(catchall):
        e2 = _clean_email(e)
        if e2:
            return {
                "primary_email": e2,
                "primary_email_type": "generic" if e in generic else ("person" if e in person else "catchall"),
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
def _log(logger: Any, msg: str):
    if logger:
        try:
            logger.info(msg)
        except Exception:
            pass
# ============================================================
# HUNTER (domain-search) - ENHANCED DEBUG VERSION
# ============================================================
def _hunter_domain_search(domain: str, logger=None) -> Dict[str, Any]:
    """
    Hunter.io domain search with comprehensive debug logging
    Returns dict with:
        - ok: bool (success/failure)
        - attempted: bool (whether API call was attempted)
        - reason: str (error message if failed)
        - source: str ('hunter')
        - confidence: str ('high'/'medium'/None)
        - generic: list (generic emails like info@, support@)
        - person: list (person emails)
        - catchall: list (catchall emails)
    """
    # Use centralized get_hunter_key from phase2
    try:
        from .phase2_enrichment import get_hunter_key
        key = get_hunter_key()
    except ImportError:
        # Fallback: prioritize HUNTER_KEY (what you have in Railway)
        key = os.getenv("HUNTER_KEY") or os.getenv("HUNTER_API_KEY") or os.getenv("HUNTER_IO_KEY")
    # ENHANCED DEBUG LOGGING
    if logger:
        logger.info(
            f"HUNTER ENV CHECK: "
            f"HUNTER_API_KEY present={bool(os.getenv('HUNTER_API_KEY'))} "
            f"(len={len(os.getenv('HUNTER_API_KEY') or '')}) | "
            f"HUNTER_KEY present={bool(os.getenv('HUNTER_KEY'))} "
            f"(len={len(os.getenv('HUNTER_KEY') or '')}) | "
            f"chosen={_mask(key)}"
        )
    if not key:
        return {
            "ok": False,
            "attempted": False,
            "reason": "missing HUNTER_KEY (or HUNTER_API_KEY or HUNTER_IO_KEY)"
        }
    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": key}
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return {
                "ok": False,
                "attempted": True,
                "reason": f"HTTP {r.status_code}: {r.text[:200]}"
            }
        data = r.json() or {}
        emails = []
        for item in (data.get("data", {}).get("emails") or []):
            e = _clean_email(item.get("value"))
            if e:
                emails.append(e)
        # Split into generic vs person emails
        generic = [
            e for e in emails
            if any(p in e for p in [
                "info@", "support@", "sales@", "hello@", "contact@", "admin@"
            ])
        ]
        person = [e for e in emails if e not in generic]
        return {
            "ok": True,
            "attempted": True,
            "source": "hunter",
            "confidence": "high" if generic else ("medium" if person else None),
            "generic": generic,
            "person": person,
            "catchall": [],
        }
    except Exception as ex:
        return {
            "ok": False,
            "attempted": True,
            "reason": f"exception: {repr(ex)}"
        }
# SNOV (async domain emails)
def _snov_get_token(logger=None) -> Optional[str]:
    client_id = os.getenv("SNOV_CLIENT_ID")
    client_secret = os.getenv("SNOV_CLIENT_SECRET")
    access_token = os.getenv("SNOV_ACCESS_TOKEN")
    if access_token:
        return access_token
    if not (client_id and client_secret):
        return None
    url = "https://api.snov.io/v1/oauth/access_token"
    try:
        r = requests.post(
            url,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        js = r.json() or {}
        return js.get("access_token")
    except Exception:
        return None
def _snov_domain_emails(domain: str, logger=None) -> Dict[str, Any]:
    token = _snov_get_token(logger=logger)
    if not token:
        return {"ok": False, "attempted": False, "reason": "missing/invalid SNOV auth (SNOV_ACCESS_TOKEN or SNOV_CLIENT_ID/SNOV_CLIENT_SECRET)"}
    # Newer Snov flow: start -> poll result
    start_url = "https://api.snov.io/v2/domain-search/domain-emails/start"
    try:
        _log(logger, f"SNOV domain email URL: {start_url} (domain={domain})")
        r = requests.post(start_url, data={"access_token": token, "domain": domain}, timeout=25)
        if r.status_code not in (200, 202):
            return {"ok": False, "attempted": True, "reason": f"HTTP {r.status_code}: {r.text[:200]}"}
        js = r.json() or {}
        links = js.get("links") or {}
        result_url = links.get("result")
        # If no result link, treat as no emails
        if not result_url:
            return {"ok": True, "attempted": True, "source": "snov", "confidence": None, "generic": [], "person": [], "catchall": []}
        max_polls = int(os.getenv("SNOV_MAX_POLLS", "5"))                 # default 5
        poll_sleep = float(os.getenv("SNOV_POLL_SLEEP", "1.0"))           # default 1s
        total_cap = float(os.getenv("SNOV_TOTAL_CAP_SECONDS", "10"))      # default 10s
        start_ts = time.time()
        poll = 0
        emails = []
        while poll < max_polls and (time.time() - start_ts) < total_cap:
            poll += 1
            _log(logger, f"SNOV poll {poll}/{max_polls}: GET {result_url}")
            rr = requests.get(result_url, timeout=25)
            if rr.status_code != 200:
                # keep polling a bit, but don't explode
                time.sleep(poll_sleep)
                continue
            rjs = rr.json() or {}
            # Different possible shapes; try common ones
            # If task not ready, rjs may not contain emails yet.
            possible = []
            if isinstance(rjs.get("data"), list):
                possible = rjs["data"]
            elif isinstance(rjs.get("emails"), list):
                possible = rjs["emails"]
            # Extract emails from objects
            extracted = []
            for item in possible:
                if isinstance(item, dict):
                    e = _clean_email(item.get("email") or item.get("value"))
                    if e:
                        extracted.append(e)
                elif isinstance(item, str):
                    e = _clean_email(item)
                    if e:
                        extracted.append(e)
            if extracted:
                emails = extracted
                break
            time.sleep(poll_sleep)
        if not emails:
            return {"ok": True, "attempted": True, "source": "snov", "confidence": None, "generic": [], "person": [], "catchall": []}
        generic, person = _split_generic_person(emails)
        return {"ok": True, "attempted": True, "source": "snov", "confidence": "medium" if emails else None, "generic": generic, "person": person, "catchall": []}
    except Exception as e:
        return {"ok": False, "attempted": True, "reason": f"exception: {repr(e)}"}
# APOLLO (email at org-level is unreliable; keep fast-fail)
# NOTE: If you later add Apollo people search, do it here.
def _apollo_domain_email(domain: str, logger=None) -> Dict[str, Any]:
    api_key = os.getenv("APOLLO_API_KEY")
    if not api_key:
        return {"ok": False, "attempted": False, "reason": "missing APOLLO_API_KEY"}
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    url = "https://api.apollo.io/v1/organizations/enrich"
    try:
        r = requests.post(url, headers=headers, json={"domain": domain}, timeout=25)
        if r.status_code != 200:
            return {"ok": False, "attempted": True, "reason": f"HTTP {r.status_code}: {r.text[:200]}"}
        js = r.json() or {}
        org = js.get("organization") or {}
        # Org-level email is rarely populated. Keep this minimal.
        maybe = _clean_email(org.get("email"))
        emails = [maybe] if maybe else []
        generic, person = _split_generic_person(emails)
        return {"ok": True, "attempted": True, "source": "apollo", "confidence": "medium" if emails else None, "generic": generic, "person": person, "catchall": []}
    except Exception as e:
        return {"ok": False, "attempted": True, "reason": f"exception: {repr(e)}"}
# FULLENRICH (not domain-only; needs name + domain)
# We will only attempt if we have at least a plausible name.
# Requires:
#   FULLENRICH_API_KEY
#   FULLENRICH_ENRICHMENT_NAME (string like "trustpilot_enrichment")
def _fullenrich_contact_email(domain: str, company_name: Optional[str], logger=None) -> Dict[str, Any]:
    api_key = os.getenv("FULLENRICH_API_KEY")
    job_name = os.getenv("FULLENRICH_ENRICHMENT_NAME")
    if not api_key:
        return {"ok": False, "attempted": False, "reason": "missing FULLENRICH_API_KEY"}
    if not job_name:
        return {"ok": False, "attempted": False, "reason": "missing FULLENRICH_ENRICHMENT_NAME"}
    if not company_name:
        return {"ok": False, "attempted": False, "reason": "missing company_name (FullEnrich needs person name to enrich)"}
    # Try to infer a contact-ish name from company_name; if not possible, skip.
    # If company_name looks like a business (LLC, Inc, etc.), FullEnrich won't help without a person name anyway.
    # We'll only attempt if there are at least 2 tokens and NOT a pure biz suffix pattern.
    tokens = [t for t in re.split(r"\s+", company_name.strip()) if t]
    biz_markers = {"llc", "inc", "ltd", "co", "corp", "company", "pllc"}
    if len(tokens) < 2 or any(t.lower().strip(".,") in biz_markers for t in tokens):
        return {"ok": False, "attempted": False, "reason": "company_name not a usable person name for FullEnrich"}
    firstname = tokens[0]
    lastname = tokens[-1]
    url = "https://app.fullenrich.com/api/v1/contact/enrich/bulk"
    _log(logger, f"FULLENRICH URL: {url} (domain={domain})")
    payload = {
        "name": job_name,
        "datas": [
            {
                "firstname": firstname,
                "lastname": lastname,
                "domain": domain,
                "enrich_fields": ["contact.emails"],
            }
        ],
    }
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=25)
        if r.status_code != 200:
            return {"ok": False, "attempted": True, "reason": f"HTTP {r.status_code}: {r.text[:200]}"}
        js = r.json() or {}
        # FullEnrich response shapes vary; do best-effort extraction
        emails = []
        # common patterns: js["data"][0]["contact"]["emails"] or similar
        data = js.get("data") or js.get("datas") or js.get("results") or []
        if isinstance(data, list) and data:
            row = data[0] or {}
            contact = row.get("contact") or row.get("person") or row.get("result") or {}
            ems = contact.get("emails") or row.get("emails") or []
            if isinstance(ems, list):
                for e in ems:
                    if isinstance(e, str):
                        e2 = _clean_email(e)
                        if e2:
                            emails.append(e2)
                    elif isinstance(e, dict):
                        e2 = _clean_email(e.get("email") or e.get("value"))
                        if e2:
                            emails.append(e2)
        generic, person = _split_generic_person(emails)
        return {"ok": True, "attempted": True, "source": "fullenrich", "confidence": "medium" if emails else None, "generic": generic, "person": person, "catchall": []}
    except Exception as e:
        return {"ok": False, "attempted": True, "reason": f"exception: {repr(e)}"}
# ============================================================
# PHASE 4.6.1: DIRECTORY EMAIL PRESERVATION
# Prevent directory/aggregator emails from becoming primary_email
# ============================================================
DIRECTORY_EMAIL_DOMAINS = {
    "chamberofcommerce.com",
    "thebluebook.com",
    "buzzfile.com",
    "brokersnapshot.com",
    "zoominfo.com",
    "opencorporates.com",
    "yelp.com",
    "facebookmail.com",
}
def _email_domain(email: str) -> str:
    """Extract domain from email address"""
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()
def _append_secondary_email(row: dict, email: str, source: str = ""):
    """Append email to secondary_email field without duplicates"""
    if not email:
        return
    existing = row.get("secondary_email") or ""
    parts = [p.strip() for p in existing.split("|") if p.strip()] if existing else []
    token = email.strip()
    if token not in parts:
        parts.append(token)
    row["secondary_email"] = " | ".join(parts)
    # Track source in debug notes
    notes = row.get("debug_notes") or ""
    if source:
        note_token = f"|secondary_email:{source}"
        if note_token not in notes:
            notes += note_token
    row["debug_notes"] = notes
def assign_email(row: dict, email: str, source: str):
    """
    Smart email assignment that preserves directory emails as secondary.
    Rules:
    - If email belongs to directory/aggregator -> store as secondary_email
    - Else -> set as primary_email if empty; otherwise store as secondary_email
    Args:
        row: Business row dict
        email: Email address to assign
        source: Source of the email (e.g., 'google', 'yelp', 'canonical')
    """
    if not email:
        return
    dom = _email_domain(email)
    # Directory/aggregator emails always go to secondary
    if dom in DIRECTORY_EMAIL_DOMAINS:
        _append_secondary_email(row, email, source=f"{source}_directory")
        return
    # If no primary email yet, use this one
    if not row.get("primary_email"):
        row["primary_email"] = email
        row["primary_email_source"] = source
    else:
        # Already have primary, store as secondary
        _append_secondary_email(row, email, source=source)
# ============================================================
# PUBLIC ENTRYPOINT EXPECTED BY pipeline.py
# ============================================================
def run_email_waterfall(domain: Optional[str], company_name: Optional[str] = None, logger: Any = None, **kwargs) -> Dict[str, Any]:
    """
    Returns dict fields pipeline can merge:
      primary_email, primary_email_type, primary_email_source, primary_email_confidence,
      generic_emails_json, person_emails_json, catchall_emails_json
    STRATEGY:
    1) Hunter domain search (primary)
    2) Snov domain emails (fallback)
    3) Apollo domain email (fallback)
    4) FullEnrich contact email (needs company_name; fallback)
    Returns first success OR empty if all fail.
    """
    _log(logger, f"run_email_waterfall: domain={domain}, company_name={company_name}")
    if not domain:
        _log(logger, "  -> No domain provided, returning empty")
        return _pick_primary_email(None, None, [], [], [])
    tried = []
    # 1) Hunter
    _log(logger, "  -> Trying Hunter domain search...")
    hunter = _hunter_domain_search(domain, logger=logger)
    tried.append("hunter")
    if hunter.get("ok"):
        _log(logger, f"  -> Hunter SUCCESS: generic={hunter.get('generic')}, person={hunter.get('person')}")
        return _pick_primary_email(
            hunter.get("source"),
            hunter.get("confidence"),
            hunter.get("generic"),
            hunter.get("person"),
            hunter.get("catchall"),
        )
    else:
        _log(logger, f"  -> Hunter FAILED: {hunter.get('reason')}")
    # 2) Snov
    _log(logger, "  -> Trying Snov domain emails...")
    snov = _snov_domain_emails(domain, logger=logger)
    tried.append("snov")
    if snov.get("ok") and (snov.get("generic") or snov.get("person")):
        _log(logger, f"  -> Snov SUCCESS: generic={snov.get('generic')}, person={snov.get('person')}")
        return _pick_primary_email(
            snov.get("source"),
            snov.get("confidence"),
            snov.get("generic"),
            snov.get("person"),
            snov.get("catchall"),
        )
    else:
        _log(logger, f"  -> Snov FAILED or empty: {snov.get('reason')}")
    # 3) Apollo
    _log(logger, "  -> Trying Apollo domain email...")
    apollo = _apollo_domain_email(domain, logger=logger)
    tried.append("apollo")
    if apollo.get("ok") and (apollo.get("generic") or apollo.get("person")):
        _log(logger, f"  -> Apollo SUCCESS: generic={apollo.get('generic')}, person={apollo.get('person')}")
        return _pick_primary_email(
            apollo.get("source"),
            apollo.get("confidence"),
            apollo.get("generic"),
            apollo.get("person"),
            apollo.get("catchall"),
        )
    else:
        _log(logger, f"  -> Apollo FAILED or empty: {apollo.get('reason')}")
    # 4) FullEnrich (needs company_name)
    if company_name:
        _log(logger, "  -> Trying FullEnrich contact email...")
        fullenrich = _fullenrich_contact_email(domain, company_name, logger=logger)
        tried.append("fullenrich")
        if fullenrich.get("ok") and (fullenrich.get("generic") or fullenrich.get("person")):
            _log(logger, f"  -> FullEnrich SUCCESS: generic={fullenrich.get('generic')}, person={fullenrich.get('person')}")
            return _pick_primary_email(
                fullenrich.get("source"),
                fullenrich.get("confidence"),
                fullenrich.get("generic"),
                fullenrich.get("person"),
                fullenrich.get("catchall"),
            )
        else:
            _log(logger, f"  -> FullEnrich FAILED or empty: {fullenrich.get('reason')}")
    else:
        _log(logger, "  -> Skipping FullEnrich (no company_name)")
    # All failed
    _log(logger, f"  -> All email providers failed or empty. Tried: {', '.join(tried)}")
    return _pick_primary_email(None, None, [], [], [])
# ============================================================
# LEGACY COMPATIBILITY (for old code that calls enrich_emails_for_domain)
# ============================================================
def enrich_emails_for_domain(domain: str, logger: Any = None, **kwargs):
    """
    Legacy entrypoint for backward compatibility.
    Maps to run_email_waterfall.
    """
    return run_email_waterfall(domain, logger=logger, **kwargs)
