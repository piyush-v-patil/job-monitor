"""Find the employer's own posting for LinkedIn rows, on the employer's own board.

A LinkedIn row links to LinkedIn, and LinkedIn no longer hands out the apply
link without a login (the guest page's `applyUrl` is gone, and JobSpy never
read it anyway). state.employer_link can only link a row to a posting this
repo already fetched, and most companies are scanned for a handful of search
terms, so a LinkedIn role at Crowe or Cisco usually has no copy to link to -
even though we know exactly where Crowe and Cisco post their jobs.

This asks those boards directly. LinkedIn itself is never contacted.

  * Which board: the company registry (config/companies*.yaml) first, then the
    board URLs of postings already tracked for that company - a Simplify row
    pointing at crowe.wd12.myworkdayjobs.com/en-US/external_careers is enough
    to know where Crowe's own listings live.
  * What is read: Greenhouse, Lever and Ashby publish the whole board in one
    request, so it is read once. Workday only searches, so each LinkedIn title
    is searched for (one request, the top 20 hits).
  * What is accepted: the same title in the row's own city, or filed under
    no city ("3 Locations", "Remote") - and only one such posting. Stricter
    than state.employer_link, because a top-20 search is not the whole board
    (see match). Two offices advertising the same title get nothing.

A row that was searched is stamped `employer_checked`, found or not, and is not
searched again for RECHECK_DAYS. Requests are paced and capped per run, and a
board that errors is skipped for this run without stamping its rows, so they
are tried again next time.

Usage:  python -m monitor.resolve [--profile supplychain] [--limit 150]
                                  [--days 30] [--dry-run]
"""
import argparse
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import yaml

from . import names, profiles, state
from .fetchers import generic
from .fetchers.http import session

FETCHERS = ("greenhouse", "lever", "ashby", "workday")
RECHECK_DAYS = 7
PACE_S = 1.0

# Board URLs of postings already tracked -> where that company posts.
BOARD_URL = (
    ("greenhouse", re.compile(r"//(?:job-)?boards\.greenhouse\.io/(?!embed/)([\w-]+)/jobs?/", re.I)),
    ("greenhouse", re.compile(r"//(?:job-)?boards\.greenhouse\.io/embed/job_app\?(?:[^#]*&)?for=([\w-]+)", re.I)),
    ("lever", re.compile(r"//jobs\.lever\.co/([\w.-]+)/[0-9a-f-]{36}", re.I)),
    ("ashby", re.compile(r"//jobs\.ashbyhq\.com/([^/?#]+)/[0-9a-f-]{36}", re.I)),
)
WD_JOBS = re.compile(
    r"//((\w[\w-]*)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/job/", re.I)
WD_SITE = re.compile(
    r"//(wd\d+\.myworkdaysite\.com)/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([\w-]+)/([\w-]+)/job/", re.I)


# state's soft-match suffixes, plus the partnership ones: Simplify files Crowe
# as "Crowe LLP" and LinkedIn as "Crowe". Only used to find the board; the
# match itself files both sides under the LinkedIn row's own name.
COMPANY_STRIP = names.suffix_pattern("technologies", "technology", "llp", "lp")


def company_key(name: str) -> str:
    return names.normalize(name or "", COMPANY_STRIP)


def board_from_url(url: str) -> dict | None:
    """The board a posting URL lives on, as a fetcher config (without a name)."""
    for fetcher, pat in BOARD_URL:
        m = pat.search(url or "")
        if m:
            return {"fetcher": fetcher, "token": m.group(1)}
    m = WD_JOBS.search(url or "")
    if m:
        host, tenant, site = m.groups()
        return {"fetcher": "workday", "host": host.lower(), "tenant": tenant.lower(), "site": site}
    m = WD_SITE.search(url or "")
    if m:
        host, tenant, site = m.groups()
        return {"fetcher": "workday", "host": host.lower(), "tenant": tenant, "site": site,
                "url_prefix": f"https://{host.lower()}/en-US/recruiting/{tenant}/{site}"}
    return None


def board_id(b: dict) -> str:
    return (f"workday:{b['host']}/{b['tenant']}/{b['site']}" if b["fetcher"] == "workday"
            else f"{b['fetcher']}:{b['token']}")


