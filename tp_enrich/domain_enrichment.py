"""
Domain discovery logic - Section C
Uses FullEnrich and Apollo for domain enrichment
"""

import os
import re
import requests
import tldextract
from typing import Dict, Optional, Tuple, Literal
from urllib.parse import urlparse
from Levenshtein import ratio
from .logging_utils import setup_logger

logger = setup_logger(__name__)

DomainConfidence = Literal["none", "low", "medium", "high"]


def extract_domain_from_website(website: Optional[str]) -> Optional[str]:
    """
    Extract clean domain from website URL.

    Args:
        website: Website URL (can be with or without scheme)

    Returns:
        Clean domain (e.g., "example.com") or None
    """
    if not website:
        return None

    website = website.strip()
    if not website:
        return None

    # Ensure scheme for parsing
    if not website.startswith("http://") and not website.startswith("https://"):
        website = "https://" + website

    parsed = urlparse(website)

    host = parsed.netloc or parsed.path
    host = host.lower().strip()

    # strip credentials or port if any (just in case)
    if "@" in host:
        host = host.split("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]

    # strip leading www.
    if host.startswith("www."):
        host = host[4:]

    # strip any leftover slashes
    host = host.split("/")[0]

    return host or None


def extract_domain(url: str) -> Optional[str]:
    """
    Extract clean domain from URL

    Args:
        url: URL or domain string

    Returns:
        Clean domain (e.g., "example.com") or None
    """
    if not url:
        return None

    # Clean up URL
    url = str(url).strip()

    # Add scheme if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    # Extract domain
    extracted = tldextract.extract(url)

    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}"

    return None


def calculate_token_overlap(text1: str, text2: str) -> float:
    """
    Calculate token overlap ratio between two texts

    Args:
        text1: First text
        text2: Second text

    Returns:
        Overlap ratio (0.0 to 1.0)
    """
    if not text1 or not text2:
        return 0.0

    # Tokenize
    tokens1 = set(re.findall(r'\w+', text1.lower()))
    tokens2 = set(re.findall(r'\w+', text2.lower()))

    if not tokens1 or not tokens2:
        return 0.0

    # Calculate Jaccard similarity
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2

    return len(intersection) / len(union) if union else 0.0


def is_generic_domain(domain: str) -> bool:
    """
    Check if domain is generic/placeholder

    Args:
        domain: Domain to check

    Returns:
        True if generic, False otherwise
    """
    generic_patterns = [
        'example.com',
        'test.com',
        'placeholder',
        'tempuri',
        'localhost',
    ]

    domain_lower = domain.lower()
    return any(pattern in domain_lower for pattern in generic_patterns)


def enrich_from_input_website(website: Optional[str], company_name: str) -> Tuple[Optional[str], DomainConfidence]:
    """
    Section C.1: Extract domain from input website field if present

    Args:
        website: Website field from input
        company_name: Company search name

    Returns:
        Tuple of (domain, confidence)
    """
    if not website:
        return None, "none"

    domain = extract_domain(website)

    if not domain:
        return None, "none"

    # Check if domain tokens match company name
    overlap = calculate_token_overlap(domain, company_name)

    if overlap >= 0.5:
        logger.debug(f"Input website domain '{domain}' matches company '{company_name}' (overlap: {overlap:.2f})")
        return domain, "high"

    logger.debug(f"Input website domain '{domain}' weak match for company '{company_name}' (overlap: {overlap:.2f})")
    return domain, "medium"


def enrich_from_fullenrich(company_name: str, region: Optional[str], api_key: Optional[str]) -> Tuple[Optional[str], DomainConfidence]:
    """
    TEMPORARY STUB: FullEnrich API is asynchronous and not compatible with
    our current synchronous "upload → get CSV" pipeline.

    FullEnrich's actual API uses /api/v1/contact/enrich/bulk with webhooks/polling,
    not a synchronous "company search" endpoint like we were trying to use.

    For now, this function is intentionally DISABLED to avoid broken network calls
    to api.fullenrich.com. It returns (None, "none") so the enrichment waterfall
    continues to Apollo and other providers.

    TODO: Implement proper async FullEnrich integration in the future.

    Args:
        company_name: Company search name
        region: Region/location info
        api_key: FullEnrich API key

    Returns:
        Tuple of (None, "none") - always returns no domain
    """
    if not api_key:
        logger.info("FullEnrich disabled: API key not provided.")
        return None, "none"

    logger.info(
        "FullEnrich disabled in synchronous pipeline. Skipping FullEnrich call for '%s'.",
        company_name
    )
    return None, "none"


