"""Second pass: read the full posting to judge seniority.

The search endpoint gives titles only, and titles lie about seniority in both
directions -- a plain "Frontend Developer" was measured demanding four years,
while an "Associate" posting asked for two. The requirement is almost always
stated in the body instead, so promising jobs get their description fetched and
their year requirement extracted before anything is sent.

This costs one extra request per job, so only jobs that already look relevant
are enriched, under a per-run cap.
"""

from __future__ import annotations

import re

from .fetcher import BudgetExhausted, Fetcher, RateLimited
from .models import Job
from .sources import linkedin

# Ordered by how explicit the phrasing is. Both English and Turkish postings
# appear in the Turkish market, often mixed within one description.
_YEAR_PATTERNS = [
    re.compile(r"(\d+)\s*\+\s*(?:years?|yrs?|yıl|yil)", re.I),
    re.compile(r"(?:en az|minimum|at least)\s*(\d+)\s*(?:years?|yrs?|yıl|yil)", re.I),
    re.compile(r"(\d+)\s*[-–]\s*\d+\s*(?:years?|yrs?|yıl|yil)", re.I),
    re.compile(r"(\d+)\s*(?:years?|yrs?|yıl|yil)\s*(?:of\s+)?(?:experience|deneyim|tecrübe)", re.I),
]

# Guards against absurd captures like a "2024 years" typo or a phone number.
_MAX_PLAUSIBLE_YEARS = 20


def extract_years(text: str) -> int | None:
    """Smallest credible year requirement stated in the text, if any.

    The minimum is taken because a posting listing several figures ("3+ years
    with React, 5+ years overall") is usually reachable at the lower bar, and
    over-filtering costs the user a job while under-filtering costs one glance.
    """
    if not text:
        return None
    found: list[int] = []
    for pattern in _YEAR_PATTERNS:
        for match in pattern.findall(text):
            value = int(match)
            if 0 < value <= _MAX_PLAUSIBLE_YEARS:
                found.append(value)
    return min(found) if found else None


def enrich(fetcher: Fetcher, jobs: list[Job], limit: int) -> list[str]:
    """Attach description and year requirement to up to `limit` LinkedIn jobs.

    ATS jobs already carry a description and are skipped. Failures are recorded
    but never fatal: an un-enriched job simply stays unfiltered rather than
    being dropped, since a missed job is worse than an extra notification.
    """
    warnings: list[str] = []
    budget = limit

    for job in jobs:
        if budget <= 0:
            break
        if job.source != "linkedin" or job.enriched:
            continue

        try:
            description = linkedin.fetch_detail(fetcher, job.external_id)
        except (RateLimited, BudgetExhausted) as exc:
            warnings.append(f"enrichment stopped: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 - one bad posting must not kill the pass
            warnings.append(f"could not read job {job.external_id}: {exc}")
            continue

        budget -= 1
        job.description = description
        job.years_required = extract_years(description)
        job.enriched = True

    return warnings


def within_experience(job: Job, max_years: int) -> bool:
    """True when the posting is reachable at the user's experience level.

    Jobs that state no requirement pass. Staying silent about a job because it
    failed to mention a number would hide exactly the openings worth seeing.
    """
    return job.years_required is None or job.years_required <= max_years
