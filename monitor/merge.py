"""Reconcile two versions of docs/data/jobs.json.

A scan's push can lose a race: another scan may land first, or the dashboard
may write a status through the GitHub API mid-run. Git cannot merge the JSON
on its own (both sides touch "updated" and neighbouring entries), so a plain
`git pull --rebase` conflicts and the run dies without publishing anything.

The file is a dict keyed by job id, which merges cleanly once you know who
owns what:
  * the scanner owns discovery - it only ever ADDS ids and fills in blank
    enrichment fields,
  * the dashboard owns "status" and "applied_on" - the scanner never writes
    them after an entry exists.

So the branch's copy wins on user data, ours contributes any job it has not
seen yet plus enrichment it is missing, and no edit is lost either way.

The "sources" block is merged too. It used to be dropped, because the result
was built from a fixed list of keys that did not name it - which quietly reset
every fetcher's health history on any push race, re-firing the "never worked"
alert for each currently-empty source and erasing the high-water marks a real
outage is measured against. The result is now built from the branch's copy, so
a top-level key can never be lost by omission again.

Usage:  python -m monitor.merge OURS THEIRS OUT
"""
import json
import sys

USER_OWNED = ("status", "applied_on")


def merge_source(mine: dict, current: dict) -> dict:
    """Reconcile one fetcher's health record across the two copies.

    "best" and "last_ok" are monotone - a count reached and a run that
    succeeded are facts, so the larger/later one wins outright.

    "last" is the count from the most recent run, and a 0 on one side may just
    mean that side's run did not cover this tier. The healthier count is kept,
    because the two mistakes are not symmetric: keeping a stale non-zero only
    delays an alert by one scan (the next empty run still trips it), whereas
    keeping a stale 0 clears the trigger entirely and the breakage goes unseen.
    """
    out = {
        "best": max(mine.get("best", 0), current.get("best", 0)),
        "last": max(mine.get("last", 0), current.get("last", 0)),
        "last_ok": max(mine.get("last_ok") or "", current.get("last_ok") or "") or None,
    }
    # "reported_dead" latches a one-time alert. Either side seeing the source
    # work clears it, so it only survives while BOTH copies still hold it.
    if mine.get("reported_dead") and current.get("reported_dead"):
        out["reported_dead"] = max(mine["reported_dead"], current["reported_dead"])
    return out


def merge(ours: dict, theirs: dict) -> dict:
    """Union of both, resolving each field to whichever side owns it."""
    out = dict(theirs)          # start from the branch so no key is lost
    out["version"] = theirs.get("version", ours.get("version", 1))
    out["jobs"] = dict(theirs.get("jobs", {}))
    for jid, mine in ours.get("jobs", {}).items():
        current = out["jobs"].get(jid)
        if current is None:
            out["jobs"][jid] = mine          # a posting only this run found
            continue
        merged = dict(current)
        for key, value in mine.items():
            if key in USER_OWNED:
                continue                     # never overwrite the user's own marks
            if value and not merged.get(key):
                merged[key] = value          # backfill only what is missing
        out["jobs"][jid] = merged
    sources = {name: dict(rec) for name, rec in (theirs.get("sources") or {}).items()}
    for name, mine in (ours.get("sources") or {}).items():
        sources[name] = (merge_source(mine, sources[name]) if name in sources
                         else dict(mine))
    if sources:
        out["sources"] = sources
    stamps = [s for s in (ours.get("updated"), theirs.get("updated")) if s]
    out["updated"] = max(stamps) if stamps else None
    return out


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    ours_p, theirs_p, out_p = argv[1:]
    with open(ours_p, encoding="utf-8") as f:
        ours = json.load(f)
    with open(theirs_p, encoding="utf-8") as f:
        theirs = json.load(f)
    result = merge(ours, theirs)
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False, sort_keys=True)
    added = len(result["jobs"]) - len(theirs.get("jobs", {}))
    print(f"merged: {len(theirs.get('jobs', {}))} on branch + {added} from this run "
          f"-> {len(result['jobs'])} jobs, "
          f"{len(result.get('sources', {}))} source health record(s) kept")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