def enrich_from_apollo(company_name: str, region: Optional[str], api_key: Optional[str]) -> Tuple[Optional[str], DomainConfidence]:
    """
    Section C.3: Apollo company search

    Args:
        company_name: Company search name
        region: Region/location info
        api_key: Apollo API key

    Returns:
        Tuple of (domain, confidence)
    """
    logger.info(f"Calling Apollo for '{company_name}' (key_present={api_key is not None})")

    if not api_key:
        logger.warning("Apollo API key not provided, skipping")
        return None, "none"

    try:
        # Apollo organization search endpoint
        url = "https://api.apollo.io/v1/organizations/search"

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key
        }

        payload = {
            "q_organization_name": company_name,
            "per_page": 5
        }

        logger.info(f"  Apollo request payload: {payload}")

        response = requests.post(url, json=payload, headers=headers, timeout=10)

        logger.info(f"  Apollo HTTP status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            # Extract candidates
            organizations = data.get('organizations', [])
            logger.info(f"  Apollo returned {len(organizations)} organizations")

            for idx, org in enumerate(organizations):
                org_name = org.get('name', '')
                org_website = org.get('website_url', '') or org.get('primary_domain', '')

                logger.info(f"  Apollo candidate {idx+1}: '{org_name}', website='{org_website}'")

                # Check token overlap
                overlap = calculate_token_overlap(org_name, company_name)
                logger.info(f"  Apollo name overlap: {overlap:.2f}")

                if overlap >= 0.7 and org_website:
                    domain = extract_domain(org_website)

                    if domain and not is_generic_domain(domain):
                        confidence = "high" if overlap >= 0.85 else "medium"
                        logger.info(f"  ✓ Apollo found domain: '{domain}' (confidence: {confidence})")
                        return domain, confidence
                    else:
                        if not domain:
                            logger.warning(f"  ✗ Apollo: Could not extract domain from '{org_website}'")
                        else:
                            logger.warning(f"  ✗ Apollo: Domain '{domain}' is generic, skipping")
                else:
                    logger.warning(f"  ✗ Apollo: Overlap {overlap:.2f} < 0.7 or no website")

            logger.warning(f"  ✗ Apollo: No acceptable match found among {len(organizations)} candidates")
        else:
            logger.warning(f"  ✗ Apollo: Non-200 status: {response.status_code}")

        logger.info(f"  Apollo: No matching company found for '{company_name}'")

    except Exception as e:
        logger.error(f"  ✗ Apollo API error: {e}", exc_info=True)

    return None, "none"


def discover_domain(company_name: str, context: Dict, input_website: Optional[str] = None) -> Tuple[Optional[str], DomainConfidence]:
    """
    Section C: Complete domain discovery logic

    Tries in exact order:
    1) Website field from input
    2) FullEnrich company search
    3) Apollo company search

    Args:
        company_name: Company search name
        context: Enrichment context with location info
        input_website: Website from input CSV (if any)

    Returns:
        Tuple of (domain, confidence)
    """
    logger.info(f"========== DOMAIN ENRICHMENT START: '{company_name}' ==========")
    logger.info(f"  Context: region={context.get('state') or context.get('region')}, input_website={input_website}")

    region = context.get('state') or context.get('region')

    # Try input website first
    domain, confidence = enrich_from_input_website(input_website, company_name)
    if domain and confidence in ["high", "medium"]:
        logger.info(f"========== DOMAIN FOUND from input website: '{domain}' (confidence: {confidence}) ==========")
        return domain, confidence

    # Try FullEnrich
    fullenrich_key = os.getenv('FULLENRICH_API_KEY')
    domain, confidence = enrich_from_fullenrich(company_name, region, fullenrich_key)
    if domain and confidence == "high":
        logger.info(f"========== DOMAIN FOUND from FullEnrich: '{domain}' (confidence: {confidence}) ==========")
        return domain, confidence

    # Try Apollo
    apollo_key = os.getenv('APOLLO_API_KEY')
    domain, confidence = enrich_from_apollo(company_name, region, apollo_key)
    if domain and confidence in ["high", "medium"]:
        logger.info(f"========== DOMAIN FOUND from Apollo: '{domain}' (confidence: {confidence}) ==========")
        return domain, confidence

    # No domain found
    logger.warning(f"========== NO DOMAIN FOUND for '{company_name}' ==========")
    return None, "none"
