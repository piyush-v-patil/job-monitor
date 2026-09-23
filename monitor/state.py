"""State persistence: each tracker's JSON file is its single source of truth.

Which file that is comes from the running profile (monitor/profiles.py):
docs/data/jobs.json for the software tracker, docs/data/supplychain.json for
the supply-chain one. The structure and every rule below are identical for
both - only the tier vocabulary and the role buckets differ, and this layer
never inspects either.

Structure:
{
  "version": 1,
  "updated": "2026-08-20T12:00:00Z",
  "jobs": {
    "<job_id>": {
      "company": str, "title": str,
      "tier": software: "intern|newgrad|experienced"
              supply chain: "intern|entry|mid|manager",
      "location": str, "url": str, "source": str,
      "first_seen": "YYYY-MM-DD", "status": "new|applied|skip|interview|rejected|closed",
      # best-effort enrichment; key is omitted entirely when the ATS has no value
      "posted_at": "YYYY-MM-DD", "comp": str, "employment_type": str,
      "workplace": "Remote|Hybrid|On-site", "department": str,
      "role": software: "ml-ai|data|security|devops-sre|mobile|frontend|
                        fullstack|backend|embedded|qa-test|solutions|software"
              supply chain: "demand-planning|supply-planning|inventory|
                        merch-planning|procurement|logistics|analytics|
                        program-mgmt|workforce|supply-chain",
      "yoe": int,            # lowest stated years-of-experience, when the posting says
      "deadline": "YYYY-MM-DD",  # application close date; very rarely published
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
import re
from datetime import datetime, timezone

# Default tracker file. Callers that run a non-default profile pass the path
# explicitly (see monitor/profiles.py); nothing else about the state layer
# differs between trackers, so the rest of this module never asks which one
# it is looking at.
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "jobs.json")


# The same posting reaches us twice when a company is both on a board we scan
# directly and listed by the Simplify aggregator. The ids differ (Greenhouse
# hands us a numeric id, Simplify only the link) and Simplify rewrites the
# title, so neither id nor title matches - but the ATS job number inside the
# URL is the same on both. That number is the posting's real identity.
ATS_ID = (
    re.compile(r"gh_jid=(\d{5,})", re.I),                       # greenhouse link-out
    re.compile(r"greenhouse\.io/[^/]+/jobs?/(\d{5,})", re.I),    # greenhouse canonical
    re.compile(r"/jobs?/(\d{5,})(?:$|[/?#])", re.I),                      # careers.<co>/jobs/123
    re.compile(r"lever\.co/[^/]+/([0-9a-f-]{36})", re.I),        # lever uuid
    re.compile(r"ashbyhq\.com/[^/]+/([0-9a-f-]{36})", re.I),     # ashby uuid
)


def canonical_key(company: str, url: str) -> str:
    """Identity of the underlying posting, independent of which source found it.

    Empty when the URL carries no recognisable ATS id, in which case the
    caller falls back to the per-source id and no de-duplication happens.
    """
    if not url:
        return ""
    for pattern in ATS_ID:
        m = pattern.search(url)
        if m:
            return f"{company.strip().lower()}#{m.group(1)}"
    return ""


# Board aggregators (JobSpy/LinkedIn) surface postings this repo also reaches
# through the employer's own ATS, under a different id and a different URL, so
# canonical_key finds nothing to match on and the job would be tracked - and
# notified - twice. These rows carry `soft_dedupe`, and are matched on what the
# two copies do agree about: who is hiring, for what, and where.
SOFT_STRIP = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh|sa|nv|ag|holdings"
    r"|group|technologies|technology)\b", re.I)
SOFT_NOISE = re.compile(r"[^a-z0-9 ]+")
# Req numbers and campus-cycle years differ between the two listings of one job
# ("Software Engineer (R12345)" vs "Software Engineer"), so they are dropped.
SOFT_TITLE_NOISE = re.compile(r"\(?\b[a-z]{0,3}[-_]?\d{4,}\b\)?", re.I)


def _soft_norm(text: str, strip_suffixes: bool = False) -> str:
    s = SOFT_NOISE.sub(" ", (text or "").lower())
    if strip_suffixes:
        s = SOFT_STRIP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def soft_key(company: str, title: str, location: str) -> str:
    """Identity of a posting by description rather than by id.

    Only the city is taken from the location: the same job reads "New York,
    NY" on LinkedIn and "New York, New York, United States" on Greenhouse, and
    matching the whole string would never fire. Empty when any part is missing,
    which leaves the posting to be tracked on its own - a missed merge costs a
    duplicate row, whereas a loose match would hide a real posting.
    """
    co = _soft_norm(company, strip_suffixes=True)
    ti = _soft_norm(SOFT_TITLE_NOISE.sub(" ", title or ""))
    city = _soft_norm((location or "").split(",")[0])
    if not co or not ti or not city:
        return ""
    return f"{co}#{ti}#{city}"


# A job id is read back out of the dashboard's markup, so it has to survive
# being written into an HTML attribute. Ids used to keep every character of the
# company name, which put an apostrophe inside "Steven's Capital Management"
# and broke that row's buttons. Anything outside [a-z0-9] becomes a hyphen.
SLUG = re.compile(r"[^a-z0-9]+")


def company_slug(company: str) -> str:
    return SLUG.sub("-", (company or "").lower()).strip("-") or "unknown"


def job_id(company: str, external_id: str = "", url: str = "") -> str:
    key = external_id.strip() or url.strip()
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{company_slug(company)}:{digest}"


def migrate_ids(state: dict) -> int:
    """Re-key entries stored under a pre-slug id. Returns how many moved.

    Only the company half of an id changes; the digest is derived from the
    posting's url or ATS id, neither of which this touches, so an entry can be
    renamed in place without re-fetching anything. Without this the scanner
    would stop recognising those postings and notify them a second time.

    Idempotent - an already-slugged id computes back to itself.
    """
    moved = 0
    for jid in list(state["jobs"]):
        entry = state["jobs"][jid]
        prefix, sep, digest = jid.rpartition(":")
        if not sep:
            continue
        want = f"{company_slug(entry.get('company') or prefix)}:{digest}"
        if want == jid:
            continue
        current = state["jobs"].get(want)
        if current is None:
            state["jobs"][want] = entry
        else:
            # both ids are already present: keep whichever the user has acted
            # on and fold in any field only the other one carried
            keep, drop = ((entry, current)
                          if entry.get("status", "new") != "new"
                          and current.get("status", "new") == "new"
                          else (current, entry))
            for k, v in drop.items():
                if v not in ("", None):
                    keep.setdefault(k, v)
            state["jobs"][want] = keep
        del state["jobs"][jid]
        moved += 1
    return moved


def load(path: str | None = None) -> dict:
    path = path or STATE_PATH
    if not os.path.exists(path):
        return {"version": 1, "updated": None, "jobs": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(state: dict, path: str | None = None) -> None:
    path = path or STATE_PATH
    state["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False, sort_keys=True)


ENRICH = ("posted_at", "comp", "employment_type", "workplace", "department",
          "role", "yoe", "deadline")


def source_health(state: dict, counts: dict) -> list:
    """Record this run's per-source yield; return sources that just went dark.

    A source that has produced postings before and now returns nothing is the
    failure mode that hides: the run still succeeds and the log still shows a
    tidy summary. Comparing against the recorded high-water mark turns that
    into something worth paging about.

    A source that has NEVER produced anything hides even better, because it
    cannot "go dark" - it has no high-water mark to fall from. Five big-tech
    fetchers sat at zero for weeks without ever tripping the check above. So a
    source with no successful run on record is reported too, once, and re-armed
    if it ever starts working.
    """
    hist = state.setdefault("sources", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    broke = []
    for name, n in counts.items():
        rec = hist.setdefault(name, {"best": 0, "last": 0, "last_ok": None})
        if n > 0:
            rec["last_ok"] = today
            rec.pop("reported_dead", None)   # working again: re-arm the alert
        elif rec.get("best", 0) >= 5 and rec.get("last", 0) > 0:
            # was healthy on the previous run, now empty
            broke.append({"name": name, "was": rec["last"], "since": rec.get("last_ok")})
        elif not rec.get("last_ok") and not rec.get("reported_dead"):
            # never once worked - report it a single time, not every 2 hours
            rec["reported_dead"] = today
            broke.append({"name": name, "was": 0, "since": None, "never": True})
        rec["best"] = max(rec.get("best", 0), n)
        rec["last"] = n
    return broke


def add_new(state: dict, jobs: list) -> list:
    """Add jobs not already tracked. Returns the list of newly added jobs.

    Existing entries are left alone except that missing enrichment fields are
    filled in (a job first seen before enrichment existed gets upgraded on a
    later scan). "status" is never written here.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new = []
    # index the postings already held by their source-independent identity, so
    # the same job arriving from a second source does not alert a second time
    seen = {}
    soft_seen = {}
    for k, e in state["jobs"].items():
        ck = canonical_key(e.get("company", ""), e.get("url", ""))
        if ck:
            seen.setdefault(ck, k)
        sk = soft_key(e.get("company", ""), e.get("title", ""), e.get("location", ""))
        if sk:
            soft_seen.setdefault(sk, k)
    # An aggregator row is the weaker copy of a posting: it carries the board's
    # URL rather than the employer's. Holding them back means that when both
    # copies arrive in one scan, the ATS row is the one that gets tracked and
    # the aggregator row folds into it, not the other way round.
    jobs = sorted(jobs, key=lambda j: bool(j.get("soft_dedupe")))
    for j in jobs:
        jid = job_id(j["company"], j.get("external_id", ""), j.get("url", ""))
        # not truthiness: yoe == 0 is a real answer ("0-2 years of experience")
        extra = {k: j[k] for k in ENRICH if j.get(k) not in ("", None)}
        ck = canonical_key(j["company"], j.get("url", ""))
        if ck and ck in seen and seen[ck] != jid:
            # already tracked under another source's id - enrich it, do not
            # add a second copy and do not notify
            entry = state["jobs"][seen[ck]]
            for k, v in extra.items():
                entry.setdefault(k, v)
            continue
        if jid in state["jobs"]:
            entry = state["jobs"][jid]
            for k, v in extra.items():          # backfill only what is absent
                entry.setdefault(k, v)
            continue
        sk = soft_key(j["company"], j["title"], j.get("location", ""))
        other = state["jobs"].get(soft_seen.get(sk, "")) if sk else None
        if other is not None and (j.get("soft_dedupe") or other.get("soft_dedupe")):
            # same posting, two listings. Enrich the tracked copy and stop.
            for k, v in extra.items():
                other.setdefault(k, v)
            if other.get("soft_dedupe") and not j.get("soft_dedupe"):
                # the employer's own listing has now turned up: take its link
                # over the board's, and stop treating the entry as the weaker
                # copy, so a later aggregator row folds into it as usual
                other["url"] = j.get("url") or other.get("url", "")
                other["source"] = j.get("source") or other.get("source", "")
                other.pop("soft_dedupe", None)
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
        if j.get("soft_dedupe"):
            entry["soft_dedupe"] = True
        entry.update(extra)
        state["jobs"][jid] = entry
        if ck:
            seen[ck] = jid
        if sk:
            soft_seen.setdefault(sk, jid)
        # the notification copy also carries the (unstored) description snippet
        new.append(dict(entry, id=jid, snippet=j.get("snippet", "")))
    return new


def dedupe(state: dict) -> int:
    """Collapse postings already stored twice under different source ids.

    Keeps the copy the user has interacted with, else the earliest seen, and
    folds any fields only the loser had onto the survivor. Returns how many
    were removed. Idempotent, so it is safe to run on every scan.
    """
    groups = {}
    for jid, e in state["jobs"].items():
        ck = canonical_key(e.get("company", ""), e.get("url", ""))
        if ck:
            groups.setdefault(ck, []).append(jid)
    removed = 0
    for ids in groups.values():
        if len(ids) < 2:
            continue
        # a marked entry outranks an unmarked one; otherwise oldest wins
        ids.sort(key=lambda i: (state["jobs"][i].get("status", "new") == "new",
                                state["jobs"][i].get("first_seen", "9999")))
        keep, rest = ids[0], ids[1:]
        for other in rest:
            for k, v in state["jobs"][other].items():
                if v not in ("", None):
                    state["jobs"][keep].setdefault(k, v)
            del state["jobs"][other]
            removed += 1
    return removed
