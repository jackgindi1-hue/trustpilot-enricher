"""
Enrichment cache management
Saves enrichment results so repeat runs don't re-enrich
"""

import json
import os
from typing import Dict, Optional
from pathlib import Path
from .logging_utils import setup_logger

logger = setup_logger(__name__)


class EnrichmentCache:
    """
    Cache for enrichment results, keyed by company_normalized_key
    """

    def __init__(self, cache_file: str = "enrichment_cache.json"):
        """
        Initialize cache

        Args:
            cache_file: Path to cache file
        """
        self.cache_file = Path(cache_file)
        self.cache: Dict[str, Dict] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        """Load cache from file if exists"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    self.cache = json.load(f)
                logger.info(f"Loaded {len(self.cache)} entries from cache: {self.cache_file}")
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}. Starting with empty cache.")
                self.cache = {}
        else:
            logger.info(f"No existing cache found at {self.cache_file}")
            self.cache = {}

    def save_cache(self) -> None:
        """Save cache to file"""
        try:
            # Create directory if needed
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2, default=str)
            logger.info(f"Saved {len(self.cache)} entries to cache: {self.cache_file}")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

    def get(self, normalized_key: str) -> Optional[Dict]:
        """
        Get enrichment result from cache

        Args:
            normalized_key: Company normalized key

        Returns:
            Cached enrichment result or None
        """
        return self.cache.get(normalized_key)

    def set(self, normalized_key: str, enrichment_result: Dict) -> None:
        """
        Store enrichment result in cache

        Args:
            normalized_key: Company normalized key
            enrichment_result: Enrichment result to cache
        """
        self.cache[normalized_key] = enrichment_result

    def has(self, normalized_key: str) -> bool:
        """
        Check if key exists in cache

        Args:
            normalized_key: Company normalized key

        Returns:
            True if cached, False otherwise
        """
        return normalized_key in self.cache
