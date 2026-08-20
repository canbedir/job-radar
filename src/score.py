"""Rule-based relevance scoring.

Scores decide whether a job buzzes the phone or quietly lands in the archive.
All rules live in config.yml so tuning never requires a code change.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .models import Job, normalize


def _compile(terms: dict[str, int]) -> list[tuple[str, re.Pattern[str], int]]:
    """Compile scoring terms into whole-word patterns.

    Whole-word matching matters: a substring search for ".net" (normalized to
    "net") would also fire on "network", and "qa" would fire on "qatar".
    """
    compiled = []
    for term, points in terms.items():
        normalized = normalize(str(term))
        if not normalized:
            continue
        compiled.append((str(term), re.compile(rf"\b{re.escape(normalized)}\b"), int(points)))
    return compiled


def _age_in_days(posted_at: str, today: date | None = None) -> int | None:
    if not posted_at:
        return None
    try:
        posted = datetime.strptime(posted_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    # Clamp to zero: LinkedIn occasionally carries a timestamp a few hours ahead
    # of the runner's clock, which would otherwise yield a negative age.
    return max(((today or date.today()) - posted).days, 0)


class Scorer:
    def __init__(self, cfg: dict) -> None:
        # Role terms are the actual relevance signal. Bonus terms only refine an
        # already-relevant job: "remote" describes how you work, not what the job
        # is, so on its own it must never carry a posting over the threshold --
        # otherwise every "Senior Engagement Manager, Remote" gets notified.
        self.role = _compile(cfg.get("role", {}))
        self.bonus = _compile(cfg.get("bonus", {}))
        self.negative = _compile(cfg.get("negative", {}))
        # YAML parses the freshness keys as ints already; normalize defensively.
        self.freshness = {int(k): int(v) for k, v in (cfg.get("freshness") or {}).items()}
        self.threshold = int(cfg.get("notify_threshold", 4))
        # Veto terms are disqualifying rather than merely costly. A penalty can
        # be outweighed by enough positive matches -- "Senior PHP Full-Stack
        # Engineer with React" nets out above the threshold on points alone --
        # whereas a veto is absolute.
        self.veto = _compile({term: 0 for term in (cfg.get("veto") or [])})

    def score(self, job: Job, today: date | None = None) -> tuple[int, list[str]]:
        # Deliberately title + location only, never the description. ATS sources
        # carry a full description while LinkedIn carries none, so including it
        # would score the same job differently depending on where it arrived
        # from, and a passing mention of "php" in an 8k-character body would
        # trip the veto on an otherwise perfect match.
        haystack = normalize(f"{job.title} {job.location}")
        total = 0
        matched: list[str] = []
        hits = 0

        for term, pattern, _ in self.veto:
            if pattern.search(haystack):
                # Score is still reported for the archive, but the veto marker
                # keeps the job out of notifications no matter how high it is.
                matched.append(f"VETO:{term}")

        for term, pattern, points in self.role:
            if pattern.search(haystack):
                total += points
                hits += 1
                matched.append(f"{term}{points:+d}")

        for term, pattern, points in self.negative:
            if pattern.search(haystack):
                total += points
                matched.append(f"{term}{points:+d}")

        # Bonuses and freshness are tie-breakers among relevant jobs, never
        # evidence of relevance themselves. Gating them on a role match stops an
        # unrelated posting from drifting over the threshold on recency or on a
        # "Remote" in its location line.
        if hits:
            for term, pattern, points in self.bonus:
                if pattern.search(haystack):
                    total += points
                    matched.append(f"{term}{points:+d}")

            age = _age_in_days(job.posted_at, today)
            if age is not None and age in self.freshness:
                points = self.freshness[age]
                total += points
                matched.append(f"fresh({age}d){points:+d}")

        return total, matched

    def apply(self, jobs: list[Job], today: date | None = None) -> list[Job]:
        for job in jobs:
            job.score, job.matched_terms = self.score(job, today)
        return jobs

    def should_notify(self, job: Job) -> bool:
        if any(term.startswith("VETO:") for term in job.matched_terms):
            return False
        return job.score >= self.threshold
