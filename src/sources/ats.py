"""Applicant tracking system job boards.

Greenhouse, Lever and Ashby all expose a public, unauthenticated JSON endpoint
so that customers can embed their openings on their own marketing sites. These
are documented product surfaces rather than scraped pages, and crucially they
publish a role the moment it goes live -- typically one to three days before
LinkedIn's crawler indexes it. That head start is the point of this source.

Each adapter returns the common Job shape so the rest of the pipeline is
unaware of which board a posting came from.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime, timedelta, timezone

from ..fetcher import Fetcher
from ..models import Job

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

_REMOTE_HINTS = ("remote", "anywhere", "distributed")


def _plain(raw: str | None, limit: int = 600) -> str:
    """Flatten an HTML description into trimmed plain text for the archive."""
    if not raw:
        return ""
    text = html_lib.unescape(_TAG.sub(" ", raw))
    return _WS.sub(" ", text).strip()[:limit]


def _iso_date(value: str | None) -> str:
    """Normalize the various timestamp formats these boards use to YYYY-MM-DD."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10]


def _epoch_ms_date(value) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _is_remote(*fields: str) -> bool:
    haystack = " ".join(f for f in fields if f).lower()
    return any(hint in haystack for hint in _REMOTE_HINTS)


def fetch_greenhouse(fetcher: Fetcher, slug: str) -> list[Job]:
    payload = fetcher.get(GREENHOUSE_URL.format(slug=slug)).json()
    jobs = []
    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name", "")
        title = item.get("title", "").strip()
        jobs.append(
            Job(
                source="greenhouse",
                external_id=f"{slug}:{item.get('id')}",
                title=title,
                company=item.get("company_name") or slug,
                location=location,
                url=item.get("absolute_url", ""),
                # first_published is the original posting date; updated_at moves
                # whenever the description is edited and would misreport age.
                posted_at=_iso_date(item.get("first_published") or item.get("updated_at")),
                remote=_is_remote(title, location),
                description=_plain(item.get("content")),
                query_label=f"gh/{slug}",
            )
        )
    return jobs


def fetch_lever(fetcher: Fetcher, slug: str) -> list[Job]:
    payload = fetcher.get(LEVER_URL.format(slug=slug), params={"mode": "json"}).json()
    jobs = []
    for item in payload:
        categories = item.get("categories") or {}
        location = categories.get("location", "")
        workplace = item.get("workplaceType", "")
        title = item.get("text", "").strip()
        jobs.append(
            Job(
                source="lever",
                external_id=f"{slug}:{item.get('id')}",
                title=title,
                company=slug,
                location=location,
                url=item.get("hostedUrl", ""),
                posted_at=_epoch_ms_date(item.get("createdAt")),
                remote=workplace.lower() == "remote" or _is_remote(title, location),
                description=_plain(item.get("descriptionPlain")),
                query_label=f"lever/{slug}",
            )
        )
    return jobs


def fetch_ashby(fetcher: Fetcher, slug: str) -> list[Job]:
    payload = fetcher.get(ASHBY_URL.format(slug=slug)).json()
    jobs = []
    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue
        location = item.get("location", "") or ""
        title = item.get("title", "").strip()
        jobs.append(
            Job(
                source="ashby",
                external_id=f"{slug}:{item.get('id')}",
                title=title,
                company=slug,
                location=location,
                url=item.get("jobUrl", ""),
                posted_at=_iso_date(item.get("publishedAt")),
                remote=bool(item.get("isRemote")) or _is_remote(title, location),
                description=_plain(item.get("descriptionPlain")),
                query_label=f"ashby/{slug}",
            )
        )
    return jobs


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def within_age(job: Job, max_age_days: int, today: date | None = None) -> bool:
    """ATS boards return their entire catalogue, including years-old postings.

    Only recent ones are worth carrying into the pipeline.
    """
    if not job.posted_at:
        return False
    try:
        posted = datetime.strptime(job.posted_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return posted >= (today or date.today()) - timedelta(days=max_age_days)
