"""Common job schema that every source is normalized into."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any

# Fold Turkish letters to ASCII. Python's lower() turns "İ" into an "i" plus a
# combining dot, so the mapping is applied before any case folding.
_TR_MAP = str.maketrans({
    "İ": "i", "I": "i", "ı": "i",
    "Ş": "s", "ş": "s",
    "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u",
    "Ö": "o", "ö": "o",
    "Ç": "c", "ç": "c",
})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Reduce text for comparison: Turkish letters to ASCII, lowercase, single spaces."""
    if not text:
        return ""
    text = text.translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NON_ALNUM.sub(" ", text.lower()).strip()


@dataclass
class Job:
    source: str
    external_id: str
    title: str
    company: str
    location: str = ""
    url: str = ""
    posted_at: str = ""          # ISO date, e.g. "2026-08-20"
    remote: bool = False
    description: str = ""        # populated by ATS sources, empty for LinkedIn
    query_label: str = ""        # which query surfaced this job (for debugging)

    # Filled in by the enrichment pass, which fetches the full posting.
    years_required: int | None = None
    enriched: bool = False

    score: int = 0
    matched_terms: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Uniqueness key within a single source."""
        return f"{self.source}:{self.external_id}"

    @property
    def fingerprint(self) -> str:
        """Uniqueness key across sources.

        The same opening can appear both on LinkedIn and on the company's own ATS.
        Hashing title + company keeps that to a single notification.
        """
        return f"{normalize(self.title)}|{normalize(self.company)}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
