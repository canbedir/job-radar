"""Hard filters applied after scoring, before anything is sent.

These answer "could I actually take this job?" rather than "is it interesting?".
A job failing one of these is never notified regardless of how well it scored.

Both filters share one rule: when the posting does not say, the job is kept.
Silence is not evidence, and a missed opening costs more than a glance at an
irrelevant card.
"""

from __future__ import annotations

from .models import Job, normalize


def within_experience(job: Job, max_years: int | None) -> bool:
    """True when the stated year requirement is within reach.

    Disabled when max_years is None, which is the default. A year count in a
    description is an employer's wish rather than a threshold -- postings ask
    for five and hire someone with two -- so filtering on it drops openings
    that were worth an application. The figure is still shown on the card so
    the decision stays with the reader.
    """
    if max_years is None:
        return True
    return job.years_required is None or job.years_required <= max_years


COUNTRY_WORDS = {"turkiye", "turkey"}


def is_reachable(
    job: Job,
    commutable: list[str],
    other_cities: list[str],
    allow_abroad: bool = False,
) -> bool:
    """True when the user could physically work this job.

    Remote roles are exempt from geography. Anything requiring presence --
    including hybrid, which still means commuting -- must be somewhere reachable.

    Istanbul postings frequently name only a district ("Şişli", "Kartal") with no
    mention of the city, so the commutable list carries districts as well.

    A location naming neither a known district nor the country is treated as
    abroad. Recognising only Turkish cities is not enough: a search scoped to
    Turkey still returns "Berlin, Almanya" and "Brezilya", and calling those
    merely unrecognised let every one of them through.
    """
    # Compared word by word rather than as substrings, so a short place name
    # cannot match inside an unrelated word.
    words = set(normalize(job.location).split())
    if not words:
        return True

    if words & {normalize(place) for place in commutable}:
        return True

    if words & COUNTRY_WORDS:
        # Somewhere in Turkey but not Istanbul. Another named city is only
        # workable remotely; a bare "Türkiye" names no city at all and is kept
        # rather than guessed at.
        if words & {normalize(city) for city in other_cities}:
            return job.remote
        return True

    # Neither a known district nor the country: the posting is abroad. Remote
    # does not exempt it -- a remote role advertised abroad is still a job in
    # another country's language and hiring process.
    return allow_abroad


def title_allowed(job: Job, blocked: list[str]) -> bool:
    """True unless the title itself announces a seniority above the user's level."""
    title = normalize(job.title)
    return not any(word in title.split() for word in blocked)
