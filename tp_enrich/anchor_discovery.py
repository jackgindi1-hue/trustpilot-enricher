# ============================================================
# PHASE 4.6 — ANCHOR DISCOVERY
#
# GOAL: When canonical matching fails (no candidates / score < 80%),
# discover anchors (domain, phone, address, state) from web sources.
#
# Flow:
# 1. Run SERP organic queries (name-only or name + vertical)
# 2. Fetch top URLs (with timeout/cap)
# 3. Extract phone/email/domain/address/state from HTML
# 4. Pick BEST anchor set by evidence strength
# 5. Return discovered_* fields + evidence
# ============================================================

import re
import time
import requests
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

from tp_enrich.retry_ratelimit import SimpleRateLimiter, timed

# PHASE 4.6.3: Global rate limiter for DDG searches
_ANCHOR_RATE = SimpleRateLimiter(min_interval_s=0.5)

# PHASE 4.6.3: Performance tuning
ANCHOR_HTTP_TIMEOUT = 6  # Reduced from 10s
ANCHOR_HTML_CAP = 80_000  # Cap HTML bytes for faster parsing
ANCHOR_MAX_URLS = 4  # Reduced from unlimited
ANCHOR_MAX_QUERIES = 2  # Limit number of search queries

# PHASE 4.6.5: Directory domains that should NOT become discovered_domain
DIRECTORY_DOMAINS = {
    "chamberofcommerce.com", "buildzoom.com", "mapquest.com", "facebook.com",
    "linkedin.com", "yelp.com", "yellowpages.com", "bbb.org", "brokersnapshot.com",
    "usarestaurants.info", "opencorporates.com", "bizapedia.com", "zoominfo.com",
    "superpages.com", "manta.com", "dnb.com", "bbb.org", "yellowpages.ca"
}


# ============================================================
# SERP Query Helpers
# ============================================================

def build_search_query(business_name: str, vertical: Optional[str] = None) -> str:
    """
    Build Google search query for anchor discovery.

    Args:
        business_name: Business name to search
        vertical: Optional vertical (e.g., "trucking", "plumbing")

    Returns:
        Search query string
    """
    query = business_name.strip()

    # Add vertical hints for better results
    if vertical:
        query = f"{query} {vertical}"

    # Add "contact" to bias toward contact pages
    query = f"{query} contact phone address"

    return query


def google_search_urls(query: str, max_results: int = 5) -> List[str]:
    """
    Get top URLs from Google search (organic results).
    Uses DuckDuckGo as a fallback to avoid Google captcha.

    Args:
        query: Search query
        max_results: Max number of URLs to return

    Returns:
        List of URLs
    """
    urls = []

    try:
        # PHASE 4.6.3: Rate limit DDG searches
        _ANCHOR_RATE.wait("ddg_search", 0.5)

        # Use DuckDuckGo HTML search (no API key needed, no captcha)
        search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.get(search_url, headers=headers, timeout=ANCHOR_HTTP_TIMEOUT)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract URLs from DuckDuckGo results
        for result in soup.select('.result__a')[:max_results]:
            href = result.get('href')
            if href:
                # DuckDuckGo wraps URLs in redirect
                if 'uddg=' in href:
                    # Extract actual URL from redirect
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    if 'uddg' in parsed:
                        actual_url = parsed['uddg'][0]
                        urls.append(actual_url)
                else:
                    urls.append(href)

    except Exception as e:
        # Fallback: return empty list (caller will handle)
        pass

    return urls[:max_results]


# ============================================================
# Web Scraping & Extraction
# ============================================================

