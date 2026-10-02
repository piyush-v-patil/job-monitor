"""Aggregator fetcher: JobSpy (LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter).

Every other fetcher in this package talks to one company's ATS. This one talks
to the job boards themselves, via https://github.com/speedyapply/JobSpy, which
is how LinkedIn becomes reachable at all: LinkedIn has no public jobs API, and
its own careers listings are not served by any ATS this repo can query. JobSpy
drives the boards' internal search endpoints and hands back one table.

What that buys, and what it costs:

  + Coverage no company registry can match. A LinkedIn search sweeps every
    employer at once, including the ones with no queryable board (Meta, most
    of finance, anyone on a bespoke careers page).
  - The boards rank by relevance, not completeness, so a search is a sample
    rather than a listing. `searches:` (handled in main.run_fetcher) is how
    the sample is widened: one term per job family, merged.
  - The same posting also reaches us through the company's own ATS, under a
    different id and URL. Rows here are marked `soft_dedupe`, which tells
    state.add_new to fold them into the ATS copy instead of tracking the job
    twice (see state.soft_key).
  - LinkedIn rate-limits hard, and blocks datacenter IPs more readily than
    residential ones. `proxies_env` names an environment variable holding a
    comma-separated proxy list, so a blocked runner is a secret away from
    working rather than a code change.
  - JobSpy loses the posting date on everything posted in the last 24 hours,
    which is the half of a LinkedIn sweep worth reading first. It is put back
    here; see _patch_linkedin_dates.

Config (config/companies*.yaml):

    - name: LinkedIn (JobSpy)
      fetcher: jobspy
      sites: [linkedin]          # any of linkedin|indeed|glassdoor|google|zip_recruiter
      location: United States
      results_wanted: 100        # per search term, per site
      hours_old: 72              # only postings newer than this
      fetch_description: false   # +1 request per job; see below
      proxies_env: JOBSPY_PROXIES
      searches:
        - software engineer intern
        - new grad software engineer

`fetch_description: true` pulls each posting's body, which is what
filters.parse_yoe reads to correct a tier the title got wrong. It costs one
extra request per job (~1.2s each on LinkedIn) and multiplies the rate-limit
exposure, so it is off by default and worth turning on only for the narrow,
high-value searches.
"""
import os
import re
import time
from datetime import datetime

# Boards that take a country hint, and the JobSpy argument carrying it.
_DEFAULT_SITES = ("linkedin",)

# JobSpy columns -> this repo's enrichment fields, where the board fills them.
_EMPLOYMENT = {
    "fulltime": "Full-time", "full-time": "Full-time", "parttime": "Part-time",
    "part-time": "Part-time", "contract": "Contract", "temporary": "Contract",
    "internship": "Intern", "intern": "Intern",
}


def _scrape_jobs():
    """Import JobSpy on first use.

    Kept out of module scope so that a missing dependency costs this one
    source rather than the whole scan: monitor.fetchers imports every module
    at startup, and an ImportError here would take the ATS fetchers down with
    it on any machine that skipped `pip install -r requirements.txt`.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError(
            "python-jobspy is not installed (pip install -r requirements.txt)"
        ) from e
    _patch_linkedin_dates()
    return scrape_jobs


# LinkedIn stamps a search card's age with one of two <time> classes:
#
#   <time class="job-search-card__listdate"      datetime="2026-09-24">4 days ago</time>
#   <time class="job-search-card__listdate--new" datetime="2026-09-29">3 hours ago</time>
#
# Both carry the same machine-readable datetime, but JobSpy reads the date off
# `find("time", class_="job-search-card__listdate")`, and BeautifulSoup matches
# class *tokens* - "job-search-card__listdate--new" is a different token, so it
# never matches. The postings that lose their date are therefore exactly the
# ones posted in the last 24 hours.
#
# Nothing about that is visible in a scan: the row still arrives, still passes
# the filters, still gets tracked. It only shows up on the dashboard, where
# "newest posted" has nothing to sort today's postings BY - they fall back to
# their discovery date, tie with every older posting found in the same sweep,
# and never reach the top of the list. 471 of the 565 LinkedIn rows found on
# 2026-09-29 arrived with no date, and not one row in the tracker was stamped
# as posted that day.
#
# So the date is re-read here, off the same card JobSpy has already parsed, and
# only where JobSpy left the field empty. A JobSpy release that fixes this
# upstream fills the field first and this never fires.
_LISTDATE = re.compile(r"job-search-card__listdate")


def _card_date(job_card):
    """The <time datetime> on a search card, whichever listdate class it wears."""
    tag = job_card.find("time", class_=_LISTDATE) if job_card else None
    if tag is None and job_card is not None:
        # Class renamed upstream: any <time datetime> on a search card is the
        # posting's date, so fall back to that rather than to nothing.
        tag = job_card.find("time", attrs={"datetime": True})
    if tag is None or not tag.get("datetime"):
        return None
    try:
        # .date(), not the datetime: JobSpy's model field is a `date`, and its
        # own rows are coerced to one on the way in. Assigning a datetime
        # instead leaves one column holding both types, which pandas cannot
        # sort - scrape_jobs then dies on every LinkedIn search.
        return datetime.strptime(tag["datetime"].strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _patch_linkedin_dates() -> None:
    """Fill in the date JobSpy drops on postings less than a day old.

    Wraps one JobSpy method rather than reimplementing the scraper: the
    original parses the card as usual, and only the empty date field is
    filled. A JobSpy that renames its internals makes this a no-op with a
    warning instead of a failed scan, which is the same behaviour as today.
    """
    try:
        from jobspy.linkedin import LinkedIn
    except ImportError:
        return
    if getattr(LinkedIn, "_fresh_date_patch", False):
        return
    original = getattr(LinkedIn, "_process_job", None)
    if original is None:
        print("  ! jobspy: LinkedIn._process_job is gone - postings from today "
              "will arrive without a date")
        return

    def _process_job(self, job_card, *a, **kw):
        post = original(self, job_card, *a, **kw)
        if post is not None and getattr(post, "date_posted", None) is None:
            fresh = _card_date(job_card)
            if fresh is not None:
                try:
                    post.date_posted = fresh
                except Exception:  # noqa: BLE001
                    pass          # model went read-only: undated, as before
        return post

    LinkedIn._process_job = _process_job
    LinkedIn._fresh_date_patch = True


def _s(value) -> str:
    """Cell -> clean string. pandas leaves NaN in every column a board skipped."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "nat", "none", "<na>"):
        return ""
    return text


