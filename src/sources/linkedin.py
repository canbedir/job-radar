"""LinkedIn public guest job search.

Uses the /jobs-guest/ endpoint that LinkedIn serves to logged-out visitors for SEO
and embedded job widgets. No cookie, no session, no account is involved, so this
never touches the user's LinkedIn account.

Two behaviours of this endpoint drive the design:

* It returns 10 cards per request and stops producing results past `start=40`,
  so coverage comes from many narrow queries rather than one broad one.
* `sortBy=DD` orders by date instead of relevance, which is the whole point:
  the normal LinkedIn UI ranks by relevance and buries fresh postings.
"""

from __future__ import annotations

import html as html_lib
import re

from ..fetcher import Fetcher
from ..models import Job

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

PAGE_SIZE = 10

_TAG = re.compile(r"<[^>]+>")
_JOB_ID = re.compile(r"urn:li:jobPosting:(\d+)")
_TITLE = re.compile(r'base-search-card__title"[^>]*>(.*?)</h3>', re.S)
_COMPANY = re.compile(r'base-search-card__subtitle"[^>]*>(.*?)</h4>', re.S)
_LOCATION = re.compile(r'job-search-card__location"[^>]*>(.*?)<', re.S)
_DATETIME = re.compile(r'datetime="([^"]+)"')
_URL = re.compile(r'href="(https://[^"]*?/jobs/view/[^"?]+)')

# Remote is inferred from the text of the posting, never from the f_WT query
# parameter: the guest endpoint accepts f_WT but does not actually apply it,
# so trusting it would mark every on-site job as remote.
_REMOTE_HINTS = ("remote", "uzaktan", "work from home", "wfh")


def _text(raw: str) -> str:
    """Strip tags and unescape entities from a captured HTML fragment."""
    return html_lib.unescape(_TAG.sub("", raw)).strip()


def parse_cards(markup: str, query_label: str = "") -> list[Job]:
    """Parse the `<li>` cards returned by the guest search endpoint.

    Fields are extracted per card rather than with document-wide regexes, so a
    card missing a location cannot shift the other fields out of alignment.
    """
    jobs: list[Job] = []
    for card in re.split(r"(?=<li\b)", markup):
        id_match = _JOB_ID.search(card)
        if not id_match:
            continue

        title_match = _TITLE.search(card)
        company_match = _COMPANY.search(card)
        if not title_match or not company_match:
            continue

        location_match = _LOCATION.search(card)
        date_match = _DATETIME.search(card)
        url_match = _URL.search(card)

        job_id = id_match.group(1)
        location = _text(location_match.group(1)) if location_match else ""
        title = _text(title_match.group(1))

        haystack = f"{title} {location}".lower()
        jobs.append(
            Job(
                source="linkedin",
                external_id=job_id,
                title=title,
                company=_text(company_match.group(1)),
                location=location,
                url=url_match.group(1) if url_match else f"https://www.linkedin.com/jobs/view/{job_id}",
                posted_at=date_match.group(1) if date_match else "",
                remote=any(hint in haystack for hint in _REMOTE_HINTS),
                query_label=query_label,
            )
        )
    return jobs


def fetch(fetcher: Fetcher, query: dict, pages: int, time_filter: str) -> list[Job]:
    """Run one configured query and return its jobs, newest first.

    Stops early on the first page that yields nothing, which is how this endpoint
    signals the end of results.
    """
    label = query.get("label") or query.get("keywords", "")
    jobs: list[Job] = []

    for page in range(pages):
        params = {
            "keywords": query["keywords"],
            "f_TPR": time_filter,
            "sortBy": "DD",
            "start": page * PAGE_SIZE,
        }
        if query.get("geo_id"):
            params["geoId"] = str(query["geo_id"])
        if query.get("location"):
            params["location"] = query["location"]
        if query.get("f_WT"):
            params["f_WT"] = str(query["f_WT"])
        if query.get("f_E"):
            params["f_E"] = str(query["f_E"])

        markup = fetcher.get(SEARCH_URL, params=params).text
        page_jobs = parse_cards(markup, query_label=label)
        if not page_jobs:
            break
        jobs.extend(page_jobs)

    return jobs


DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

_DESCRIPTION = re.compile(r'class="[^"]*description__text[^"]*"(.*?)</section>', re.S)


def fetch_detail(fetcher: Fetcher, job_id: str) -> str:
    """Fetch the full description text for one posting.

    The search endpoint returns titles only, but seniority is usually stated in
    the body ("at least 5 years"), not the title -- a plain "Frontend Developer"
    can still demand four years. This is the only way to see that.

    LinkedIn's own seniority label is deliberately ignored: it is unreliable
    (postings tagged "Entry level" were found demanding four years) and its
    wording changes with the request's Accept-Language.
    """
    markup = fetcher.get(DETAIL_URL.format(job_id=job_id)).text
    match = _DESCRIPTION.search(markup)
    return _text(match.group(1)) if match else ""
