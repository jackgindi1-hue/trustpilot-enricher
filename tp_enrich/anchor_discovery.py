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
import requests
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup


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
        # Use DuckDuckGo HTML search (no API key needed, no captcha)
        search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.get(search_url, headers=headers, timeout=10)
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
    Extract US state code from text.
    Looks for 2-letter state codes (CA, NY, TX, etc.)
    """
    if not text:
        return None

    # Common US state codes
    states = [
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
        'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
        'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY'
    ]

    # Look for state codes in common contexts
    for state in states:
        # Match state code with word boundaries
        if re.search(rf'\b{state}\b', text, re.I):
            return state

    return None


def scrape_page_for_anchors(url: str, timeout: int = 5) -> Dict[str, Any]:
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
        anchors["domain"] = extract_domain_from_url(response.url)

        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')

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

def phase46_anchor_discovery(
    business_name: str,
    vertical: Optional[str] = None,
    max_urls: int = 3,
    logger=None
) -> Dict[str, Any]:
    """
    Discover anchors (domain, phone, address, state) from web sources.

    Args:
        business_name: Business name to search
        vertical: Optional vertical (e.g., "trucking", "plumbing")
        max_urls: Max URLs to scrape (default 3)
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
    if logger:
        logger.info(f"   -> ANCHOR DISCOVERY: Searching for {business_name}")

    # Build search query
    query = build_search_query(business_name, vertical)

    # Get top URLs from search
    urls = google_search_urls(query, max_results=max_urls)

    if not urls:
        if logger:
            logger.warning(f"   -> ANCHOR DISCOVERY: No URLs found for {business_name}")
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

    if not evidence_list:
        if logger:
            logger.warning(f"   -> ANCHOR DISCOVERY: No anchors found in scraped pages")
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

    return {
        "discovered_domain": best.get("domain"),
        "discovered_phone": best.get("phone"),
        "discovered_state_region": best.get("state"),
        "discovered_address": best.get("address"),
        "discovered_email": best.get("email"),
        "discovered_evidence_url": best.get("evidence_url"),
        "discovered_evidence_source": "serp_scrape",
        "discovery_evidence_json": json.dumps(evidence_list, indent=2),
    }
