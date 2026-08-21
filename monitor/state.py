"""State persistence: docs/data/jobs.json is the single source of truth.

Structure:
{
  "version": 1,
  "updated": "2026-08-20T12:00:00Z",
  "jobs": {
    "<job_id>": {
      "company": str, "title": str, "tier": "intern|newgrad|experienced",
      "location": str, "url": str, "source": str,
      "first_seen": "YYYY-MM-DD", "status": "new|applied|skip|interview|rejected|closed",
      # best-effort enrichment; key is omitted entirely when the ATS has no value
      "posted_at": "YYYY-MM-DD", "comp": str, "employment_type": str,
      "workplace": "Remote|Hybrid|On-site", "department": str,
      # written by the dashboard when you mark Applied/Interview; the scanner
      # only ever reads past it, so the activity history is never rewritten
      "applied_on": "YYYY-MM-DD"
    }
  }
}

The dashboard edits only the "status" field; the scanner only adds new ids
and never overwrites an existing entry, so user edits are always preserved.
Enrichment fields are the one exception: they are backfilled onto existing
entries when previously missing, which never touches "status".
"""
import hashlib
import json
import os
from datetime import datetime, timezone

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "jobs.json")


def job_id(company: str, external_id: str = "", url: str = "") -> str:
    key = external_id.strip() or url.strip()
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{company.lower().replace(' ', '-')}:{digest}"


def load() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"version": 1, "updated": None, "jobs": {}}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(state: dict) -> None:
    state["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(os.path.abspath(STATE_PATH)), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False, sort_keys=True)


ENRICH = ("posted_at", "comp", "employment_type", "workplace", "department")


def add_new(state: dict, jobs: list) -> list:
    """Add jobs not already tracked. Returns the list of newly added jobs.

    Existing entries are left alone except that missing enrichment fields are
    filled in (a job first seen before enrichment existed gets upgraded on a
    later scan). "status" is never written here.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new = []
    for j in jobs:
        jid = job_id(j["company"], j.get("external_id", ""), j.get("url", ""))
        extra = {k: j[k] for k in ENRICH if j.get(k)}
        if jid in state["jobs"]:
            entry = state["jobs"][jid]
            for k, v in extra.items():          # backfill only what is absent
                entry.setdefault(k, v)
            continue
        entry = {
            "company": j["company"],
            "title": j["title"],
            "tier": j["tier"],
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "source": j.get("source", ""),
            "first_seen": today,
            "status": "new",
        }
        entry.update(extra)
        state["jobs"][jid] = entry
        # the notification copy also carries the (unstored) description snippet
        new.append(dict(entry, id=jid, snippet=j.get("snippet", "")))
    return new
