#!/usr/bin/env python3
"""
Trustpilot Review Enrichment Tool
CLI Entry Point

Uses tp_enrich.pipeline module for core enrichment logic.
This module can be reused by both CLI and API server.
"""

import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

from tp_enrich.logging_utils import setup_logger
from tp_enrich.pipeline import run_pipeline

logger = setup_logger(__name__)


def main():
    """Main CLI entry point"""
    # Load environment variables
    load_dotenv()

    # Parse arguments
    parser = argparse.ArgumentParser(
        description='Enrich Trustpilot reviews CSV with business contact information'
    )
    parser.add_argument(
        'input_file',
        help='Path to input Trustpilot CSV file'
    )
    parser.add_argument(
        '-o', '--output',
        default='enriched_output.csv',
        help='Path to output enriched CSV file (default: enriched_output.csv)'
    )
    parser.add_argument(
        '-c', '--cache',
        default='enrichment_cache.json',
        help='Path to enrichment cache file (default: enrichment_cache.json)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        import logging
        logger.setLevel(logging.DEBUG)

    # Validate input file exists
    if not Path(args.input_file).exists():
        logger.error(f"Input file not found: {args.input_file}")
        sys.exit(1)

    # Check for API keys
    logger.info("Checking API keys...")
    api_keys = {
        'GOOGLE_PLACES_API_KEY': os.getenv('GOOGLE_PLACES_API_KEY'),
        'YELP_API_KEY': os.getenv('YELP_API_KEY'),
        'OPENCORPORATES_API_KEY': os.getenv('OPENCORPORATES_API_KEY'),
        'HUNTER_API_KEY': os.getenv('HUNTER_API_KEY'),
        'SNOV_API_KEY': os.getenv('SNOV_API_KEY'),
        'APOLLO_API_KEY': os.getenv('APOLLO_API_KEY'),
        'FULLENRICH_API_KEY': os.getenv('FULLENRICH_API_KEY'),
    }

    for key_name, key_value in api_keys.items():
        if key_value:
            logger.info(f"  ✓ {key_name} provided")
        else:
            logger.warning(f"  ✗ {key_name} not provided - will skip this integration")

    # Run pipeline
    try:
        run_pipeline(args.input_file, args.output, args.cache)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
