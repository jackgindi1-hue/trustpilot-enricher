"""
Email enrichment logic - Section I
Hunter.io and Snov.io for domain-based email discovery
"""

import os
import re
import requests
from typing import Dict, List, Literal, Optional
from .logging_utils import setup_logger

logger = setup_logger(__name__)

EmailType = Literal["generic", "person", "catchall"]


def _categorize_and_select_primary(all_emails: List[Dict]) -> Dict:
    """
    Helper to categorize emails and select primary email

    Args:
        all_emails: List of email dicts with 'email', 'type', 'source', 'confidence'

    Returns:
        Dict with categorized emails and primary email selection
    """
    result = {
        'generic_emails': [],
        'person_emails': [],
        'catchall_emails': [],
        'primary_email': None,
        'primary_email_type': None,
        'primary_email_source': None,
        'primary_email_confidence': 'none'
    }

    # Categorize emails
    for email_info in all_emails:
        email_type = email_info['type']

        if email_type == 'generic':
            result['generic_emails'].append(email_info)
        elif email_type == 'person':
            result['person_emails'].append(email_info)
        elif email_type == 'catchall':
            result['catchall_emails'].append(email_info)

    # Choose primary email by priority:
    # 1) Highest-confidence person email
    # 2) If none: highest-confidence generic email
    # 3) Never use catchall as primary unless no other option

    primary = None

    # Try person emails first
    if result['person_emails']:
        person_emails_sorted = sorted(result['person_emails'], key=lambda x: x['confidence'], reverse=True)
        primary = person_emails_sorted[0]

    # Fall back to generic emails
    elif result['generic_emails']:
        generic_emails_sorted = sorted(result['generic_emails'], key=lambda x: x['confidence'], reverse=True)
        primary = generic_emails_sorted[0]

    # Last resort: catchall
    elif result['catchall_emails']:
        catchall_emails_sorted = sorted(result['catchall_emails'], key=lambda x: x['confidence'], reverse=True)
        primary = catchall_emails_sorted[0]

    if primary:
        result['primary_email'] = primary['email']
        result['primary_email_type'] = primary['type']
        result['primary_email_source'] = primary['source']

        # Map confidence score to level
        confidence_score = primary['confidence']
        if confidence_score >= 80:
            result['primary_email_confidence'] = 'high'
        elif confidence_score >= 50:
            result['primary_email_confidence'] = 'medium'
        else:
            result['primary_email_confidence'] = 'low'

    return result


def classify_email(email: str) -> EmailType:
    """
    Classify email as generic, person, or catchall

    Section I rules:
    - GENERIC: info@, support@, help@, sales@, billing@, admin@, office@, hello@
    - PERSON: looks like a human email (john.smith@domain, jsmith@domain)
    - CATCHALL: noreply@, no-reply@

    Args:
        email: Email address

    Returns:
        Email type classification
    """
    if not email:
        return "generic"

    local_part = email.split('@')[0].lower() if '@' in email else email.lower()

    # Catchall patterns
    catchall_patterns = ['noreply', 'no-reply', 'donotreply', 'do-not-reply']
    if any(pattern in local_part for pattern in catchall_patterns):
        return "catchall"

    # Generic patterns
    generic_patterns = [
        'info', 'support', 'help', 'sales', 'billing',
        'admin', 'office', 'hello', 'contact', 'service',
        'customer', 'team', 'general', 'inquiry'
    ]
    if any(pattern == local_part for pattern in generic_patterns):
        return "generic"

    # Person patterns - looks like a name
    # Contains dots, numbers, or name-like structure
    if '.' in local_part or re.match(r'^[a-z]+[0-9]*$', local_part):
        # Check if it's a name-like pattern (not generic)
        if local_part not in generic_patterns:
            return "person"

    # Default to generic
    return "generic"


