"""Mark tracked postings the employer has stopped accepting applications for.

jobs.json accumulates: a posting is added when a board first shows it and
nothing ever takes it back out. That is right for discovery - a scan is a
relevance-ranked sample, so a posting missing from one sweep is usually just
below the fold - but it means the feed keeps showing roles that closed days
ago, and you find out by opening one and reading "No longer accepting
applications". Absence from a scan cannot be the signal either: the LinkedIn
source only ever asks for the last 72 hours, so every posting it found leaves
that window while still open.

So closure is established by asking the posting itself. LinkedIn's public job
page carries the state in its markup - a closed posting grows a `closed-job`
figure in the top card - and a deleted one 404s. Both are affirmative answers;
anything else (a rate-limited response, an auth wall, a layout this does not
recognise) yields no verdict and the posting is left exactly as it was.

What gets written:

    "closed_at":  "YYYY-MM-DD"   the day the closure was observed
    "checked_at": "YYYY-MM-DD"   the day the posting was last probed
    "posted_at":  "YYYY-MM-DD"   only when the row had no date at all, read off
    "posted_approx": true        the page's own "6 days ago" and marked derived

`status` is never touched. It belongs to the dashboard - it is what you marked,
and a job you applied to before it closed is still a job you applied to - and
monitor/merge.py treats it as user-owned, so a closure written into it would be
dropped the first time this run lost a push race. `closed_at` is a fact about
the posting, it merges like any other enrichment field, and the dashboard reads
it to drop the row out of the open feed while keeping it under "Closed".

Budget: one request per posting, ~1.2s apart, so a full pass over the ~4,000
LinkedIn postings would take over an hour. Each run instead takes the postings
whose information is oldest (never probed first, then longest since), which
makes a daily run a rotation rather than a sweep - and re-probes a posting that
was open last week rather than one checked this morning. Nothing already marked
closed is probed again.

For scale: a random sample of 200 tracked LinkedIn postings, taken the day this
was written, found 21 already closed or deleted - about 10% of the feed, and not
concentrated in the old end of it.

Usage:
  python -m monitor.expire                      # default profile, 500 postings
  python -m monitor.expire --limit 2000         # a longer pass
  python -m monitor.expire --dry-run            # report, write nothing
  python -m monitor.expire --profile supplychain
"""
import argparse
import re
import sys
import time
from datetime import datetime, timezone

import requests

from . import profiles, state
from .fetchers.http import UA, rel_date

# A closed LinkedIn posting renders this in place of the apply button:
#
#   <figure class="closed-job closed-job__flavor topcard__flavor-row">
#     <span class="closed-job__icon ..."></span>
#     <figcaption class="closed-job__flavor--closed">No longer accepting
#     applications</figcaption>
#   </figure>
#
# The class is what is matched, not the sentence. The sentence also turns up in
# job descriptions ("we are no longer accepting applications for the 2026
# cohort"), and a description is the one part of the page the employer writes.
CLOSED = (
    re.compile(r"closed-job__flavor--closed", re.I),
    re.compile(r'class="[^"]*\bclosed-job\b', re.I),
)

# Proof that what came back is the job page at all. LinkedIn answers a
# throttled or logged-out request with a sign-in wall that has no apply state
# in it, and reading "no closed marker" off that page would mark every posting
# in the tracker open; reading a 200 as gone would mark them all closed. So a
# response that does not carry the top card gets no verdict at all.
JOB_PAGE = re.compile(r"top-card-layout|topcard__title", re.I)

# Hosts this knows how to ask. A posting on any other host is left alone
# rather than guessed at: every board words its closed state differently, and a
# wrong guess here silently hides a job you could still apply to.
SUPPORTED = ("linkedin.com",)

OPEN, GONE, CLOSED_STATE, UNKNOWN = "open", "gone", "closed", "unknown"


def supported(job: dict) -> bool:
    url = job.get("url", "")
    return any(host in url for host in SUPPORTED)


def candidates(jobs: dict, limit: int) -> list:
    """The postings worth probing this run, oldest information first.

    A posting already known to be closed is skipped: the answer does not
    change, and re-asking would spend the budget on the part of the tracker
    that is already settled.
    """
    pending = [(jid, j) for jid, j in jobs.items()
               if supported(j) and not j.get("closed_at")]
    # Never probed sorts ahead of probed (""), then least recently probed. This
    # is a rotation, not a hunt: a 200-posting random sample found closures
    # spread evenly across ages (3 of 7 postings from the previous day, 1 of 6
    # from eight days before), because the accounts that close fastest are the
    # high-volume reposters. So age only breaks ties between postings whose
    # information is equally old.
    pending.sort(key=lambda kv: (kv[1].get("checked_at") or "",
                                 kv[1].get("posted_at") or kv[1].get("first_seen") or ""))
    return pending[:limit] if limit else pending


