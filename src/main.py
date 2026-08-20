"""Entry point: collect jobs, deduplicate, score, notify, persist."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .fetcher import BudgetExhausted, Fetcher, RateLimited
from .models import Job
from .sources import linkedin

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def collect_linkedin(cfg: dict, fetcher: Fetcher) -> tuple[list[Job], list[str]]:
    """Run every configured LinkedIn query.

    A block ends the whole run, since more requests would only dig the hole
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

        print(f"  {label:<16} {len(found):>3} jobs", file=sys.stderr)
        jobs.extend(found)

    return jobs, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LinkedIn + ATS job radar")
    parser.add_argument("--config", default=str(ROOT / "config.yml"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print results instead of notifying or writing state",
    )
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    run_cfg = cfg["run"]
    fetcher = Fetcher(
        max_requests=run_cfg["max_requests"],
        delay_range=tuple(run_cfg["delay_range"]),
        timeout=run_cfg["timeout"],
    )

    print("collecting from linkedin...", file=sys.stderr)
    jobs, warnings = collect_linkedin(cfg, fetcher)

    # Collapse duplicates that several queries surfaced, keeping first occurrence.
    unique: dict[str, Job] = {}
    for job in jobs:
        unique.setdefault(job.key, job)

    print(
        f"\n{len(jobs)} results, {len(unique)} unique, {fetcher.count} requests",
        file=sys.stderr,
    )
    for warning in warnings:
        print(f"WARN {warning}", file=sys.stderr)

    for job in sorted(unique.values(), key=lambda j: j.posted_at, reverse=True):
        remote = " [remote]" if job.remote else ""
        print(f"{job.posted_at}  {job.title} @ {job.company} - {job.location}{remote}")
        print(f"           {job.url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
