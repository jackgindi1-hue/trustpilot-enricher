# ============================================================
# PHASE 0 GATING: Domain Canonicalization + Phase2 Skip Logic
# ============================================================
import re
import time
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse, urljoin

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

def _has_str(x) -> bool:
    return bool(x is not None and str(x).strip() != "")

def domain_from_url(u: Optional[str]) -> Optional[str]:
    try:
        if not u:
            return None
        u = str(u).strip()
        if not u:
            return None
        if "://" not in u:
            u = "http://" + u
        host = (urlparse(u).netloc or "").lower()
        host = host.split("@")[-1].split(":")[0].strip(".")
        if host.startswith("www."):
            host = host[4:]
        if not host or "." not in host:
            return None
        return host
    except Exception:
        return None

def is_high_enough_for_skip(base_out: Dict[str, Any]) -> bool:
    """
    Phase2 should SKIP only when we already have:
      - phone
      - domain
      - email (the key!)
    and they're not obviously junk.
    """
    phone = base_out.get("primary_phone") or base_out.get("primary_phone_display")
    domain = base_out.get("company_domain") or base_out.get("domain")
    email = base_out.get("primary_email")
    # confidence fields vary across your code; treat presence of email as the main requirement
    return _has_str(phone) and _has_str(domain) and _has_str(email)

def should_run_phase2(base_out: Dict[str, Any], google_payload: Dict[str, Any]) -> bool:
    """
    Phase 2 = discovery/fallback only.
    We run it ONLY when it can help recover missing anchors.
    NOTE: Missing state should NOT trigger Phase2 globally; it only affects OC.
    """
    if is_high_enough_for_skip(base_out):
        return False

    phone = base_out.get("primary_phone") or base_out.get("primary_phone_display")
    domain = base_out.get("company_domain") or base_out.get("domain")
    if not _has_str(domain):
        # try derive from known website field (if present)
        domain = domain_from_url(base_out.get("business_website") or "")

    # Run Phase2 only if we are missing key anchor data
    if not _has_str(phone):
        return True
    if not _has_str(domain):
        return True

    # If email missing, Phase2 is generally NOT an email source,
    # BUT it can provide BBB "Visit Website" for domain recovery if domain missing.
    # If we already have domain and phone, don't run Phase2 just because email is missing.
    return False

def should_run_opencorporates(google_payload: Dict[str, Any], base_out: Dict[str, Any]) -> bool:
    state = (google_payload or {}).get("state_region") or (google_payload or {}).get("state") or base_out.get("business_state_region")
    return _has_str(state)

def pick_first_email(text: str) -> Optional[str]:
    if not text:
        return None
    emails = [e.strip().lower() for e in _EMAIL_RE.findall(text)]
    # basic cleanup / de-dupe
    seen = set()
    out = []
    for e in emails:
        if e.startswith("mailto:"):
            e = e.replace("mailto:", "").strip()
        if "@" in e and "." in e.split("@")[-1] and e not in seen:
            seen.add(e)
            out.append(e)
    return out[0] if out else None
