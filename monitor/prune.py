"""Drop tracked postings that are not in the US after all.

jobs.json accumulates; a filter fix only changes what future scans admit, so
postings that a since-fixed hole let in stay on the dashboard forever. This
re-applies the *current* location rules to what is already stored.

Entries the dashboard owns are never touched: anything whose status has moved
off "new" is reported and kept, because a posting you have already applied to
is worth more than a tidy feed.

Usage:  python -m monitor.prune [--dry-run]
"""
import sys

from . import filters, state
from .fetchers.generic import workday_country


def find(jobs: dict) -> tuple[dict, dict]:
    """-> ({id: reason} to drop, {id: reason} kept because the user acted)."""
    drop, held = {}, {}
    for jid, j in jobs.items():
        reason = ""
        # Workday hides the country behind "N Locations"; the URL slug has it.
        if j.get("source") == "workday":
            country = workday_country(j.get("url", ""))
            if country and country != "US":
                reason = country
        if not reason and not filters.is_us(j.get("location", "")):
            reason = j.get("location", "") or "non-US"
        if not reason:
            continue
        (held if j.get("status", "new") != "new" else drop)[jid] = reason
    return drop, held


def main(argv):
    dry = "--dry-run" in argv[1:]
    st = state.load()
    drop, held = find(st["jobs"])

    for jid, reason in sorted(held.items()):
        j = st["jobs"][jid]
        print(f"  kept ({j['status']}): {j['company']} - {j['title']} [{reason}]")
    for jid, reason in sorted(drop.items()):
        j = st["jobs"][jid]
        print(f"  drop [{reason}]: {j['company']} - {j['title']}")

    print(f"\n{len(drop)} of {len(st['jobs'])} tracked postings are not US"
          + (f", {len(held)} more kept (already actioned)" if held else ""))
    if dry:
        print("Dry run: nothing saved.")
        return 0
    for jid in drop:
        del st["jobs"][jid]
    state.save(st)
    print(f"saved: {len(st['jobs'])} postings remain")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
