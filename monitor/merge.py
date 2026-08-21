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

Usage:  python -m monitor.merge OURS THEIRS OUT
"""
import json
import sys

USER_OWNED = ("status", "applied_on")


def merge(ours: dict, theirs: dict) -> dict:
    """Union of both, resolving each field to whichever side owns it."""
    out = {
        "version": theirs.get("version", ours.get("version", 1)),
        "jobs": dict(theirs.get("jobs", {})),
    }
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
          f"-> {len(result['jobs'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
