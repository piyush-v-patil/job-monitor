"""State persistence: docs/data/jobs.json is the single source of truth.

Structure:
{
  "version": 1,
  "updated": "2026-08-20T12:00:00Z",
  "jobs": {
    "<job_id>": {
      "company": str, "title": str, "tier": "intern|newgrad|experienced",
      "location": str, "url": str, "source": str,
      "first_seen": "YYYY-MM-DD", "status": "new|applied|skip|interview|rejected|closed"
    }
  }
}

The dashboard edits only the "status" field; the scanner only adds new ids
and never overwrites an existing entry, so user edits are always preserved.
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


def add_new(state: dict, jobs: list) -> list:
    """Add jobs not already tracked. Returns the list of newly added jobs."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new = []
    for j in jobs:
        jid = job_id(j["company"], j.get("external_id", ""), j.get("url", ""))
        if jid in state["jobs"]:
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
        state["jobs"][jid] = entry
        new.append(dict(entry, id=jid))
    return new
