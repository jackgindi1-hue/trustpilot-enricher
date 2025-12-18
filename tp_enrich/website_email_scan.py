# ============================================================
# WEBSITE EMAIL MICRO-SCAN: Fast 2-page max email discovery
# ============================================================
import requests
from typing import Optional, List, Tuple
from urllib.parse import urlparse, urljoin

from tp_enrich.phase0_gating import pick_first_email

def _safe_get(url: str, timeout: int = 12) -> Tuple[int, str]:
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"}
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    return r.status_code, (r.text or "")

def _normalize_base_url(website: str) -> Optional[str]:
    if not website:
        return None
    w = website.strip()
    if not w:
        return None
    if "://" not in w:
        w = "http://" + w
    try:
        p = urlparse(w)
        if not p.netloc:
            return None
        # keep scheme+host
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return None

def micro_scan_for_email(website: Optional[str], logger=None) -> Optional[str]:
    """
    FAST + SAFE:
    - fetch homepage
    - if no email found, fetch at most 1 more page (contact/about) if link appears on homepage
    - stop on first valid email
    """
    base = _normalize_base_url(website or "")
    if not base:
        return None

    try:
        st, html = _safe_get(base)
        if logger:
            logger.info(f"WEBSITE_EMAIL_SCAN: home status={st} base={base}")
        if st == 200 and html:
            e = pick_first_email(html)
            if e:
                if logger: logger.info(f"WEBSITE_EMAIL_SCAN: found email on homepage: {e}")
                return e

            # find one likely contact link (only one extra fetch to stay fast)
            lowered = html.lower()
            candidates = []
            for path in ["/contact", "/contact-us", "/about", "/about-us"]:
                if path in lowered:
                    candidates.append(path)
            # also detect hrefs
            if 'href="/contact' in lowered:
                candidates.append("/contact")
            if 'href="/contact-us' in lowered:
                candidates.append("/contact-us")

            # dedupe
            seen = set()
            c2 = []
            for c in candidates:
                if c not in seen:
                    seen.add(c)
                    c2.append(c)
            if c2:
                url2 = urljoin(base + "/", c2[0].lstrip("/"))
                st2, html2 = _safe_get(url2)
                if logger:
                    logger.info(f"WEBSITE_EMAIL_SCAN: secondary status={st2} url={url2}")
                if st2 == 200 and html2:
                    e2 = pick_first_email(html2)
                    if e2:
                        if logger: logger.info(f"WEBSITE_EMAIL_SCAN: found email on secondary page: {e2}")
                        return e2
    except Exception as e:
        if logger:
            logger.warning(f"WEBSITE_EMAIL_SCAN: exception={repr(e)}")
        return None

    return None