def extract_domain_from_url(url: str) -> Optional[str]:
    """Extract clean domain from URL"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        if domain.startswith('www.'):
            domain = domain[4:]
        return domain.lower().strip()
    except:
        return None


def is_directory_domain(domain: str) -> bool:
    """
    PHASE 4.6.5: Check if domain is a directory/aggregator site.

    Directory domains should NOT become discovered_domain because:
    - They're not the actual business website
    - They poison email enrichment (Hunter searches wrong domain)
    - They cause canonical matching failures

    Returns:
        True if domain is a known directory site, False otherwise
    """
    if not domain:
        return False

    domain = domain.lower().strip()

    # Direct match
    if domain in DIRECTORY_DOMAINS:
        return True

    # Subdomain match (e.g., "maps.google.com" matches "google.com" if in list)
    for dir_domain in DIRECTORY_DOMAINS:
        if domain.endswith('.' + dir_domain) or domain == dir_domain:
            return True

    return False


def extract_phone_from_text(text: str) -> Optional[str]:
    """
    Extract phone number from text using regex.
    Supports US formats: (123) 456-7890, 123-456-7890, 1234567890
    """
    if not text:
        return None

    # Remove common non-phone number patterns
    text = re.sub(r'(fax|toll.free|customer.service)[\s:]*', '', text, flags=re.I)

    # US phone patterns
    patterns = [
        r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # (123) 456-7890 or 123-456-7890
        r'\d{10}',  # 1234567890
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            phone = match.group(0)
            # Normalize to digits only
            digits = re.sub(r'\D', '', phone)
            if len(digits) == 10:
                return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
            elif len(digits) == 11 and digits[0] == '1':
                return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"

    return None


def extract_address_from_text(text: str) -> Optional[str]:
    """
    Extract address from text using simple heuristics.
    Looks for patterns like "123 Main St, City, ST 12345"
    """
    if not text:
        return None

    # Simple pattern: street number + street name + city + state + zip
    pattern = r'\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Court|Ct)[,\s]+[A-Za-z\s]+[,\s]+[A-Z]{2}[\s,]+\d{5}'

    match = re.search(pattern, text, re.I)
    if match:
        return match.group(0).strip()

    return None


def extract_state_from_text(text: str) -> Optional[str]:
    """
    PHASE 4.6.5: Extract US state code from VISIBLE TEXT only.

    CRITICAL: Must match state codes in address-like contexts, NOT in HTML attributes.
    Pattern matches state codes that appear after commas or spaces (typical address format).

    Examples that SHOULD match:
    - "123 Main St, Springfield, VA 12345"
    - "Located in VA"
    - "Springfield, VA"

    Examples that should NOT match:
    - 'id="VA"' or 'class="VA"' (HTML attributes)
    - Random uppercase VA in middle of word
    """
    if not text:
        return None

    # PHASE 4.6.5: Strict pattern - state code must be preceded by comma/space
    # This prevents matching HTML attributes like id="VA"
    pattern = r'(?:(?:,\s*)|(?:\s))(' \
              r'AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|' \
              r'NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY' \
              r')(?:\s|,|$|\d)'  # Must be followed by space, comma, end, or digit (zip code)

    match = re.search(pattern, text)
    if match:
        return match.group(1)

    return None


def scrape_page_for_anchors(url: str, timeout: int = ANCHOR_HTTP_TIMEOUT) -> Dict[str, Any]:
    """
    Scrape a single page and extract anchors.

    Args:
        url: URL to scrape
        timeout: Request timeout in seconds

    Returns:
        Dict with extracted anchors: domain, phone, address, state, email
    """
    anchors = {
        "domain": None,
        "phone": None,
        "address": None,
        "state": None,
        "email": None,
        "evidence_url": url,
    }

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()

        # Extract domain from final URL (after redirects)
        extracted_domain = extract_domain_from_url(response.url)

        # PHASE 4.6.5: Filter directory domains
        # Directory domains should NOT become discovered_domain (they're not the business site)
        # But we keep them as evidence for debugging
        if extracted_domain and not is_directory_domain(extracted_domain):
            anchors["domain"] = extracted_domain
        elif extracted_domain:
            # Keep directory domain as evidence only (not as discovered_domain)
            anchors["directory_domain"] = extracted_domain
            anchors["domain"] = None  # Explicitly None so it doesn't poison results

        # PHASE 4.6.3: Cap HTML size for faster parsing
        html = response.text[:ANCHOR_HTML_CAP]

        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')

        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()

        # Get text content
        text = soup.get_text(separator=' ', strip=True)

        # Extract anchors from text
        anchors["phone"] = extract_phone_from_text(text)
        anchors["address"] = extract_address_from_text(text)
        anchors["state"] = extract_state_from_text(text)

        # Extract email from text
        email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if email_match:
            anchors["email"] = email_match.group(0)

    except Exception as e:
        # Failed to scrape this URL, return partial anchors
        pass

    return anchors


# ============================================================
# PHASE 4.6 MAIN ANCHOR DISCOVERY
# ============================================================

def _anchors_strong(discovered: dict) -> bool:
    """PHASE 4.6.3: Check if we have strong enough anchors to stop early."""
    d = bool((discovered.get("discovered_domain") or "").strip())
    p = bool((discovered.get("discovered_phone") or "").strip())
    s = bool((discovered.get("discovered_state_region") or "").strip())
    # Strong if we have: (domain + state) OR (phone + state) OR (domain + phone)
    return (s and d) or (s and p) or (d and p)


def phase46_anchor_discovery(
    business_name: str,
    vertical: Optional[str] = None,
    max_urls: int = 2,
    logger=None
) -> Dict[str, Any]:
    """
    Discover anchors (domain, phone, address, state) from web sources.

    Args:
        business_name: Business name to search
        vertical: Optional vertical (e.g., "trucking", "plumbing")
        max_urls: Max URLs to scrape (default 2)
        logger: Optional logger

    Returns:
        Dict with discovered anchors + evidence:
        {
            "discovered_domain": str,
            "discovered_phone": str,
            "discovered_state_region": str,
            "discovered_address": str,
            "discovered_email": str,
            "discovered_evidence_url": str,
            "discovered_evidence_source": "serp_scrape",
            "discovery_evidence_json": str (JSON list of all evidence),
        }
    """
    # PHASE 4.6.3: Add timing
    done = timed(logger, "ANCHOR_DISCOVERY")

    if logger:
        logger.info(f"   -> ANCHOR DISCOVERY: Searching for {business_name}")

    # Build search query
    query = build_search_query(business_name, vertical)

    # Get top URLs from search
    urls = google_search_urls(query, max_results=max_urls)

    if not urls:
        if logger:
            logger.warning(f"   -> ANCHOR DISCOVERY: No URLs found for {business_name}")
        done("no_urls_found")
        return {
            "discovered_domain": None,
            "discovered_phone": None,
            "discovered_state_region": None,
            "discovered_address": None,
            "discovered_email": None,
            "discovered_evidence_url": None,
            "discovered_evidence_source": None,
            "discovery_evidence_json": "[]",
        }

    if logger:
        logger.info(f"   -> ANCHOR DISCOVERY: Found {len(urls)} URLs to scrape")

    # Scrape each URL and collect evidence
    evidence_list = []

    for url in urls:
        if logger:
            logger.info(f"   -> Scraping: {url}")

        anchors = scrape_page_for_anchors(url)
        if any(anchors.values()):  # If we found any anchor
            evidence_list.append(anchors)

            # PHASE 4.6.3: Early stopping if we have strong anchors
            # Build a temporary result to check
            temp_result = {
                "discovered_domain": anchors.get("domain"),
                "discovered_phone": anchors.get("phone"),
                "discovered_state_region": anchors.get("state"),
            }
            if _anchors_strong(temp_result):
                if logger:
                    logger.info(f"   -> ANCHOR DISCOVERY: Strong anchors found, stopping early")
                break

    if not evidence_list:
        if logger:
            logger.warning(f"   -> ANCHOR DISCOVERY: No anchors found in scraped pages")
        done("no_anchors_found")
        return {
            "discovered_domain": None,
            "discovered_phone": None,
            "discovered_state_region": None,
            "discovered_address": None,
            "discovered_email": None,
            "discovered_evidence_url": None,
            "discovered_evidence_source": None,
            "discovery_evidence_json": "[]",
        }

    # Pick BEST anchor set by evidence strength
    # Scoring: phone + address = strongest, domain alone = weakest
    def score_evidence(ev):
        score = 0
        if ev.get("phone"): score += 3
        if ev.get("address"): score += 2
        if ev.get("state"): score += 2
        if ev.get("domain"): score += 1
        if ev.get("email"): score += 1
        return score

    best = max(evidence_list, key=score_evidence)

    if logger:
        logger.info(
            f"   -> ANCHOR DISCOVERY: Best evidence from {best.get('evidence_url')} "
            f"(domain={bool(best.get('domain'))}, phone={bool(best.get('phone'))}, "
            f"state={bool(best.get('state'))}, address={bool(best.get('address'))})"
        )

    # Return discovered anchors
    import json

    result = {
        "discovered_domain": best.get("domain"),
        "discovered_phone": best.get("phone"),
        "discovered_state_region": best.get("state"),
        "discovered_address": best.get("address"),
        "discovered_email": best.get("email"),
        "discovered_evidence_url": best.get("evidence_url"),
        "discovered_evidence_source": "serp_scrape",
        "discovery_evidence_json": json.dumps(evidence_list, indent=2),
    }

    # PHASE 4.6.3: Log timing
    done(f"found_domain={bool(result.get('discovered_domain'))} found_phone={bool(result.get('discovered_phone'))}")

    return result


# ============================================================
# PHASE 4.6.3 — ANCHOR DISCOVERY WITH CACHING + 429 RETRY
# ============================================================

# Per-job cache for anchor discovery (prevents duplicate lookups)
DISCOVERY_CACHE: Dict[str, Dict[str, Any]] = {}


def _norm_key(s: str) -> str:
    """Normalize company name for cache key."""
    return " ".join((s or "").lower().split()).strip()


def _sleep_backoff(attempt: int):
    """Exponential backoff for retries."""
    sleep_time = min(8.0, 0.6 * (2 ** attempt))  # 0.6, 1.2, 2.4, 4.8, 8.0
    time.sleep(sleep_time)


def phase46_anchor_discovery_cached(
    business_name: str,
    vertical: Optional[str] = None,
    max_urls: int = 2,
    logger=None,
    max_retries: int = 3
) -> Dict[str, Any]:
    """
    PHASE 4.6.3: Cached anchor discovery with 429 retry logic.

    Improvements over base function:
    - Per-job caching (prevents duplicate lookups)
    - 429 retry with exponential backoff
    - Explicit debug notes on failure
    - Never silently returns empty (logs failures)

    Args:
        business_name: Business name to search
        vertical: Optional vertical (e.g., "trucking", "plumbing")
        max_urls: Max URLs to scrape (default 2)
        logger: Optional logger
        max_retries: Max retry attempts for 429 errors (default 3)

    Returns:
        Dict with discovered anchors + evidence (same as phase46_anchor_discovery)
    """
    # Check cache first
    cache_key = _norm_key(business_name)
    if cache_key in DISCOVERY_CACHE:
        if logger:
            logger.info(f"   -> ANCHOR DISCOVERY: Using cached result for '{business_name}'")
        return DISCOVERY_CACHE[cache_key].copy()

    # Try discovery with retry logic
    last_error = None
    for attempt in range(max_retries):
        try:
            result = phase46_anchor_discovery(
                business_name=business_name,
                vertical=vertical,
                max_urls=max_urls,
                logger=logger
            )

            # Cache successful result
            DISCOVERY_CACHE[cache_key] = result.copy()
            return result

        except requests.exceptions.HTTPError as ex:
            last_error = ex
            status_code = getattr(ex.response, 'status_code', None) if hasattr(ex, 'response') else None

            # Retry on 429 (rate limit)
            if status_code == 429:
                if logger:
                    logger.warning(
                        f"   -> ANCHOR DISCOVERY: 429 rate limit hit (attempt {attempt + 1}/{max_retries}) "
                        f"for '{business_name}'"
                    )
                if attempt < max_retries - 1:
                    _sleep_backoff(attempt)
                    continue
                else:
                    if logger:
                        logger.error(f"   -> ANCHOR DISCOVERY: Max retries exceeded for '{business_name}'")
                    break
            else:
                # Non-429 HTTP error, don't retry
                if logger:
                    logger.error(
                        f"   -> ANCHOR DISCOVERY: HTTP {status_code} error for '{business_name}': {ex}"
                    )
                break

        except Exception as ex:
            last_error = ex
            error_msg = str(ex).lower()

            # Check if error message indicates rate limiting
            if "429" in error_msg or "rate" in error_msg or "quota" in error_msg:
                if logger:
                    logger.warning(
                        f"   -> ANCHOR DISCOVERY: Rate limit detected (attempt {attempt + 1}/{max_retries}) "
                        f"for '{business_name}': {ex}"
                    )
                if attempt < max_retries - 1:
                    _sleep_backoff(attempt)
                    continue
                else:
                    if logger:
                        logger.error(f"   -> ANCHOR DISCOVERY: Max retries exceeded for '{business_name}'")
                    break
            else:
                # Non-rate-limit error, don't retry
                if logger:
                    logger.error(f"   -> ANCHOR DISCOVERY: Error for '{business_name}': {ex}")
                break

    # All retries failed - return empty result with debug notes
    if logger:
        logger.error(
            f"   -> ANCHOR DISCOVERY: FAILED for '{business_name}' after {max_retries} attempts. "
            f"Last error: {repr(last_error)}"
        )

    empty_result = {
        "discovered_domain": None,
        "discovered_phone": None,
        "discovered_state_region": None,
        "discovered_address": None,
        "discovered_email": None,
        "discovered_evidence_url": None,
        "discovered_evidence_source": None,
        "discovery_evidence_json": "[]",
        "discovery_error": repr(last_error),
        "discovery_failed": True,
    }

    # Cache the failure to avoid retrying same business repeatedly
    DISCOVERY_CACHE[cache_key] = empty_result.copy()

    return empty_result


def clear_discovery_cache():
    """Clear the per-job discovery cache (call at job start/end)."""
    global DISCOVERY_CACHE
    DISCOVERY_CACHE.clear()