def registry(path: str) -> list:
    """Every company entry in a config/companies*.yaml, whichever tier it is under."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return [c for v in cfg.values() if isinstance(v, list) for c in v if isinstance(c, dict)]


def boards(companies: list, jobs: dict) -> dict:
    """{company key: board config}. The registry wins; otherwise the board most of
    that company's tracked postings link to."""
    counts, configs = {}, {}
    for j in jobs.values():
        if j.get("soft_dedupe"):
            continue
        b, key = board_from_url(j.get("url", "")), company_key(j.get("company", ""))
        if b and key:
            counts.setdefault(key, Counter())[board_id(b)] += 1
            configs[board_id(b)] = b
    out = {key: dict(configs[ids.most_common(1)[0][0]]) for key, ids in counts.items()}
    keep = ("fetcher", "token", "host", "tenant", "site", "url_prefix", "facets")
    for c in companies:
        if c.get("fetcher") in FETCHERS and company_key(c.get("name", "")):
            out[company_key(c["name"])] = {k: c[k] for k in keep if k in c}
    return out


def candidates(jobs: dict, known: dict, today: str, days: int = 30) -> list:
    """LinkedIn rows worth a search, newest first: no employer link yet, still open,
    first seen in the last `days`, not searched in the last RECHECK_DAYS, and at a
    company whose board we know."""
    day = datetime.strptime(today, "%Y-%m-%d")
    since = (day - timedelta(days=days)).strftime("%Y-%m-%d")
    recheck = (day - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%d")
    out = [jid for jid, j in jobs.items()
           if j.get("soft_dedupe") and not j.get("employer_url")
           and j.get("status") != "closed" and not j.get("closed_at")
           and (j.get("first_seen") or "") >= since
           and (j.get("employer_checked") or "") <= recheck
           and company_key(j.get("company", "")) in known]
    return sorted(out, key=lambda jid: jobs[jid].get("first_seen") or "", reverse=True)


def search_text(title: str) -> str:
    """A title as a Workday search: without the req numbers boards append."""
    return " ".join(state.SOFT_TITLE_NOISE.sub(" ", title or "").split())


def fetch(board: dict, title: str) -> list:
    """The postings on `board` that could be `title`: the whole board where the board
    publishes one, else the top hits of a search for the title (Workday)."""
    c = dict(board, name="")
    if board["fetcher"] == "workday":
        url = f"https://{board['host']}/wday/cxs/{board['tenant']}/{board['site']}/jobs"
        return generic._workday_pass(session(), url, dict(c, max_results=20), search_text(title))
    return getattr(generic, board["fetcher"])(c)


# Boards file one place several ways: "Santa Clara, CA - USA (Offices)",
# "US-VA-Dahlgren", "United States-North Carolina-Morrisville", "Hub - San
# Francisco", "New York, NY; Boston, MA". Each piece is compared on its own, so
# "York" never matches inside "New York".
PLACE_SPLIT = re.compile(r"[,;/|()\u2022-]")


def same_place(row_location: str, location: str) -> bool:
    """The posting is filed under the row's city, or under no city at all."""
    other = state._city(location)
    if state.NON_CITY.match(other):
        return True
    city = state._city(row_location)
    if not city or state.NON_CITY.match(city):
        return False
    return city == other or city in {state._soft_norm(p) for p in PLACE_SPLIT.split(location or "")}


def match(row: dict, postings: list, whole_board: bool) -> str:
    """The employer's own posting for `row`, or "" unless it cannot be another req.

    Stricter than state.employer_link, which can trust "one URL for this title"
    because it looks at everything tracked. A board read here may be a top-20
    search, where the one "Software Engineer II" in the results is as likely to
    be in El Segundo as in the row's Cambridge. So the posting must be in the
    row's city or name no city ("2 Locations", "Remote") - and be the only such
    one. The one-URL rule survives only for a row that names no city itself, and
    only against a whole board, where nothing is missing from the list.
    """
    rk = state.role_key(row.get("company", ""), row.get("title", ""))
    same = [p for p in postings if p.get("url")
            and state.role_key(row.get("company", ""), p.get("title", "")) == rk]
    near = {p["url"] for p in same if same_place(row.get("location", ""), p.get("location", ""))}
    if len(near) == 1:
        return next(iter(near))
    urls = {p["url"] for p in same}
    if (not near and whole_board and len(urls) == 1
            and state.NON_CITY.match(state._city(row.get("location", "")))):
        return next(iter(urls))
    return ""


class OutOfRequests(Exception):
    pass


class Resolver:
    """Reads boards for rows, at most `limit` requests a run, `pace` seconds apart.
    A board read once is reused for every row at that company; a board that errors
    is not asked again this run."""

    def __init__(self, limit: int, fetch=fetch, pace: float = PACE_S):
        self.limit, self.fetch, self.pace = limit, fetch, pace
        self.cache: dict = {}
        self.failed: dict = {}
        self.requests = Counter()           # "board" / "search" -> count

    def postings(self, board: dict, title: str) -> list | None:
        bid = board_id(board)
        if bid in self.failed:
            return None
        key = bid + ("#" + search_text(title).lower() if board["fetcher"] == "workday" else "")
        if key in self.cache:
            return self.cache[key]
        if sum(self.requests.values()) >= self.limit:
            raise OutOfRequests
        if self.requests and self.pace:
            time.sleep(self.pace)
        self.requests["search" if board["fetcher"] == "workday" else "board"] += 1
        try:
            self.cache[key] = self.fetch(board, title)
        except Exception as e:  # noqa: BLE001 - one board never stops the run
            self.failed[bid] = f"{type(e).__name__}: {e}"[:160]
            return None
        return self.cache[key]


def resolve(jobs: dict, known: dict, today: str, resolver: Resolver, days: int = 30):
    """-> ({job_id: employer url}, [job ids searched])."""
    links, checked = {}, []
    for jid in candidates(jobs, known, today, days):
        row = jobs[jid]
        board = known[company_key(row.get("company", ""))]
        try:
            posts = resolver.postings(board, row.get("title", ""))
        except OutOfRequests:
            break
        if posts is None:
            continue                         # board unreachable: try again next run
        checked.append(jid)
        link = match(row, posts, whole_board=board["fetcher"] != "workday")
        if link:
            links[jid] = link
    return links, checked


def apply(jobs: dict, links: dict, checked: list, today: str) -> None:
    # only employer_url: `url` is what the closure sweep probes and what
    # de-duplication reads (see backfill.apply_links)
    for jid in checked:
        jobs[jid]["employer_checked"] = today
    for jid, link in links.items():
        jobs[jid]["employer_url"] = link


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", choices=list(profiles.PROFILES), default="tech")
    ap.add_argument("--limit", type=int, default=150, help="most board requests this run")
    ap.add_argument("--days", type=int, default=30, help="only rows first seen this recently")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    profile = profiles.get(args.profile)
    st = state.load(profile.state_path)
    jobs = st["jobs"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    known = boards(registry(profile.config_path), jobs)
    todo = candidates(jobs, known, today, args.days)
    print(f"{len(known)} company boards known; {len(todo)} LinkedIn rows to look up "
          f"(first seen in the last {args.days} days, not searched in the last {RECHECK_DAYS})")

    resolver = Resolver(args.limit)
    links, checked = resolve(jobs, known, today, resolver, args.days)
    for jid, link in list(links.items())[:10]:
        print(f"  link  {jobs[jid]['company'][:22]:22s} {jobs[jid]['title'][:40]:40s} {link[:70]}")
    if len(links) > 10:
        print(f"  ... and {len(links) - 10} more")
    for bid, err in resolver.failed.items():
        print(f"  error {bid}: {err}")
    print(f"\n{resolver.requests['board']} board(s) read, {resolver.requests['search']} search(es); "
          f"{len(checked)} rows looked up: {len(links)} linked, {len(checked) - len(links)} no safe match"
          + (f"; {len(todo) - len(checked)} left for the next run" if len(todo) > len(checked) else ""))
    if args.dry_run:
        print("Dry run: nothing saved.")
        return 0
    if not checked:
        return 0
    apply(jobs, links, checked, today)
    state.save(st, profile.state_path)
    print(f"saved: {sum(1 for j in jobs.values() if j.get('soft_dedupe') and j.get('employer_url'))} "
          f"board postings now carry the employer's link")
    return 0


if __name__ == "__main__":
    sys.exit(main())
