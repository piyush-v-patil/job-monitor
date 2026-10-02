"""Recover what the tracker can work out from what it already holds.

Two things a scan can only fix for postings it is looking at right now, which
leaves everything found earlier as it was. Both are recovered here, offline,
from the file itself - no board is contacted.

**Dates.** LinkedIn tags a search card two ways: "job-search-card__listdate"
once a posting is a day old, and "job-search-card__listdate--new" while it is
fresher than that. JobSpy only ever matched the first, so for as long as that
went unnoticed the postings arriving with no date were exactly the ones posted
in the last 24 hours (see _patch_linkedin_dates in fetchers/jobspy_board.py).
That is fixed for postings found from here on, and leaves a backlog the
dashboard cannot sort: 1,093 rows in the software tracker, 453 in the other.

The fix is also what makes the backlog recoverable. An undated row is undated
*because* LinkedIn called it less than a day old on the day we found it, so its
posting date is its `first_seen`, give or take a day. That is an inference
rather than a reading, so it was checked against LinkedIn's own "N days ago" on
18 undated postings: 17 were posted within 0-1 days of `first_seen` (12 the same
day, 5 the day before) and the 18th page stated no age at all. A day's error is
worth carrying to get a sortable feed but not worth hiding, so these rows are
flagged `posted_approx` and the dashboard prints them as "≈ posted ...". If a
board later states the real date, state.enrich replaces the derived one.

Only `jobspy-linkedin` rows qualify. Everywhere else a missing date means the
board never published one, and `first_seen` says nothing about when the job went
up - an ATS may have been sitting on it for a month before we arrived.

**Employer links.** A board row links to the board. state.employer_link finds
the employer's own posting for the same role among the rows this repo fetched
itself, and a scan applies it to the rows passing through it - but a LinkedIn
sweep only asks for the last 72 hours, so a posting found last week never comes
back and never gets the link. The same match is applied here to everything
stored.

Re-running is harmless: a row that has a date, or a link, is never touched.
Once the date fix has been live for a while, narrow the dates with
`--before <the day it went live>`, because an undated row from after that day is
undated for a different reason - LinkedIn served a card with no age on it - and
the inference does not hold for those.

Usage:  python -m monitor.backfill [--profile supplychain] [--dates | --links]
                                   [--before YYYY-MM-DD] [--dry-run]
"""
import argparse
import sys
from datetime import datetime, timezone

from . import profiles, state

# The source whose missing dates mean "posted in the last 24 hours".
SOURCE = "jobspy-linkedin"


def find_dates(jobs: dict, before: str) -> dict:
    """-> {job_id: the date it would be given}."""
    out = {}
    for jid, j in jobs.items():
        if j.get("source") != SOURCE or j.get("posted_at"):
            continue
        seen = j.get("first_seen") or ""
        if seen and seen <= before:
            out[jid] = seen
    return out


def find_links(jobs: dict) -> dict:
    """-> {job_id: the employer's own url for that posting}."""
    index = state.ats_index(jobs, [])
    out = {}
    for jid, j in jobs.items():
        if not j.get("soft_dedupe") or j.get("employer_url"):
            continue
        link = state.employer_link(j, index)
        if link:
            out[jid] = link
    return out


def apply_dates(jobs: dict, dates: dict) -> None:
    for jid, date in dates.items():
        jobs[jid]["posted_at"] = date
        jobs[jid]["posted_approx"] = True


def apply_links(jobs: dict, links: dict) -> None:
    # `url` is left exactly as the board gave it: it is what the closure sweep
    # probes (monitor/expire.py) and what canonical_key reads, and rewriting it
    # would let dedupe() delete one of the two rows.
    for jid, link in links.items():
        jobs[jid]["employer_url"] = link


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", choices=list(profiles.PROFILES), default="tech")
    ap.add_argument("--dates", action="store_true", help="dates only")
    ap.add_argument("--links", action="store_true", help="employer links only")
    ap.add_argument("--before", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    help="date only the postings first seen on or before this day "
                         "(default: today - see the module docstring)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    do_dates = args.dates or not args.links
    do_links = args.links or not args.dates

    profile = profiles.get(args.profile)
    st = state.load(profile.state_path)
    jobs = st["jobs"]
    board = [j for j in jobs.values() if j.get("soft_dedupe")]
    undated = [j for j in jobs.values()
               if j.get("source") == SOURCE and not j.get("posted_at")]
    print(f"{len(jobs)} tracked: {len(board)} from a board, {len(undated)} undated")

    dates = find_dates(jobs, args.before) if do_dates else {}
    links = find_links(jobs) if do_links else {}

    for jid, date in sorted(dates.items(), key=lambda kv: kv[1])[:8]:
        print(f"  ≈{date}  {jobs[jid]['company'][:24]:24s} {jobs[jid]['title'][:46]}")
    if len(dates) > 8:
        print(f"  ... and {len(dates) - 8} more dates")
    for jid, link in list(links.items())[:8]:
        print(f"  link    {jobs[jid]['company'][:24]:24s} {link[:62]}")
    if len(links) > 8:
        print(f"  ... and {len(links) - 8} more links")

    skipped = len(undated) - len(dates) if do_dates else 0
    print(f"\n{len(dates)} postings would be dated from first_seen"
          + (f" ({skipped} left alone: first seen after {args.before}, or never)"
             if skipped else "")
          + f"; {len(links)} would gain the employer's own link")
    if args.dry_run:
        print("Dry run: nothing saved.")
        return 0
    if not dates and not links:
        print("Nothing to do.")
        return 0
    apply_dates(jobs, dates)
    apply_links(jobs, links)
    state.save(st, profile.state_path)
    print(f"saved: {sum(1 for j in jobs.values() if j.get('posted_approx'))} derived date(s), "
          f"{sum(1 for j in board if j.get('employer_url'))} of {len(board)} board postings "
          f"now carrying the employer's link")
    return 0


if __name__ == "__main__":
    sys.exit(main())
