"""
Social enrichment logic - Section H
Website-derived social links + scrapers
"""

import re
import requests
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
from .logging_utils import setup_logger

logger = setup_logger(__name__)


def extract_social_links_from_website(website_url: str) -> Dict[str, List[str]]:
    """
    Extract Facebook and Instagram links from website HTML

    Args:
        website_url: Company website URL

    Returns:
        Dict with facebook_urls and instagram_urls lists
    """
    result = {
        'facebook_urls': [],
        'instagram_urls': []
    }

    if not website_url:
        return result

    try:
        # Add scheme if missing
        if not website_url.startswith(('http://', 'https://')):
            website_url = 'https://' + website_url

        logger.debug(f"Fetching website to extract social links: {website_url}")

        # Fetch website HTML
        response = requests.get(website_url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')

            # Find all links
            links = soup.find_all('a', href=True)

            for link in links:
                href = link['href']

                # Check for Facebook
                if 'facebook.com/' in href:
                    result['facebook_urls'].append(href)

                # Check for Instagram
                if 'instagram.com/' in href:
                    result['instagram_urls'].append(href)

            # Deduplicate
            result['facebook_urls'] = list(set(result['facebook_urls']))
            result['instagram_urls'] = list(set(result['instagram_urls']))

            logger.debug(f"Found {len(result['facebook_urls'])} Facebook and {len(result['instagram_urls'])} Instagram links")

    except Exception as e:
        logger.warning(f"Failed to fetch website {website_url}: {e}")

    return result


def scrape_facebook_profile(fb_url: str) -> Dict:
    """
    Scrape Facebook profile for contact info

    Section H: Use scrapers (e.g., Apify) to extract phone, email, address

    Args:
        fb_url: Facebook profile URL

    Returns:
        Dict with fb_phone, fb_email, fb_address
    """
    result = {
        'fb_phone': None,
        'fb_email': None,
        'fb_address': None
    }

    # This would require Apify actors or custom scrapers
    # Meta's official API doesn't provide this data
    # For now, we'll skip implementation but maintain the structure

    logger.debug(f"Facebook scraping not implemented (requires Apify actors)")

    return result


def scrape_instagram_profile(ig_url: str) -> Dict:
    """
    Scrape Instagram profile for contact info

    Section H: Use scrapers (e.g., Apify) to extract phone, email, address

    Args:
        ig_url: Instagram profile URL

    Returns:
        Dict with ig_phone, ig_email, ig_address
    """
    result = {
        'ig_phone': None,
        'ig_email': None,
        'ig_address': None
    }

    # This would require Apify actors or custom scrapers
    # For now, we'll skip implementation but maintain the structure

    logger.debug(f"Instagram scraping not implemented (requires Apify actors)")

    return result


def enrich_from_social(website_urls: List[str]) -> Dict:
    """
    Section H: Social enrichment pipeline

    1) Fetch company website HTML
    2) Extract Facebook/Instagram hrefs
    3) Use social scrapers to extract contact info
    4) Store as secondary contact data

    Args:
        website_urls: List of potential website URLs to check

    Returns:
        Dict with social contact data
    """
    result = {
        'fb_phone': None,
        'fb_email': None,
        'fb_address': None,
        'ig_phone': None,
        'ig_email': None,
        'ig_address': None,
        'facebook_urls': [],
        'instagram_urls': []
    }

    # Try each website URL
    for website in website_urls:
        if not website:
            continue

        # Extract social links
        social_links = extract_social_links_from_website(website)

        result['facebook_urls'].extend(social_links['facebook_urls'])
        result['instagram_urls'].extend(social_links['instagram_urls'])

    # Deduplicate
    result['facebook_urls'] = list(set(result['facebook_urls']))
    result['instagram_urls'] = list(set(result['instagram_urls']))

    # Scrape social profiles for contact info
    # (Implementation requires Apify actors or custom scrapers)

    if result['facebook_urls']:
        fb_data = scrape_facebook_profile(result['facebook_urls'][0])
        result.update(fb_data)

    if result['instagram_urls']:
        ig_data = scrape_instagram_profile(result['instagram_urls'][0])
        result.update(ig_data)

    return result
