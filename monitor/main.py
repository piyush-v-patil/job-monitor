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
        mark = "✓" if jobs else "∅"   # ∅ = reachable but returned nothing
        print(f"  {mark} {name}: {len(jobs)} raw postings")
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  ! {name}: FAILED - {e}")
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["bigtech", "other", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-notify", action="store_true",
                    help="save state but send no Discord messages (use after "
                         "repairing a fetcher, to absorb its backlog quietly)")
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
    raw, counts = [], []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for jobs in ex.map(run_fetcher, companies):
            counts.append(len(jobs))
            raw.extend(jobs)

    in_scope = []
    for j in raw:
        r = filters.in_scope(j, include_senior=args.include_senior)
        if r:
            # Aggregator rows carry a tier hint; trust it if the title was ambiguous.
            if j.get("tier_hint") and r["tier"] == "experienced":
                r["tier"] = j["tier_hint"]
                # the tier just changed, so the urgency derived from it must
                # be recomputed - otherwise a hinted new grad row ships with
                # an experienced role's priority and no apply-now flag
                if r["tier"] == "newgrad":
                    r["apply_now"] = True
                    r.setdefault("newgrad_signal", "aggregator")
                r["priority"] = filters.priority(r)
            in_scope.append(r)

    st = state.load()
    seeding = not st["jobs"]
    rekeyed = state.migrate_ids(st)
    if rekeyed:
        print(f"re-keyed {rekeyed} posting(s) onto slugged ids")
    merged = state.dedupe(st)
    if merged:
        print(f"merged {merged} duplicate posting(s) held under two source ids")
    new = state.add_new(st, in_scope)

    print(f"\n{len(raw)} raw -> {len(in_scope)} in scope -> {len(new)} new"
          + (" (seed run: notifications suppressed)" if seeding else ""))

    by_source = {c.get("name", "?"): n for c, n in zip(companies, counts)}
    empty = [name for name, n in by_source.items() if n == 0]
    if empty:
        print(f"WARNING: {len(empty)} source(s) returned 0 postings: "
              + ", ".join(empty))
    # sources that worked last run and are empty now - worth interrupting for
    broke = state.source_health(st, by_source)
    if broke:
        print("BROKEN since last run: "
              + ", ".join(f"{b['name']} (was {b['was']})" for b in broke))
    urgent = [j for j in new if j.get("apply_now")]
    if urgent:
        print(f"\n🚨 {len(urgent)} new grad / early career posting(s) - APPLY IMMEDIATELY:")
        for j in urgent[:50]:
            print(f"  -> {j['company']}: {j['title']} ({j['location'][:60]}) {j['url']}")
    for j in new[:50]:
        print(f"  [{j['tier']:>11}] {j['company']}: {j['title']} ({j['location'][:60]})")

    if args.dry_run:
        print("\nDry run: nothing saved or sent.")
        return

    state.save(st)
    if new and not seeding and not args.no_notify:
        notify.send(new, run_label=f"(scan: {args.tier})")
    elif new and args.no_notify:
        print(f"--no-notify: absorbed {len(new)} job(s) without notifying.")
    # a broken source is reported even on a --no-notify backfill: it means
    # postings are being missed right now, which is not a quiet event
    if broke and not seeding:
        notify.send_alert(broke)

    # Fail the workflow visibly if literally every fetcher errored.
    if raw == [] and companies:
        print("All fetchers returned nothing - check endpoints.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