def session() -> requests.Session:
    """A browser-shaped session: these are HTML pages, not an API."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


# The same page states the posting's age, as a phrase rather than a date:
#   <span class="posted-time-ago__text topcard__flavor--metadata">6 days ago</span>
# It is read off the response this run already has, so a date costs nothing
# here, and it is the standing answer to a row that reached the tracker undated
# for any reason - a card with no age on it, a board that stopped stating one.
AGE = re.compile(r"posted-time-ago__text[^>]*>\s*(.*?)\s*</span>", re.S)


def read(s: requests.Session, url: str, timeout: int = 20) -> tuple:
    """-> (OPEN | GONE | CLOSED_STATE | UNKNOWN, page body). Never raises."""
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return UNKNOWN, ""
    if r.status_code in (404, 410):
        return GONE, ""                  # the posting has been taken down
    if r.status_code != 200:
        return UNKNOWN, ""               # 429/999/5xx: throttled, not an answer
    body = r.text
    if not JOB_PAGE.search(body):
        return UNKNOWN, body             # sign-in wall, or a layout change
    return (CLOSED_STATE if any(p.search(body) for p in CLOSED) else OPEN), body


def probe(s: requests.Session, url: str, timeout: int = 20) -> str:
    """The verdict alone, for callers with no use for the page."""
    return read(s, url, timeout)[0]


def page_date(body: str) -> str:
    """'6 days ago' on the page -> 'YYYY-MM-DD', or "" if it says nothing."""
    m = AGE.search(body or "")
    return rel_date(re.sub(r"\s+", " ", m.group(1))) if m else ""


def run(jobs: dict, limit: int, delay: float, s=None, today=None) -> dict:
    """Probe up to `limit` postings and write the verdicts onto `jobs`.

    Returns a tally. Stops early once the responses stop being answers: a
    string of UNKNOWNs means LinkedIn is throttling this IP, and continuing
    would spend hundreds of requests to deepen the block.
    """
    s = s or session()
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tally = {OPEN: 0, GONE: 0, CLOSED_STATE: 0, UNKNOWN: 0}
    closed, blind, dated = [], 0, 0
    picked = candidates(jobs, limit)
    for i, (jid, j) in enumerate(picked):
        verdict, body = read(s, j["url"])
        tally[verdict] += 1
        if verdict == UNKNOWN:
            blind += 1
            # 10 in a row with nothing recognisable back is a block, not a run
            if blind >= 10:
                print(f"  ! stopping after {i + 1} probes: 10 unreadable "
                      f"responses in a row (rate limited, or the page changed)")
                break
        else:
            blind = 0
            # checked_at records the probe, so the next run rotates past this
            # posting instead of asking about it again
            j["checked_at"] = today
            # a row that arrived with no date gets one from the page's own
            # "N days ago", marked derived: a phrase is not a stated date
            if not j.get("posted_at"):
                stated = page_date(body)
                if stated:
                    j["posted_at"] = stated
                    j["posted_approx"] = True
                    dated += 1
            if verdict in (GONE, CLOSED_STATE):
                j["closed_at"] = today
                closed.append((jid, j, verdict))
        if delay and i + 1 < len(picked):
            time.sleep(delay)
    tally["picked"] = len(picked)
    tally["closed_rows"] = closed
    tally["dated"] = dated
    return tally


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", choices=list(profiles.PROFILES), default="tech")
    ap.add_argument("--limit", type=int, default=500,
                    help="postings to probe this run (0 = every one, slow)")
    ap.add_argument("--delay", type=float, default=1.2,
                    help="seconds between requests (LinkedIn throttles bursts)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    profile = profiles.get(args.profile)
    st = state.load(profile.state_path)
    jobs = st["jobs"]
    if not jobs:
        print("nothing tracked yet")
        return 0

    checkable = [j for j in jobs.values() if supported(j)]
    already = sum(1 for j in checkable if j.get("closed_at"))
    print(f"{len(jobs)} tracked, {len(checkable)} on a board this can ask "
          f"({already} already known closed); probing up to {args.limit or 'all'} "
          f"at {args.delay}s apart...")

    t = run(jobs, args.limit, args.delay)
    for jid, j, verdict in t["closed_rows"]:
        mark = "gone" if verdict == GONE else "closed"
        # a posting you had already acted on keeps its mark; the closure is
        # still recorded, because "applied, and it closed" is worth knowing
        acted = "" if j.get("status", "new") == "new" else f" [{j['status']}]"
        print(f"  {mark}{acted}: {j['company']} - {j['title'][:60]}")

    print(f"\n{t['picked']} probed -> {t[CLOSED_STATE]} no longer accepting, "
          f"{t[GONE]} taken down, {t[OPEN]} still open, {t[UNKNOWN]} no answer"
          + (f"; {t['dated']} undated posting(s) picked up a date" if t["dated"] else ""))
    if args.dry_run:
        print("Dry run: nothing saved.")
        return 0
    if not t["closed_rows"] and not t[OPEN]:
        print("No verdicts to save.")
        return 0
    state.save(st, profile.state_path)
    still_open = sum(1 for j in jobs.values()
                     if j.get("status", "new") == "new" and not j.get("closed_at"))
    print(f"saved: {still_open} postings still open and unactioned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