def _date(value) -> str:
    """JobSpy hands back a datetime.date, or NaT where the board gave nothing."""
    text = _s(value)
    return text[:10] if text else ""


def _comp(row) -> str:
    """'$120,000 - $160,000 / yr' from whichever half of the range is present."""
    lo, hi = _s(row.get("min_amount")), _s(row.get("max_amount"))
    if not lo and not hi:
        return ""
    cur = _s(row.get("currency")) or "USD"
    sym = "$" if cur in ("USD", "CAD", "AUD") else ""

    def money(v):
        try:
            return f"{sym}{float(v):,.0f}"
        except (TypeError, ValueError):
            return f"{sym}{v}"

    span = f"{money(lo)} - {money(hi)}" if lo and hi else money(lo or hi)
    interval = _s(row.get("interval"))
    return f"{span} / {interval}" if interval else span


def _workplace(row) -> str:
    wfh = _s(row.get("work_from_home_type"))
    if wfh:
        return wfh.title()
    remote = row.get("is_remote")
    if remote is True or _s(remote).lower() == "true":
        return "Remote"
    return ""


def _proxies(c):
    """Read the proxy list out of the environment, never out of the config.

    A proxy URL carries credentials, and config/ is committed. The config
    names the variable; the value stays in Actions secrets.
    """
    var = c.get("proxies_env", "JOBSPY_PROXIES")
    raw = os.environ.get(var, "") if var else ""
    proxies = [p.strip() for p in raw.split(",") if p.strip()]
    return proxies or None


def jobspy(c):
    """c: see the module docstring. Returns this repo's raw job dicts."""
    scrape_jobs = _scrape_jobs()
    sites = [s.strip() for s in (c.get("sites") or _DEFAULT_SITES) if s.strip()]
    term = (c.get("search") or "").strip()
    fetch_desc = bool(c.get("fetch_description"))
    proxies = _proxies(c)

    kwargs = {
        "site_name": sites,
        "search_term": term or None,
        "location": c.get("location") or "United States",
        "results_wanted": int(c.get("results_wanted", 100)),
        "linkedin_fetch_description": fetch_desc,
        "description_format": "markdown",
        "verbose": 0,
    }
    if c.get("distance") is not None:
        kwargs["distance"] = int(c["distance"])
    if c.get("hours_old"):
        kwargs["hours_old"] = int(c["hours_old"])
    if c.get("is_remote") is not None:
        kwargs["is_remote"] = bool(c["is_remote"])
    if c.get("job_type"):
        kwargs["job_type"] = c["job_type"]
    if c.get("country") and any(s in ("indeed", "glassdoor") for s in sites):
        kwargs["country_indeed"] = c["country"]
    if proxies:
        kwargs["proxies"] = proxies
    if c.get("google_search_term"):
        kwargs["google_search_term"] = c["google_search_term"]

    df = scrape_jobs(**kwargs)
    # A board that matched nothing returns an empty frame, not an error.
    if df is None or not len(df):
        return []

    # Politeness between terms: main.run_fetcher calls this once per search,
    # back to back, and LinkedIn counts the whole burst against one IP.
    delay = float(c.get("delay_seconds", 2))

    out = []
    for row in df.to_dict("records"):
        company, title = _s(row.get("company")), _s(row.get("title"))
        url = _s(row.get("job_url"))
        if not company or not title or not url:
            continue
        site = _s(row.get("site")) or "jobspy"
        description = _s(row.get("description")) if fetch_desc else ""
        out.append({
            "company": company,
            "title": title,
            "location": _s(row.get("location")),
            # The board's own link-out to the employer's ATS, where it has one.
            # Preferred for applying; LinkedIn fills it only some of the time.
            "url": _s(row.get("job_url_direct")) or url,
            "external_id": _s(row.get("id")) or url,
            "source": f"jobspy-{site}",
            "posted_at": _date(row.get("date_posted")),
            "comp": _comp(row),
            "employment_type": _EMPLOYMENT.get(
                _s(row.get("job_type")).lower(), _s(row.get("job_type")).title()),
            "workplace": _workplace(row),
            "department": _s(row.get("job_function")),
            "snippet": description[:180].rstrip() + ("…" if len(description) > 180 else ""),
            "yoe": _yoe(description, title),
            # This posting is almost certainly also on the employer's own board.
            # Let state.add_new fold it into that copy rather than track both.
            "soft_dedupe": True,
        })

    if delay:
        time.sleep(delay)
    return out


def _yoe(description: str, title: str):
    """Years-of-experience from the body, when the body was fetched.

    Imported here rather than at module scope only to keep the import graph
    flat - filters imports nothing from fetchers, and this keeps it that way
    if that ever changes.
    """
    if not description:
        return None
    from .. import filters
    return filters.parse_yoe(description[:6000], title)
