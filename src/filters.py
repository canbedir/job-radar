"""Hard filters applied after scoring, before anything is sent.

These answer "could I actually take this job?" rather than "is it interesting?".
A job failing one of these is never notified regardless of how well it scored.

Both filters share one rule: when the posting does not say, the job is kept.
Silence is not evidence, and a missed opening costs more than a glance at an
irrelevant card.
"""

from __future__ import annotations

from .models import Job, normalize


def within_experience(job: Job, max_years: int) -> bool:
    """True when the stated year requirement is reachable.

    Postings that name no figure pass: plenty of junior-friendly roles simply
    never mention years.
    """
    return job.years_required is None or job.years_required <= max_years


def is_reachable(job: Job, commutable: list[str], other_cities: list[str]) -> bool:
    """True when the user could physically work this job.

    Remote roles are exempt from geography. Anything requiring presence -- including
    hybrid, which still means commuting -- must be somewhere the user can reach.

    Istanbul postings frequently name only a district ("Şişli", "Kartal") with no
    mention of the city, so the commutable list carries districts as well.
    """
    if job.remote:
        return True

    # Compared word by word rather than as substrings, so a short place name
    # cannot match inside an unrelated word.
    words = set(normalize(job.location).split())
    if not words:
        return True

    if words & {normalize(place) for place in commutable}:
        return True

    # A different city we recognise means the job is genuinely out of reach.
    # An unrecognised or vague location ("Türkiye") is kept rather than guessed at.
    return not (words & {normalize(city) for city in other_cities})


def title_allowed(job: Job, blocked: list[str]) -> bool:
    """True unless the title itself announces a seniority above the user's level."""
    title = normalize(job.title)
    return not any(word in title.split() for word in blocked)
