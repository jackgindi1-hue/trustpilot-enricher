"""
Merge enrichment results and apply priority rules
Sections J, K, L: Phone priority, Email priority, Overall confidence
"""

import json
import re
import phonenumbers
from typing import Dict, List, Optional, Literal
from .logging_utils import setup_logger

logger = setup_logger(__name__)

Confidence = Literal["none", "low", "medium", "high", "failed"]


def normalize_phone(phone: str) -> Optional[str]:
    """
    Normalize phone number to E.164 format

    Args:
        phone: Raw phone number

    Returns:
        Normalized phone or None
    """
    if not phone:
        return None

    try:
        # Try parsing as US number first
        parsed = phonenumbers.parse(phone, "US")

        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except:
        pass

    # Try without region
    try:
        parsed = phonenumbers.parse(phone, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except:
        pass

    return None


def format_phone_display(phone: str) -> str:
    """
    Format phone for display

    Args:
        phone: Phone number

    Returns:
        Formatted display number
    """
    if not phone:
        return ""

    try:
        parsed = phonenumbers.parse(phone, "US")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
    except:
        return phone


def aggregate_phones(enrichment_data: Dict) -> List[Dict]:
    """
    Section J: Aggregate all phones from enrichment sources

    Returns list of phone dicts with:
    - number_normalized
    - display
    - source
    - confidence
    - type (main/local/toll_free)

    Args:
        enrichment_data: All enrichment data

    Returns:
        List of phone dicts
    """
    all_phones = []

    # Google Places phones (Priority #1 for SMB phone numbers)
    maps_phone_main = enrichment_data.get('maps_phone_main')
    maps_confidence = enrichment_data.get('maps_match_confidence', 'none')

    if maps_phone_main:
        normalized = normalize_phone(maps_phone_main)
        if normalized:
            all_phones.append({
                'number_normalized': normalized,
                'display': format_phone_display(maps_phone_main),
                'source': 'google_places',
                'confidence': maps_confidence,
                'type': 'main'
            })

    # Yelp phones
    yelp_phone = enrichment_data.get('yelp_phone_display') or enrichment_data.get('yelp_phone_e164')
    yelp_confidence = enrichment_data.get('yelp_match_confidence', 'none')

    if yelp_phone:
        normalized = normalize_phone(yelp_phone)
        if normalized:
            all_phones.append({
                'number_normalized': normalized,
                'display': format_phone_display(yelp_phone),
                'source': 'yelp',
                'confidence': yelp_confidence,
                'type': 'main'
            })

    # YellowPages/BBB phones
    yp_phone = enrichment_data.get('yp_phone')
    bbb_phone = enrichment_data.get('bbb_phone')

    if yp_phone:
        normalized = normalize_phone(yp_phone)
        if normalized:
            all_phones.append({
                'number_normalized': normalized,
                'display': format_phone_display(yp_phone),
                'source': 'yellowpages',
                'confidence': enrichment_data.get('yp_match_confidence', 'medium'),
                'type': 'main'
            })

    if bbb_phone:
        normalized = normalize_phone(bbb_phone)
        if normalized:
            all_phones.append({
                'number_normalized': normalized,
                'display': format_phone_display(bbb_phone),
                'source': 'bbb',
                'confidence': enrichment_data.get('bbb_match_confidence', 'medium'),
                'type': 'main'
            })

    # Apollo/FullEnrich phones (if any in enrichment_data)
    # These would be added if domain_enrichment returned phone data

    # Social phones
    fb_phone = enrichment_data.get('fb_phone')
    ig_phone = enrichment_data.get('ig_phone')

    if fb_phone:
        normalized = normalize_phone(fb_phone)
        if normalized:
            all_phones.append({
                'number_normalized': normalized,
                'display': format_phone_display(fb_phone),
                'source': 'facebook',
                'confidence': 'low',
                'type': 'main'
            })

    if ig_phone:
        normalized = normalize_phone(ig_phone)
        if normalized:
            all_phones.append({
                'number_normalized': normalized,
                'display': format_phone_display(ig_phone),
                'source': 'instagram',
                'confidence': 'low',
                'type': 'main'
            })

    # Deduplicate phones by normalized number
    unique_phones = {}
    for phone in all_phones:
        norm = phone['number_normalized']
        if norm not in unique_phones:
            unique_phones[norm] = phone
        else:
            # Keep the one with higher confidence
            existing_conf = unique_phones[norm]['confidence']
            new_conf = phone['confidence']

            conf_order = {'high': 3, 'medium': 2, 'low': 1, 'none': 0}

            if conf_order.get(new_conf, 0) > conf_order.get(existing_conf, 0):
                unique_phones[norm] = phone

    return list(unique_phones.values())


def select_primary_phone(all_phones: List[Dict]) -> Dict:
    """
    Section J: Choose primary phone by exact priority

    Priority:
    1) Google Places (TOP PRIORITY - best for SMB phone numbers)
    2) Yelp (with high confidence)
    3) YellowPages/BBB
    4) Apollo/FullEnrich company phone
    5) Website/social phones

    Args:
        all_phones: List of all phone dicts

    Returns:
        Dict with primary phone info
    """
    result = {
        'primary_phone': None,
        'primary_phone_display': None,
        'primary_phone_source': None,
        'primary_phone_confidence': 'none'
    }

    if not all_phones:
        return result

    # Define source priority order
    # Google Places is #1 priority for phone numbers
    source_priority = {
        'google_places': 1,
        'yelp': 2,
        'yellowpages': 3,
        'bbb': 3,
        'apollo': 4,
        'fullenrich': 4,
        'facebook': 5,
        'instagram': 5,
        'website': 5
    }

    confidence_order = {'high': 3, 'medium': 2, 'low': 1, 'none': 0}

    # Sort phones by priority and confidence
    sorted_phones = sorted(all_phones, key=lambda x: (
        -source_priority.get(x['source'], 99),
        -confidence_order.get(x['confidence'], 0)
    ))

    # Choose primary
    if sorted_phones:
        primary = sorted_phones[0]

        result['primary_phone'] = primary['number_normalized']
        result['primary_phone_display'] = primary['display']
        result['primary_phone_source'] = primary['source']
        result['primary_phone_confidence'] = primary['confidence']

    return result


def select_primary_email(email_data: Dict) -> Dict:
    """
    Section K: Choose primary email by priority

    Priority:
    1) Verified person email (Apollo/Hunter/Snov)
    2) Verified generic email
    3) Scraped website/social emails
    4) Catchall only if nothing else

    Args:
        email_data: Email enrichment data

    Returns:
        Dict with primary email (already selected in email_enrichment)
    """
    # Email priority is already handled in email_enrichment.py
    # Just pass through
    return {
        'primary_email': email_data.get('primary_email'),
        'primary_email_type': email_data.get('primary_email_type'),
        'primary_email_source': email_data.get('primary_email_source'),
        'primary_email_confidence': email_data.get('primary_email_confidence', 'none')
    }


def calculate_overall_confidence(enrichment_data: Dict) -> Tuple[Confidence, str]:
    """
    Section L: Calculate overall lead confidence

    high if:
    - domain_confidence high AND
    - primary_phone_confidence ≥ medium AND
    - primary_email_confidence ≥ medium

    medium if partial coverage
    low if only minimal enrichment
    failed if nothing useful

    Args:
        enrichment_data: All enrichment data

    Returns:
        Tuple of (confidence_level, status_note)
    """
    domain_conf = enrichment_data.get('domain_confidence', 'none')
    phone_conf = enrichment_data.get('primary_phone_confidence', 'none')
    email_conf = enrichment_data.get('primary_email_confidence', 'none')

    conf_order = {'high': 3, 'medium': 2, 'low': 1, 'none': 0}

    # High confidence
    if (domain_conf == 'high' and
        conf_order.get(phone_conf, 0) >= 2 and
        conf_order.get(email_conf, 0) >= 2):
        return 'high', 'Complete enrichment with high confidence'

    # Medium confidence
    if (conf_order.get(domain_conf, 0) >= 2 or
        conf_order.get(phone_conf, 0) >= 2 or
        conf_order.get(email_conf, 0) >= 2):
        return 'medium', 'Partial enrichment with medium confidence'

    # Low confidence
    if (conf_order.get(domain_conf, 0) >= 1 or
        conf_order.get(phone_conf, 0) >= 1 or
        conf_order.get(email_conf, 0) >= 1):
        return 'low', 'Minimal enrichment with low confidence'

    # Failed
    return 'failed', 'No useful enrichment data found'


def merge_enrichment_results(enrichment_data: Dict) -> Dict:
    """
    Merge all enrichment results and apply priority rules

    Sections J, K, L implementation

    Args:
        enrichment_data: All enrichment data

    Returns:
        Final merged enrichment result
    """
    result = {}

    # Aggregate all phones
    all_phones = aggregate_phones(enrichment_data)

    # Select primary phone
    primary_phone = select_primary_phone(all_phones)
    result.update(primary_phone)

    # Store all phones as JSON
    result['all_phones_json'] = json.dumps(all_phones) if all_phones else None

    # Select primary email (already done in email enrichment)
    primary_email = select_primary_email(enrichment_data)
    result.update(primary_email)

    # Store categorized emails as JSON
    result['generic_emails_json'] = json.dumps(enrichment_data.get('generic_emails', [])) if enrichment_data.get('generic_emails') else None
    result['person_emails_json'] = json.dumps(enrichment_data.get('person_emails', [])) if enrichment_data.get('person_emails') else None
    result['catchall_emails_json'] = json.dumps(enrichment_data.get('catchall_emails', [])) if enrichment_data.get('catchall_emails') else None

    # Domain info
    result['company_domain'] = enrichment_data.get('company_domain')
    result['domain_confidence'] = enrichment_data.get('domain_confidence', 'none')

    # Address info (choose best available)
    address_sources = [
        enrichment_data.get('maps_address'),
        enrichment_data.get('yelp_address'),
        enrichment_data.get('yp_address'),
        enrichment_data.get('bbb_address'),
        enrichment_data.get('oc_registered_address')
    ]

    for addr in address_sources:
        if addr:
            result['business_address'] = addr
            break

    # OpenCorporates data
    result['oc_company_name'] = enrichment_data.get('oc_company_name')
    result['oc_jurisdiction'] = enrichment_data.get('oc_jurisdiction')
    result['oc_company_number'] = enrichment_data.get('oc_company_number')
    result['oc_incorporation_date'] = enrichment_data.get('oc_incorporation_date')
    result['oc_match_confidence'] = enrichment_data.get('oc_match_confidence', 'none')

    # Calculate overall confidence
    overall_confidence, status = calculate_overall_confidence(result)
    result['overall_lead_confidence'] = overall_confidence
    result['enrichment_status'] = 'success' if overall_confidence != 'failed' else 'failed'
    result['enrichment_notes'] = status

    return result


from typing import Tuple
