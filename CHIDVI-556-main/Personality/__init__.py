"""Personality package with file-based persona discovery."""
from .manager import PersonalityManager
from .loader import discover_personalities

__all__ = ["PersonalityManager", "discover_personalities"]
