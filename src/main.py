"""Entry point: collect jobs, deduplicate, score, notify, persist."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .fetcher import BudgetExhausted, Fetcher, RateLimited
from .models import Job
from .notify import Telegram
from .score import Scorer
from .sources import ats, linkedin
from .store import Store

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def collect_linkedin(cfg: dict, fetcher: Fetcher) -> tuple[list[Job], list[str]]:
    """Run every configured LinkedIn query.

    A block ends the whole run, since further requests would only dig the hole
    deeper. Any other per-query failure is recorded and the run continues.
    """
    source_cfg = cfg["sources"]["linkedin"]
    jobs: list[Job] = []
    warnings: list[str] = []

    for query in source_cfg["queries"]:
        label = query.get("label", query["keywords"])
        try:
            found = linkedin.fetch(
                fetcher,
                query,
                pages=source_cfg["pages_per_query"],
                time_filter=source_cfg["time_filter"],
            )
        except RateLimited as exc:
            warnings.append(f"rate limited on {label}: {exc}")
            break
        except BudgetExhausted as exc:
            warnings.append(f"stopped at {label}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 - one bad query must not kill the run
            warnings.append(f"{label} failed: {exc}")
            continue

        log(f"  {label:<16} {len(found):>3} jobs")
        jobs.extend(found)

    return jobs, warnings


def collect_ats(cfg: dict, fetcher: Fetcher) -> tuple[list[Job], list[str]]:
    """Pull every configured ATS board.

    These boards are independent of LinkedIn, so a LinkedIn block must not stop
    them and one unreachable board must not stop the others.
    """
    source_cfg = cfg["sources"]["ats"]
    max_age = int(source_cfg.get("max_age_days", 7))
    jobs: list[Job] = []
    warnings: list[str] = []

    for entry in source_cfg["companies"]:
        board, slug = entry["board"], entry["slug"]
        adapter = ats.ADAPTERS.get(board)
        if adapter is None:
            warnings.append(f"unknown board type: {board}")
            continue

        try:
            found = adapter(fetcher, slug)
        except (RateLimited, BudgetExhausted) as exc:
            warnings.append(f"ats stopped at {board}/{slug}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 - one bad board must not kill the run
            warnings.append(f"{board}/{slug} failed: {exc}")
            continue

        recent = [job for job in found if ats.within_age(job, max_age)]
        log(f"  {board}/{slug:<18} {len(recent):>3} recent of {len(found)}")
        jobs.extend(recent)

    return jobs, warnings


def collect(cfg: dict, fetcher: Fetcher) -> tuple[list[Job], list[str]]:
    jobs: list[Job] = []
    warnings: list[str] = []

    if cfg["sources"].get("linkedin", {}).get("enabled"):
        log("collecting from linkedin...")
        found, warns = collect_linkedin(cfg, fetcher)
        jobs.extend(found)
        warnings.extend(warns)

    if cfg["sources"].get("ats", {}).get("enabled"):
        log("collecting from ats boards...")
        found, warns = collect_ats(cfg, fetcher)
        jobs.extend(found)
        warnings.extend(warns)

    return jobs, warnings


def log(message: str) -> None:
    print(message, file=sys.stderr)


def describe(job: Job, explain: bool = False) -> str:
    remote = " [remote]" if job.remote else ""
    line = f"[{job.score:>3}] {job.posted_at}  {job.title} @ {job.company} - {job.location}{remote}"
    if explain and job.matched_terms:
        line += f"\n       {', '.join(job.matched_terms)}"
    line += f"\n       {job.url}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LinkedIn + ATS job radar")
    parser.add_argument("--config", default=str(ROOT / "config.yml"))
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print results without notifying or writing state",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="show which terms produced each score",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="send one sample card to Telegram and exit",
    )
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    telegram = Telegram()

    if args.test_notify:
        if not telegram.configured:
            log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")
            return 1
        sample = Job(
            source="linkedin",
            external_id="0",
            title="Senior Frontend Engineer (React)",
            company="Example Corp",
            location="Istanbul, Turkey",
            url="https://www.linkedin.com/jobs/",
            posted_at="2026-08-20",
            remote=True,
            score=12,
            matched_terms=["react+3", "typescript+3", "remote+4", "fresh(0d)+3"],
        )
        ok = telegram.send_job(sample)
        log("sent" if ok else "failed")
        return 0 if ok else 1

    run_cfg = cfg["run"]
    fetcher = Fetcher(
        max_requests=run_cfg["max_requests"],
        delay_range=tuple(run_cfg["delay_range"]),
        timeout=run_cfg["timeout"],
    )

    jobs, warnings = collect(cfg, fetcher)

    store = Store(Path(args.data_dir))
    first_run = store.is_empty
    warmup_left = store.warmup_remaining(int(run_cfg.get("warmup_hours", 24)))
    fresh = store.filter_new(jobs)

    scorer = Scorer(cfg["scoring"])
    scorer.apply(fresh)
    fresh.sort(key=lambda j: (j.score, j.posted_at), reverse=True)

    to_notify = [job for job in fresh if scorer.should_notify(job)]

    log(
        f"\n{len(jobs)} collected, {len(fresh)} new, "
        f"{len(to_notify)} above threshold ({scorer.threshold}), "
        f"{fetcher.count} requests"
    )
    for warning in warnings:
        log(f"WARN {warning}")

    if args.dry_run:
        for job in fresh:
            print(describe(job, explain=args.explain))
        log("\ndry run: nothing notified, nothing written")
        return 0

    tg_cfg = cfg.get("telegram", {})
    telegram_ready = bool(tg_cfg.get("enabled")) and telegram.configured

    if warmup_left > 0:
        # Seed silently. LinkedIn hands back a different slice of its backlog on
        # every call, so the first day of runs keeps surfacing older postings
        # that are only new to the ledger. Notifying through that would bury the
        # user before a single genuinely new job arrives.
        log(
            f"warm-up: seeding {len(fresh)} jobs without notifying "
            f"({warmup_left:.1f}h remaining)"
        )
        if first_run and telegram_ready:
            telegram.send_text(
                f"✅ <b>job radar armed</b>\nLearning the existing backlog for "
                f"{warmup_left:.0f}h. Alerts start once that settles."
            )
        store.commit(fresh)
        log(f"state written to {store.seen_path}")
        return 0

    if telegram_ready:
        limit = int(tg_cfg.get("max_notifications_per_run", 15))
        overflow = len(to_notify) - limit
        for job in to_notify[:limit]:
            telegram.send_job(job)
        if overflow > 0:
            # Rather than spamming, say how many were held back; they stay in
            # the archive and can be reviewed there.
            telegram.send_text(f"…and {overflow} more matches this run (archived).")
        # Report a degraded run instead of failing silently.
        for warning in warnings:
            if "rate limited" in warning:
                telegram.send_warning(warning)
                break
    elif tg_cfg.get("enabled"):
        log("WARN telegram enabled but credentials missing; skipping notifications")

    # Everything collected is committed, not just what was notified, so a job
    # below the threshold is never offered again on the next run.
    store.commit(fresh)
    log(f"state written to {store.seen_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
