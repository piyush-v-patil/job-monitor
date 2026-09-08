"""Entrypoint.

Usage:
  python -m monitor.main --tier bigtech                      # every-3h run
  python -m monitor.main --tier all                          # daily full sweep
  python -m monitor.main --tier all --dry-run                # print, don't save/notify
  python -m monitor.main --profile supplychain --tier all    # the other tracker

--profile picks which tracker to run (see monitor/profiles.py): its company
registry, its role rules, its state file and its Discord webhook. The default
is the original software tracker, so every existing invocation is unchanged.

First run behavior: if the state file is empty, all found jobs are SEEDED
into state without Discord notifications (avoids a 500-message flood).
"""
import argparse
import concurrent.futures as cf
import sys

import yaml

from . import notify, profiles, state
from .fetchers import FETCHERS


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_fetcher(company):
    """Fetch one source, running it once per configured search term.

    Boards that only expose a keyword search (Workday, Eightfold, Amazon,
    Microsoft, Google, Walmart) return whatever ranks highest for one phrase,
    and no single phrase covers a whole job family - "demand planning" misses
    every "Sr Planner" and "S&OP Manager" on the same board. A `searches:`
    list runs the fetcher once per term and merges the results, which is the
    difference between seeing a company's planning org and seeing a corner of
    it. Postings repeat heavily across terms, so the union costs far less
    than the number of passes suggests.
    """
    name = company.get("name", company.get("fetcher"))
    fn = FETCHERS.get(company.get("fetcher", ""))
    if fn is None:
        print(f"  ! {name}: unknown fetcher '{company.get('fetcher')}' - skipped")
        return []
    terms = company.get("searches") or [company.get("search")]
    jobs, seen, failures = [], set(), 0
    for i, term in enumerate(terms):
        pass_cfg = dict(company)
        if term:
            pass_cfg["search"] = term
        pass_cfg.pop("searches", None)
        # Workday also sweeps newest-first with an empty search, which returns
        # the same postings no matter which term it is paired with. Running it
        # once instead of once per term is the difference between a 45-request
        # scan of a board and a 90-request one.
        if i:
            pass_cfg["skip_recent"] = True
        try:
            for job in fn(pass_cfg):
                key = job.get("external_id") or job.get("url", "")
                if key and key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
        except Exception as e:  # noqa: BLE001
            failures += 1
            label = f"{name} [{term}]" if term else name
            print(f"  ! {label}: FAILED - {e}")
    if failures == len(terms):
        return []
    mark = "✓" if jobs else "∅"   # ∅ = reachable but returned nothing
    print(f"  {mark} {name}: {len(jobs)} raw postings"
          + (f" over {len(terms)} searches" if len(terms) > 1 else ""))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=list(profiles.PROFILES), default="tech",
                    help="which tracker to run (default: the software one)")
    ap.add_argument("--tier", choices=["bigtech", "other", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-notify", action="store_true",
                    help="save state but send no Discord messages (use after "
                         "repairing a fetcher, to absorb its backlog quietly)")
    ap.add_argument("--include-senior", action="store_true",
                    help="widen the band: senior titles (tech) / director and "
                         "above (supply chain)")
    args = ap.parse_args()

    profile = profiles.get(args.profile)
    filters = profile.rules
    cfg = load_config(profile.config_path)
    companies = []
    if args.tier in ("bigtech", "all"):
        companies += cfg.get("bigtech", [])
    if args.tier in ("other", "all"):
        companies += cfg.get("other", [])
        companies += cfg.get("aggregators", [])

    print(f"Scanning {len(companies)} sources "
          f"(profile={profile.key}, tier={args.tier})...")
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
            in_scope.append(r)

    st = state.load(profile.state_path)
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
    for j in new[:50]:
        print(f"  [{j['tier']:>11}] {j['company']}: {j['title']} ({j['location'][:60]})")

    if args.dry_run:
        print("\nDry run: nothing saved or sent.")
        return

    state.save(st, profile.state_path)
    if new and not seeding and not args.no_notify:
        notify.send(new, run_label=f"(scan: {profile.key}/{args.tier})",
                    webhook_env=profile.webhook_env,
                    tier_labels=profile.tier_labels, tier_colors=profile.tier_colors)
    elif new and args.no_notify:
        print(f"--no-notify: absorbed {len(new)} job(s) without notifying.")
    # a broken source is reported even on a --no-notify backfill: it means
    # postings are being missed right now, which is not a quiet event
    if broke and not seeding:
        notify.send_alert(broke, webhook_env=profile.webhook_env)

    # Fail the workflow visibly if literally every fetcher errored.
    if raw == [] and companies:
        print("All fetchers returned nothing - check endpoints.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
