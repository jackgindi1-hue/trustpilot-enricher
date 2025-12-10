"""
Email enrichment logic - Section I
Hunter.io and Snov.io for domain-based email discovery
"""

import os
import re
import requests
from typing import Dict, List, Literal
from .logging_utils import setup_logger

logger = setup_logger(__name__)

EmailType = Literal["generic", "person", "catchall"]


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


def enrich_from_hunter(domain: str, api_key: Optional[str]) -> List[Dict]:
    """
    Hunter.io domain search

    Args:
        domain: Company domain
        api_key: Hunter API key

    Returns:
        List of email dicts with type and confidence
    """
    emails = []

    if not api_key:
        logger.debug("Hunter.io API key not provided, skipping")
        return emails

    if not domain:
        return emails

    try:
        url = "https://api.hunter.io/v2/domain-search"

        params = {
            'domain': domain,
            'api_key': api_key
        }

        logger.debug(f"Querying Hunter.io for domain: {domain}")

        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()

            if data.get('data'):
                hunter_emails = data['data'].get('emails', [])

                for item in hunter_emails:
                    email = item.get('value')
                    confidence = item.get('confidence', 0)

                    if email:
                        email_type = classify_email(email)

                        emails.append({
                            'email': email,
                            'type': email_type,
                            'source': 'hunter',
                            'confidence': confidence
                        })

                logger.debug(f"Hunter.io found {len(emails)} emails for {domain}")

        elif response.status_code == 401:
            logger.warning("Hunter.io: Invalid API key")
        elif response.status_code == 429:
            logger.warning("Hunter.io: Rate limit exceeded")

    except Exception as e:
        logger.warning(f"Hunter.io API error: {e}")

    return emails


def enrich_from_snov(domain: str, api_key: Optional[str]) -> List[Dict]:
    """
    Snov.io domain search

    Args:
        domain: Company domain
        api_key: Snov.io API key (or user_id:api_key format)

    Returns:
        List of email dicts with type and confidence
    """
    emails = []

    if not api_key:
        logger.debug("Snov.io API key not provided, skipping")
        return emails

    if not domain:
        return emails

    try:
        url = "https://api.snov.io/v1/get-domain-emails-with-info"

        # Snov.io uses user_id and api_key
        # Expected format: "user_id:api_key" or just api_key
        if ':' in api_key:
            user_id, key = api_key.split(':', 1)
        else:
            logger.warning("Snov.io API key format should be 'user_id:api_key'")
            return emails

        params = {
            'domain': domain,
            'user_id': user_id,
            'api_key': key
        }

        logger.debug(f"Querying Snov.io for domain: {domain}")

        response = requests.post(url, json=params, timeout=10)

        if response.status_code == 200:
            data = response.json()

            if data.get('success') and data.get('emails'):
                snov_emails = data['emails']

                for item in snov_emails:
                    email = item.get('email')

                    if email:
                        email_type = classify_email(email)

                        # Snov doesn't provide confidence, default to medium (50)
                        emails.append({
                            'email': email,
                            'type': email_type,
                            'source': 'snov',
                            'confidence': 50
                        })

                logger.debug(f"Snov.io found {len(emails)} emails for {domain}")

    except Exception as e:
        logger.warning(f"Snov.io API error: {e}")

    return emails


def enrich_emails(domain: Optional[str]) -> Dict:
    """
    Section I: Email enrichment from domain

    Args:
        domain: Company domain

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

    if not domain:
        logger.debug("No domain provided for email enrichment")
        return result

    all_emails = []

    # Hunter.io
    hunter_key = os.getenv('HUNTER_API_KEY')
    hunter_emails = enrich_from_hunter(domain, hunter_key)
    all_emails.extend(hunter_emails)

    # Snov.io
    snov_key = os.getenv('SNOV_API_KEY')
    snov_emails = enrich_from_snov(domain, snov_key)
    all_emails.extend(snov_emails)

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
