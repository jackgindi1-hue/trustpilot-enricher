"""
Name classification logic - Section A
Classifies displayName as "business" | "person" | "other"
"""

import re
from typing import Literal
from .logging_utils import setup_logger

logger = setup_logger(__name__)

ClassificationType = Literal["business", "person", "other"]

# Section A.2 - "other" patterns
OTHER_PATTERNS = {
    "consumer", "customer", "anonymous", "business account", "anon",
    "customer service", "consumer.displayname"
}

# Section A.3 - Business legal suffixes
BUSINESS_LEGAL_SUFFIXES = {
    "llc", "inc", "corp", "corporation", "co", "ltd", "llp", "pllc",
    "pc", "p.c", "incorporated"
}

# Section A.4 - Strong industry/business words
BUSINESS_KEYWORDS = {
    "auto", "boutique", "truck", "trucking", "transport", "logistics",
    "express", "freight", "construction", "roofing", "plumbing", "electric",
    "electrical", "hvac", "pools", "pool", "janitorial", "cleaning", "detail",
    "detailing", "cafe", "café", "restaurant", "eatery", "grill", "bar", "boba",
    "spa", "studio", "studios", "photography", "media", "therapy", "hypnosis",
    "clinic", "homecare", "care", "services", "service", "funding", "capital",
    "equity", "lending", "finance", "financial", "insurance", "realty",
    "real estate", "properties", "cycles", "tractor", "works", "wholesale",
    "distribution", "distributor", "supply", "supplies"
}

# Section A.5 - Business structure patterns
BUSINESS_STRUCTURE_PATTERNS = [
    r'\b\w+\s*&\s*\w+',  # X & Y
    r'\b\w+\s*&\s*sons\b',  # X & Sons
]

# Section A.5 - Business endings
BUSINESS_ENDINGS = {
    "café", "cafe", "grill", "spa", "trucking", "custom cycles", "tractor works"
}

# Section A.6 - Organizational terms
ORGANIZATIONAL_TERMS = {
    "academy", "operations lead", "equity", "contracting services",
    "senior ins services", "children academy"
}

# Section A.8 - Nickname patterns
NICKNAME_PATTERNS = [
    r'^uncle\s+\w+',
    r'\bbig\s+\w+',
    r'\bjunior\s+\w+',
    r'\bspeedy\b'
]


def normalize_and_tokenize(name: str) -> tuple[str, list[str]]:
    """
    Normalize name and return normalized version + tokens

    Args:
        name: Raw display name

    Returns:
        Tuple of (normalized_name, tokens)
    """
    # Normalize
    normalized = name.strip().lower()

    # Tokenize - split on spaces and punctuation but keep some structure
    tokens = re.findall(r'\b\w+\b', normalized)

    return normalized, tokens


def is_location_pattern(normalized: str, tokens: list[str]) -> bool:
    """
    Check if name looks like a location (e.g., "Atlanta, Georgia")
    """
    # Simple heuristic: contains comma and looks like "City, State"
    if ',' in normalized and len(tokens) == 2:
        return True
    return False


def has_legal_suffix(tokens: list[str]) -> bool:
    """Section A.3 - Check for business legal suffixes"""
    for token in tokens:
        # Remove punctuation for comparison
        clean_token = re.sub(r'[^\w]', '', token).lower()
        if clean_token in BUSINESS_LEGAL_SUFFIXES:
            return True
    return False


def has_business_keywords(tokens: list[str], normalized: str) -> bool:
    """Section A.4 - Check for strong industry/business words"""
    for token in tokens:
        if token.lower() in BUSINESS_KEYWORDS:
            return True

    # Check multi-word keywords in normalized string
    for keyword in BUSINESS_KEYWORDS:
        if ' ' in keyword and keyword in normalized:
            return True

    return False


def matches_business_structure(normalized: str) -> bool:
    """Section A.5 - Check for business structure patterns"""
    for pattern in BUSINESS_STRUCTURE_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True

    # Check business endings
    for ending in BUSINESS_ENDINGS:
        if normalized.endswith(ending):
            return True

    return False


def has_organizational_terms(normalized: str, tokens: list[str]) -> bool:
    """Section A.6 - Check for organizational terms"""
    for term in ORGANIZATIONAL_TERMS:
        if term in normalized:
            return True

        # Check individual tokens too
        for token in tokens:
            if token in ORGANIZATIONAL_TERMS:
                return True

    return False


def is_human_name_pattern(tokens: list[str]) -> bool:
    """
    Section A.7 - Check if matches human name pattern
    1-3 name-like tokens, maybe suffix
    """
    if len(tokens) < 1 or len(tokens) > 4:
        return False

    # Common suffixes
    suffixes = {'jr', 'sr', 'ii', 'iii', 'iv', 'md', 'phd', 'esq'}

    # Filter out suffixes
    name_tokens = [t for t in tokens if t.lower() not in suffixes]

    # Should have 1-3 actual name tokens
    if 1 <= len(name_tokens) <= 3:
        # Each token should be mostly alphabetic and capitalized-looking
        return True

    return False


def is_nickname_pattern(normalized: str) -> bool:
    """Section A.8 - Check for nickname-like patterns"""
    for pattern in NICKNAME_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True

    # Additional nickname heuristics
    if normalized.count(' ') >= 2:  # Multiple words might be nickname
        # "Big Dame Big Dame", "Junior White Boy Speedy"
        words = normalized.split()
        if any(w in {'big', 'little', 'junior', 'uncle', 'aunt'} for w in words):
            return True

    return False


def is_acronym_pattern(tokens: list[str]) -> bool:
    """
    Section A.9 - Check if looks like acronym
    Single token, 2-4 uppercase letters
    """
    if len(tokens) == 1:
        token = tokens[0]
        if len(token) >= 2 and len(token) <= 4 and token.isupper():
            return True

    return False


def classify_name(display_name: str) -> ClassificationType:
    """
    Classify displayName according to Section A logic

    Args:
        display_name: Raw display name from Trustpilot

    Returns:
        Classification: "business" | "person" | "other"
    """
    if not display_name or pd.isna(display_name):
        return "other"

    # Step 1: Normalize and tokenize
    normalized, tokens = normalize_and_tokenize(str(display_name))

    # Step 2: Check for "other" patterns
    if normalized in OTHER_PATTERNS:
        return "other"

    if is_location_pattern(normalized, tokens):
        return "other"

    # Step 3: Check for business legal suffixes
    if has_legal_suffix(tokens):
        return "business"

    # Step 4: Check for business keywords
    if has_business_keywords(tokens, normalized):
        return "business"

    # Step 5: Check for business structure patterns
    if matches_business_structure(normalized):
        return "business"

    # Step 6: Check for organizational terms
    if has_organizational_terms(normalized, tokens):
        return "business"

    # Step 7: Check for human name pattern
    if is_human_name_pattern(tokens):
        return "person"

    # Step 8: Check for nickname pattern
    if is_nickname_pattern(normalized):
        return "person"

    # Step 9: Check for acronym pattern
    if is_acronym_pattern(tokens):
        # Only classify as business if business keyword exists
        if has_business_keywords(tokens, normalized):
            return "business"
        return "other"

    # Step 10: Ambiguous cases - only classify as business if ≥80% confidence
    # For now, default ambiguous to person if it looks name-like, else other
    if len(tokens) >= 2 and len(tokens) <= 3:
        return "person"

    return "other"


# Import pandas for isna check
import pandas as pd
