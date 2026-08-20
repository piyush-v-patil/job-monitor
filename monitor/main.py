"""Entrypoint.

Usage:
  python -m monitor.main --tier bigtech          # every-3h run
  python -m monitor.main --tier all              # daily full sweep
  python -m monitor.main --tier all --dry-run    # print, don't save/notify

First run behavior: if the state file is empty, all found jobs are SEEDED
into state without Discord notifications (avoids a 500-message flood).
"""
import argparse
import concurrent.futures as cf
import os
import sys

import yaml

from . import filters, notify, state
from .fetchers import FETCHERS

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "companies.yaml")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_fetcher(company):
    name = company.get("name", company.get("fetcher"))
    fn = FETCHERS.get(company.get("fetcher", ""))
    if fn is None:
        print(f"  ! {name}: unknown fetcher '{company.get('fetcher')}' - skipped")
        return []
    try:
        jobs = fn(company)
        print(f"  ✓ {name}: {len(jobs)} raw postings")
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  ! {name}: FAILED - {e}")
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["bigtech", "other", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-senior", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    companies = []
    if args.tier in ("bigtech", "all"):
        companies += cfg.get("bigtech", [])
    if args.tier in ("other", "all"):
        companies += cfg.get("other", [])
        companies += cfg.get("aggregators", [])

    print(f"Scanning {len(companies)} sources (tier={args.tier})...")
    raw = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for jobs in ex.map(run_fetcher, companies):
            raw.extend(jobs)

    in_scope = []
    for j in raw:
        r = filters.in_scope(j, include_senior=args.include_senior)
        if r:
            # Aggregator rows carry a tier hint; trust it if the title was ambiguous.
            if j.get("tier_hint") and r["tier"] == "experienced":
                r["tier"] = j["tier_hint"]
            in_scope.append(r)

    st = state.load()
    seeding = not st["jobs"]
    new = state.add_new(st, in_scope)

    print(f"\n{len(raw)} raw -> {len(in_scope)} in scope -> {len(new)} new"
          + (" (seed run: notifications suppressed)" if seeding else ""))
    for j in new[:50]:
        print(f"  [{j['tier']:>11}] {j['company']}: {j['title']} ({j['location'][:60]})")

    if args.dry_run:
        print("\nDry run: nothing saved or sent.")
        return

    state.save(st)
    if new and not seeding:
        notify.send(new, run_label=f"(scan: {args.tier})")

    # Fail the workflow visibly if literally every fetcher errored.
    if raw == [] and companies:
        print("All fetchers returned nothing - check endpoints.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
