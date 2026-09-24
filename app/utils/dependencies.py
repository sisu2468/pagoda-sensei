"""
FastAPI dependency injection helpers.
Loads operator database (live from Supabase, falling back to mock JSON)
and wires up the MatchingEngine singleton.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.matching_engine import MatchingEngine

logger = logging.getLogger(__name__)

OPERATORS_PATH = Path(__file__).parent.parent / "data" / "operators.json"


def _load_mock_operators() -> list[dict[str, Any]]:
    """Load the placeholder operator database from JSON."""
    if not OPERATORS_PATH.exists():
        raise FileNotFoundError(f"Operator database not found at {OPERATORS_PATH}")
    with OPERATORS_PATH.open("r", encoding="utf-8") as f:
        operators = json.load(f)
    logger.info("Loaded %d MOCK operators from %s", len(operators), OPERATORS_PATH)
    return operators


@lru_cache(maxsize=1)
def _load_operators() -> list[dict[str, Any]]:
    """
    Load operators from Supabase (real Pagoda tours) if credentials are
    configured. Falls back to mock data if Supabase is unreachable or
    not configured, so the service never goes down over this.
    """
    use_live = os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_ANON_KEY")

    if use_live:
        try:
            from app.services.supabase_service import fetch_live_operators
            operators = fetch_live_operators()
            if operators:
                logger.info("Using %d LIVE operators from Supabase", len(operators))
                return operators
            logger.warning("Supabase returned 0 operators — falling back to mock data")
        except Exception as exc:
            logger.warning("Supabase fetch failed (%s) — falling back to mock data", exc)

    return _load_mock_operators()


@lru_cache(maxsize=1)
def get_matching_engine() -> MatchingEngine:
    """
    Return the shared MatchingEngine instance (singleton via lru_cache).
    Reads ANTHROPIC_API_KEY from environment.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set")

    operators = _load_operators()
    return MatchingEngine(api_key=api_key, operators=operators)


def get_operator_count() -> int:
    """Returns the number of operators loaded (for health checks)."""
    try:
        return len(_load_operators())
    except FileNotFoundError:
        return 0
