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
from .sources import linkedin
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


def collect(cfg: dict, fetcher: Fetcher) -> tuple[list[Job], list[str]]:
    jobs: list[Job] = []
    warnings: list[str] = []

    if cfg["sources"].get("linkedin", {}).get("enabled"):
        log("collecting from linkedin...")
        found, warns = collect_linkedin(cfg, fetcher)
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
    bootstrap = store.is_empty
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
    if bootstrap:
        # First run sees a whole 24h backlog at once. Sending all of it would
        # bury the user under notifications, so the ledger is seeded silently
        # and only real-time arrivals are announced from the next run onwards.
        log(f"first run: seeding ledger with {len(fresh)} jobs, no notifications sent")
        if tg_cfg.get("enabled") and telegram.configured:
            telegram.send_text(
                f"✅ <b>job radar armed</b>\nSeeded with {len(fresh)} existing postings. "
                "You will be notified about new ones from now on."
            )
        store.commit(fresh)
        log(f"state written to {store.seen_path}")
        return 0

    if tg_cfg.get("enabled") and telegram.configured:
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
