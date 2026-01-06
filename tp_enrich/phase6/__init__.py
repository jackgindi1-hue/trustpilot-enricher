"""
PHASE 6 — Trainable Classification Override System

This module provides:
- Postgres-backed override store for name -> label mappings
- Token-based model for classification
- FastAPI routes for managing overrides and training

MODES (set via PHASE6_MODE env var):
- off (default): No behavior changes
- shadow: Logs only, no output changes
- enforce: Applies overrides/model prefill BEFORE pipeline
"""
