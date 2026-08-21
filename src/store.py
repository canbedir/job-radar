"""Persistent state: what has already been seen, and the archive of everything.

Two files under data/:

* seen.json  - dedup ledger, written back to the repo by the workflow so state
               survives across runs on ephemeral GitHub runners.
* jobs.jsonl - append-only archive of every job ever collected, including the
               ones that scored below the notification threshold. Nothing is
               lost just because it did not buzz the phone.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Job

# Drop ledger entries older than this. LinkedIn postings fall out of the search
# window long before then, so anything older can never resurface as a duplicate.
RETENTION_DAYS = 60


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.seen_path = data_dir / "seen.json"
        self.archive_path = data_dir / "jobs.jsonl"
        self.seen: dict[str, str] = {}
        self.created_at: str = ""
        self._load()

    def _load(self) -> None:
        if not self.seen_path.exists():
            return
        try:
            with self.seen_path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            self.seen = data.get("seen", {})
            self.created_at = data.get("created_at", "")
        except (json.JSONDecodeError, OSError):
            # A corrupt ledger must not stop the run. Worst case the next run
            # re-notifies recent jobs once, which is far better than crashing.
            self.seen = {}
            self.created_at = ""

    @staticmethod
    def _utc_now() -> datetime:
        """Timezone-aware UTC clock.

        The ledger is written by GitHub runners (UTC) and read locally in
        whatever zone the machine uses. A naive timestamp made those two
        disagree by the offset, so every timing decision uses UTC explicitly.
        """
        return datetime.now(timezone.utc)

    def warmup_remaining(self, hours: int) -> float:
        """Hours left before notifications should start.

        LinkedIn returns a different slice of results on each call, so a single
        seeding pass does not capture the existing backlog -- the first day of
        runs keeps turning up dozens of older postings that are new only to the
        ledger. Staying silent for a full window lets the backlog settle, after
        which anything new really is new.
        """
        if not self.created_at:
            return float(hours)
        try:
            started = datetime.fromisoformat(self.created_at)
        except ValueError:
            return 0.0
        if started.tzinfo is None:
            # Ledgers written before timestamps carried a zone were produced by
            # UTC runners, so read them as UTC rather than as local time.
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (self._utc_now() - started).total_seconds() / 3600
        return max(hours - elapsed, 0.0)

    @property
    def is_empty(self) -> bool:
        """True on the very first run, before any ledger exists."""
        return not self.seen

    def is_new(self, job: Job) -> bool:
        """True when neither the source id nor the cross-source fingerprint is known."""
        return job.key not in self.seen and job.fingerprint not in self.seen

    def mark(self, job: Job) -> None:
        stamp = self._utc_now().isoformat(timespec="seconds")
        self.seen[job.key] = stamp
        self.seen[job.fingerprint] = stamp

    def filter_new(self, jobs: list[Job]) -> list[Job]:
        """Return only jobs not seen before, without marking them yet.

        Also collapses duplicates inside this batch, so two queries surfacing the
        same posting cannot produce two notifications.
        """
        fresh: list[Job] = []
        batch: set[str] = set()
        for job in jobs:
            if not self.is_new(job) or job.key in batch or job.fingerprint in batch:
                continue
            batch.add(job.key)
            batch.add(job.fingerprint)
            fresh.append(job)
        return fresh

    def _prune(self) -> None:
        cutoff = (self._utc_now().date() - timedelta(days=RETENTION_DAYS)).isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}

    def commit(self, jobs: list[Job]) -> None:
        """Mark jobs as seen, append them to the archive and persist the ledger."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for job in jobs:
            self.mark(job)
        self._prune()

        if jobs:
            with self.archive_path.open("a", encoding="utf-8") as fh:
                for job in jobs:
                    fh.write(json.dumps(job.to_dict(), ensure_ascii=False) + "\n")

        # Write via a temporary file so an interrupted run cannot leave behind a
        # truncated ledger that would re-notify everything.
        tmp = self.seen_path.with_suffix(".json.tmp")
        now = self._utc_now().isoformat(timespec="seconds")
        payload = {
            "created_at": self.created_at or now,
            "updated_at": now,
            "seen": self.seen,
        }
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, self.seen_path)
