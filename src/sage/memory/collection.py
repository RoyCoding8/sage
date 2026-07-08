"""
Json Document Collection — shared base for memory stores backed by a single document.

Eliminates the duplicated get_all() / clear() delegation found across
CaseMemory, SkillLibrary, and similar stores that wrap an AtomicJsonLines
or AtomicJsonDocument.

Subclasses set self._document in __init__ and inherit get_all / clear for free.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class JsonDocumentCollection:
    """Base class for memory stores that delegate to a single atomic document."""

    _document: Any  # set by subclass __init__

    def get_all(self) -> list[dict]:
        """Return all items from the backing document."""
        try:
            return self._document.read()
        except (OSError, ValueError) as e:
            logger.warning("Failed to read %s: %s", type(self).__name__, e)
            return []

    def clear(self):
        """Remove all items from the backing document."""
        self._document.clear()