def enrich_from_hunter(domain: str, api_key: Optional[str]) -> Optional[Dict]:
    """
    Hunter.io domain search - returns full email enrichment dict

    Args:
        domain: Company domain
        api_key: Hunter API key

    Returns:
        Dict with categorized emails and primary email, or None if nothing found
    """
    logger.info(f"  Calling Hunter.io for domain='{domain}' (key_present={api_key is not None})")

    if not api_key:
        logger.warning("  Hunter.io API key not provided, skipping")
        return None

    if not domain:
        logger.warning("  Hunter.io: Domain is empty, skipping")
        return None

    all_emails = []

    try:
        url = "https://api.hunter.io/v2/domain-search"

        params = {
            'domain': domain,
            'api_key': api_key
        }

        response = requests.get(url, params=params, timeout=10)

        logger.info(f"  Hunter.io HTTP status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            if data.get('data'):
                hunter_emails = data['data'].get('emails', [])

                for item in hunter_emails:
                    email = item.get('value')
                    confidence = item.get('confidence', 0)

                    if email:
                        email_type = classify_email(email)

                        all_emails.append({
                            'email': email,
                            'type': email_type,
                            'source': 'hunter',
                            'confidence': confidence
                        })

                logger.info(f"  ✓ Hunter.io found {len(all_emails)} emails for '{domain}'")
                # Log email types breakdown
                types_count = {}
                for e in all_emails:
                    t = e['type']
                    types_count[t] = types_count.get(t, 0) + 1
                logger.info(f"    Email types: {types_count}")
            else:
                logger.warning(f"  ✗ Hunter.io: No data returned for '{domain}'")

        elif response.status_code == 401:
            logger.error("  ✗ Hunter.io: Invalid API key")
        elif response.status_code == 429:
            logger.error("  ✗ Hunter.io: Rate limit exceeded")
        else:
            logger.warning(f"  ✗ Hunter.io: Non-200 status: {response.status_code}")

    except Exception as e:
        logger.error(f"  ✗ Hunter.io API error: {e}", exc_info=True)

    if not all_emails:
        return None

    # Categorize and select primary email
    return _categorize_and_select_primary(all_emails)


def enrich_from_snov(domain: str, api_key: Optional[str]) -> Optional[Dict]:
    """
    Snov.io domain search - returns full email enrichment dict

    Args:
        domain: Company domain
        api_key: Snov.io API key (or user_id:api_key format)

    Returns:
        Dict with categorized emails and primary email, or None if nothing found
    """
    logger.info(f"  Calling Snov.io for domain='{domain}' (key_present={api_key is not None})")

    if not api_key:
        logger.warning("  Snov.io API key not provided, skipping")
        return None

    if not domain:
        logger.warning("  Snov.io: Domain is empty, skipping")
        return None

    all_emails = []

    try:
        url = "https://api.snov.io/v1/get-domain-emails-with-info"

        # Snov.io uses user_id and api_key
        # Expected format: "user_id:api_key" or just api_key
        if ':' in api_key:
            user_id, key = api_key.split(':', 1)
        else:
            logger.error("  ✗ Snov.io API key format should be 'user_id:api_key'")
            return None

        params = {
            'domain': domain,
            'user_id': user_id,
            'api_key': key
        }

        response = requests.post(url, json=params, timeout=10)

        logger.info(f"  Snov.io HTTP status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            if data.get('success') and data.get('emails'):
                snov_emails = data['emails']

                for item in snov_emails:
                    email = item.get('email')

                    if email:
                        email_type = classify_email(email)

                        # Snov doesn't provide confidence, default to medium (50)
                        all_emails.append({
                            'email': email,
                            'type': email_type,
                            'source': 'snov',
                            'confidence': 50
                        })

                logger.info(f"  ✓ Snov.io found {len(all_emails)} emails for '{domain}'")
                # Log email types breakdown
                types_count = {}
                for e in all_emails:
                    t = e['type']
                    types_count[t] = types_count.get(t, 0) + 1
                logger.info(f"    Email types: {types_count}")
            else:
                logger.warning(f"  ✗ Snov.io: No success or no emails for '{domain}'")
        else:
            logger.warning(f"  ✗ Snov.io: Non-200 status: {response.status_code}")

    except Exception as e:
        logger.error(f"  ✗ Snov.io API error: {e}", exc_info=True)

    if not all_emails:
        return None

    # Categorize and select primary email
    return _categorize_and_select_primary(all_emails)


def enrich_emails_from_apollo_domain(domain: str, api_key: Optional[str]) -> Optional[Dict]:
    """
    Apollo.io DOMAIN-BASED email search (last resort in waterfall)

    Args:
        domain: Company domain
        api_key: Apollo API key

    Returns:
        Dict with categorized emails and primary email, or None if nothing found
    """
    logger.info(f"  Calling Apollo.io for domain='{domain}' (key_present={api_key is not None})")

    if not api_key:
        logger.warning("  Apollo.io API key not provided, skipping")
        return None

    if not domain:
        logger.warning("  Apollo.io: Domain is empty, skipping")
        return None

    all_emails = []

    try:
        # Apollo People Search by domain
        url = "https://api.apollo.io/v1/mixed_people/search"

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key
        }

        payload = {
            "organization_domains": [domain],
            "per_page": 10
        }

        logger.info(f"  Apollo request payload: {payload}")

        response = requests.post(url, json=payload, headers=headers, timeout=10)

        logger.info(f"  Apollo HTTP status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            people = data.get('people', [])
            logger.info(f"  Apollo returned {len(people)} people for domain '{domain}'")

            for person in people:
                email = person.get('email')
                if email:
                    email_type = classify_email(email)

                    all_emails.append({
                        'email': email,
                        'type': email_type,
                        'source': 'apollo',
                        'confidence': 60  # Medium confidence for Apollo emails
                    })

            if all_emails:
                logger.info(f"  ✓ Apollo found {len(all_emails)} emails for '{domain}'")
                # Log email types breakdown
                types_count = {}
                for e in all_emails:
                    t = e['type']
                    types_count[t] = types_count.get(t, 0) + 1
                logger.info(f"    Email types: {types_count}")
            else:
                logger.warning(f"  ✗ Apollo: No emails found for domain '{domain}'")

        elif response.status_code == 422:
            logger.warning(f"  ✗ Apollo: 422 Unprocessable Entity for domain '{domain}'")
        else:
            logger.warning(f"  ✗ Apollo: Non-200 status: {response.status_code}")

    except Exception as e:
        logger.error(f"  ✗ Apollo API error: {e}", exc_info=True)

    if not all_emails:
        return None

    # Categorize and select primary email
    return _categorize_and_select_primary(all_emails)


def enrich_emails_with_waterfall(
    domain: Optional[str],
    hunter_api_key: Optional[str],
    snov_api_key: Optional[str],
    apollo_api_key: Optional[str],
) -> Dict:
    """
    Email enrichment waterfall: Hunter → Snov → Apollo (ALL domain-based)

    Args:
        domain: Company domain
        hunter_api_key: Hunter.io API key
        snov_api_key: Snov.io API key
        apollo_api_key: Apollo.io API key

    Returns:
        Dict with categorized emails and primary email
    """
    logger.info(f"========== EMAIL WATERFALL START: domain='{domain}' ==========")

    # Base empty structure
    result = {
        "primary_email": None,
        "primary_email_type": None,
        "primary_email_source": None,
        "primary_email_confidence": "none",
        "generic_emails": [],
        "person_emails": [],
        "catchall_emails": [],
    }

    if not domain or str(domain).strip() == "":
        # Keep existing behavior: skip when no domain
        logger.warning(
            "========== SKIPPING email enrichment: domain is missing or empty =========="
        )
        return result

    # 1) Hunter first (if key present)
    hunter_data = None
    if hunter_api_key:
        hunter_data = enrich_from_hunter(domain, hunter_api_key)

    if hunter_data and hunter_data.get("primary_email"):
        logger.info(f"  ✓ Email waterfall: using Hunter result for '{domain}'")
        return hunter_data

    # 2) Snov next (if key present)
    snov_data = None
    if snov_api_key:
        snov_data = enrich_from_snov(domain, snov_api_key)

    if snov_data and snov_data.get("primary_email"):
        logger.info(f"  ✓ Email waterfall: using Snov result for '{domain}'")
        return snov_data

    # 3) Apollo LAST (if key present)
    apollo_email_data = None
    if apollo_api_key:
        apollo_email_data = enrich_emails_from_apollo_domain(domain, apollo_api_key)

    if apollo_email_data and apollo_email_data.get("primary_email"):
        logger.info(f"  ✓ Email waterfall: using Apollo result for '{domain}'")
        return apollo_email_data

    # If nobody finds anything, return the empty base structure
    logger.warning(f"  ✗ Email waterfall: no emails found for '{domain}'")
    return result


def enrich_emails(domain: Optional[str]) -> Dict:
    """
    Section I: Email enrichment from domain

    Args:
        domain: Company domain

    Returns:
        Dict with categorized emails and primary email selection
    """
    logger.info(f"========== EMAIL ENRICHMENT START: domain='{domain}' ==========")

    result = {
        'generic_emails': [],
        'person_emails': [],
        'catchall_emails': [],
        'primary_email': None,
        'primary_email_type': None,
        'primary_email_source': None,
        'primary_email_confidence': 'none'
    }

    if not domain or str(domain).strip() == '':
        logger.warning("========== SKIPPING email enrichment: domain is missing or empty ==========")
        return result

    all_emails = []

    # Hunter.io
    hunter_key = os.getenv('HUNTER_API_KEY')
    logger.info(f"  Hunter.io key present: {hunter_key is not None}")
    hunter_emails = enrich_from_hunter(domain, hunter_key)
    all_emails.extend(hunter_emails)

    # Snov.io
    snov_key = os.getenv('SNOV_API_KEY')
    logger.info(f"  Snov.io key present: {snov_key is not None}")
    snov_emails = enrich_from_snov(domain, snov_key)
    all_emails.extend(snov_emails)

    logger.info(f"  Total emails found: {len(all_emails)}")

    # Categorize emails
    for email_info in all_emails:
        email_type = email_info['type']

        if email_type == 'generic':
            result['generic_emails'].append(email_info)
        elif email_type == 'person':
            result['person_emails'].append(email_info)
        elif email_type == 'catchall':
            result['catchall_emails'].append(email_info)

    # Choose primary email by priority:
    # 1) Highest-confidence person email
    # 2) If none: highest-confidence generic email
    # 3) Never use catchall as primary unless no other option

    primary = None

    # Try person emails first
    if result['person_emails']:
        person_emails_sorted = sorted(result['person_emails'], key=lambda x: x['confidence'], reverse=True)
        primary = person_emails_sorted[0]

    # Fall back to generic emails
    elif result['generic_emails']:
        generic_emails_sorted = sorted(result['generic_emails'], key=lambda x: x['confidence'], reverse=True)
        primary = generic_emails_sorted[0]

    # Last resort: catchall
    elif result['catchall_emails']:
        catchall_emails_sorted = sorted(result['catchall_emails'], key=lambda x: x['confidence'], reverse=True)
        primary = catchall_emails_sorted[0]

    if primary:
        result['primary_email'] = primary['email']
        result['primary_email_type'] = primary['type']
        result['primary_email_source'] = primary['source']

        # Map confidence score to level
        confidence_score = primary['confidence']
        if confidence_score >= 80:
            result['primary_email_confidence'] = 'high'
        elif confidence_score >= 50:
            result['primary_email_confidence'] = 'medium'
        else:
            result['primary_email_confidence'] = 'low'

    return result
