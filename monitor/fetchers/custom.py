"""Company-specific fetchers for big tech careers APIs.

These use unofficial-but-public JSON endpoints that back each company's own
careers site. They can change without notice — if one starts failing, check
the network tab of the careers page and update the endpoint.
"""
import html
import re

from .. import filters
from .http import session, get_json, get_text, post_json, iso_date, clean_text


def _sched(value: str) -> str:
    """Amazon job_schedule_type -> the shared vocabulary."""
    v = str(value or "").lower()
    return "Full-time" if "full" in v else ("Part-time" if "part" in v else "")


def amazon(c):
    s = session()
    url = ("https://www.amazon.jobs/en/search.json?result_limit=100&sort=recent"
           "&category%5B%5D=software-development&country%5B%5D=USA"
           f"&base_query={c.get('search', 'software engineer').replace(' ', '+')}")
    data = get_json(s, url)
    out = []
    for j in data.get("jobs", []):
        out.append({
            "company": "Amazon",
            "title": j.get("title", ""),
            "location": j.get("normalized_location", "") or j.get("location", ""),
            "url": "https://www.amazon.jobs" + j.get("job_path", ""),
            "external_id": str(j.get("id_icims", "") or j.get("id", "")),
            "source": "amazon.jobs",
            "posted_at": iso_date(j.get("posted_date")),
            "employment_type": "Intern" if j.get("is_intern") else _sched(
                j.get("job_schedule_type", "")),
            "department": j.get("job_category", "") or j.get("business_category", ""),
            "snippet": clean_text(j.get("description_short", "") or j.get("description", "")),
            "yoe": filters.parse_yoe(
                (j.get("basic_qualifications", "") or "") + " " + (j.get("description", "") or ""),
                j.get("title", "")),
        })
    return out


def microsoft(c):
    s = session()
    out = []
    for pg in (1, 2):
        url = ("https://gcsservices.careers.microsoft.com/search/api/v1/search"
               f"?q={c.get('search', 'software engineer').replace(' ', '%20')}"
               f"&lc=United%20States&l=en_us&pg={pg}&pgSz=100&o=Relevance&flt=true")
        data = get_json(s, url)
        jobs = (((data.get("operationResult") or {}).get("result") or {}).get("jobs")) or []
        if not jobs:
            break
        for j in jobs:
            props = j.get("properties") or {}
            out.append({
                "company": "Microsoft",
                "title": j.get("title", ""),
                "location": props.get("primaryLocation", "") or ", ".join(
                    props.get("locations", []) or []),
                "url": f"https://jobs.careers.microsoft.com/global/en/job/{j.get('jobId','')}",
                "external_id": str(j.get("jobId", "")),
                "source": "microsoft careers",
                "posted_at": iso_date(props.get("postingDate")),
                "employment_type": props.get("employmentType", ""),
                "workplace": ("Remote" if str(props.get("workSiteFlexibility", "")).lower()
                              .startswith("up to 100") else ""),
                "department": props.get("discipline", "") or props.get("profession", ""),
                "snippet": clean_text(props.get("description", "")),
            })
    return out


# Google retired the careers JSON API (v3/search now 404s). The results page
# is server-rendered though, so the listing is read straight out of the HTML.
# Each card carries the id and title on its "Learn more" anchor and the
# locations in a preceding <p class="l103df">.
GOOGLE_CARD = re.compile(
    r'href="(jobs/results/(\d+)-[^"?]*)[^"]*"\s+aria-label="Learn more about ([^"]+)"')
GOOGLE_LOC = re.compile(r'<p class="l103df">(.*?)</p>', re.S)


def _text(fragment: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def google(c):
    """Scrape the careers results page - sorted newest-first, 20 per page."""
    s = session()
    out, seen = [], set()
    for page in range(1, int(c.get("pages", 5)) + 1):
        url = ("https://www.google.com/about/careers/applications/jobs/results/"
               f"?q={c.get('search', 'software engineer').replace(' ', '%20')}"
               f"&location=United%20States&sort_by=date&page={page}")
        markup = get_text(s, url)
        cards = list(GOOGLE_CARD.finditer(markup))
        if not cards:
            break
        for m in cards:
            jid = m.group(2)
            if jid in seen:
                continue
            seen.add(jid)
            # the card's location paragraph is the last one before its anchor
            before = GOOGLE_LOC.findall(markup[: m.start()])
            loc = _text(before[-1]) if before else ""
            # "Google | Sunnyvale, CA, USA" and "YouTube | San Bruno, CA, USA"
            loc = loc.split("|")[-1].strip()
            out.append({
                "company": "Google",
                "title": html.unescape(m.group(3)),
                "location": loc,
                "url": ("https://www.google.com/about/careers/applications/"
                        + m.group(1)),
                "external_id": jid,
                "source": "google careers",
            })
    return out


APPLE_API = "https://jobs.apple.com/api/v1"
# The dates the search response is asked to render. Apple's own site sends
# these and the endpoint answers an empty result set without them.
APPLE_FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}


def apple(c):
    """c: {name, search?, max_pages?}  ->  jobs.apple.com role search

    Two things about this endpoint cost a while to find, and both make it
    answer `{"res":{"searchResults":[],"totalRecords":0}}` rather than an
    error, which is why the source sat at zero without ever looking broken:

      - the API moved from /api/role/search to /api/v1/search, and the old
        path now redirects into apple.com's 404 page;
      - `filters` must carry only the filters you are actually using. Apple's
        own code builds the object by merging in each filter *if truthy*, so
        sending the full set with empty arrays - the obvious reading of their
        UI state - matches nothing at all. `format` is likewise required.

    A CSRF token comes from /api/v1/CSRFToken as a response header, against
    the cookies the search page sets, so the warm-up request is load-bearing.
    """
    s = session()
    s.headers.update({
        "Accept": "application/json", "Content-Type": "application/json",
        "Referer": "https://jobs.apple.com/en-us/search",
        "Origin": "https://jobs.apple.com",
    })
    s.get("https://jobs.apple.com/en-us/search", timeout=30)   # cookies
    token = s.get(f"{APPLE_API}/CSRFToken", timeout=30).headers.get("X-Apple-CSRF-Token")
    if token:
        s.headers["X-Apple-CSRF-Token"] = token

    out, pages = [], int(c.get("max_pages", 8))
    for page in range(1, pages + 1):
        data = post_json(s, f"{APPLE_API}/search", json={
            "query": c.get("search", "software engineer"),
            # postLocation-USA is Apple's own id for the whole country; their
            # board is global and the rest of the pipeline is US-only
            "filters": {"locations": ["postLocation-USA"]},
            "page": page, "locale": "en-us", "sort": "newest",
            "format": APPLE_FORMAT,
        })
        rows = ((data.get("res") or {}).get("searchResults")) or []
        for j in rows:
            locs = j.get("locations") or []
            team = j.get("team") or {}
            out.append({
                "company": "Apple",
                "title": j.get("postingTitle", ""),
                "location": "; ".join(filter(None, (l.get("name", "") for l in locs[:3]))),
                "country": (locs[0].get("countryName", "") if locs else ""),
                "url": ("https://jobs.apple.com/en-us/details/"
                        f"{j.get('positionId','')}/{j.get('transformedPostingTitle','')}"),
                "external_id": str(j.get("positionId", "")),
                "source": "jobs.apple.com",
                "posted_at": iso_date(j.get("postDateInGMT") or j.get("postingDate")),
                "department": team.get("teamName", ""),
                "snippet": clean_text(j.get("jobSummary", "")),
                "yoe": filters.parse_yoe(clean_text(j.get("jobSummary", ""), 6000),
                                         j.get("postingTitle", "")),
            })
        if not rows:
            break
    return out


def tesla(c):
    s = session()
    data = get_json(s, "https://www.tesla.com/cua-api/apps/careers/state")
    listings = (data.get("listings") or [])
    lookup = data.get("lookup") or {}
    locations = lookup.get("locations") or {}
    departments = lookup.get("departments") or {}
    out = []
    for j in listings:
        dep = str(j.get("dp", ""))
        dep_name = str(departments.get(dep, ""))
        if "engineering" not in dep_name.lower() and "information technology" not in dep_name.lower():
            continue
        loc = locations.get(str(j.get("l", "")), "")
        loc_str = loc if isinstance(loc, str) else (loc or {}).get("name", "")
        out.append({
            "company": "Tesla",
            "title": j.get("t", ""),
            "location": loc_str,
            "url": f"https://www.tesla.com/careers/search/job/{j.get('id','')}",
            "external_id": str(j.get("id", "")),
            "source": "tesla.com",
        })
    return out


def uber(c):
    s = session()
    s.headers["x-csrf-token"] = "x"  # required magic value for this endpoint
    body = {
        "params": {
            "text": c.get("search", "software engineer"),
            "location": [{"country": "USA"}],
        },
        "limit": 100, "page": 0,
    }
    data = post_json(
        s, "https://www.uber.com/api/loadSearchJobsResults?localeCode=en", json=body)
    results = ((data.get("data") or {}).get("results")) or []
    out = []
    for j in results:
        loc = j.get("location") or {}
        all_loc = [loc] + (j.get("allLocations") or [])
        loc_str = "; ".join(
            f"{x.get('city','')}, {x.get('region','')}" for x in all_loc[:3] if x)
        out.append({
            "company": "Uber",
            "title": j.get("title", ""),
            "location": loc_str,
            "country": loc.get("countryName", "") or loc.get("country", ""),
            "url": f"https://www.uber.com/global/en/careers/list/{j.get('id','')}/",
            "external_id": str(j.get("id", "")),
            "source": "uber.com",
        })
    return out


def walmart(c):
    """c: {name, search?, max_results?}  ->  careers.walmart.com search-ai API

    The Workday CXS endpoint Walmart used to expose now answers 422. Their
    careers site talks to this hybrid-search service instead, which also
    carries pay range and posting date.
    """
    s = session()
    s.headers.update({"Referer": "https://careers.walmart.com/results",
                      "Origin": "https://careers.walmart.com"})
    out, page, size = [], 0, 50
    want = int(c.get("max_results", 200))
    while len(out) < want:
        data = post_json(
            s, "https://careers.walmart.com/api/ai/search-ai/api/v1/combined/"
               f"hybrid-search?page={page}&size={size}&locale=en_US",
            json={"query": c.get("search", "software engineer"),
                  "basicSearch": False, "filter": "", "locale": "en_US"},
            headers={"Content-Type": "application/json"})
        jobs = data.get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            m = j.get("metadata") or {}
            jid = m.get("jobId") or str(j.get("id", "")).replace("-External", "")
            city = (m.get("primaryLocationCity") or "").title()
            state = m.get("primaryLocationState") or ""
            lo, hi = m.get("minPay"), m.get("maxPay")
            out.append({
                "company": c.get("name", "Walmart"),
                "title": m.get("title") or m.get("jobPostingTitle", ""),
                "location": ", ".join(filter(None, [city, state])),
                "country": m.get("primaryLocationCountry", ""),
                "url": f"https://careers.walmart.com/us/en/job/{jid}" if jid else "",
                "external_id": jid,
                "source": "walmart careers",
                "posted_at": iso_date(m.get("jobPostingStartDate")),
                "comp": (f"${int(lo):,} - ${int(hi):,} {m.get('payFrequency','')}".strip()
                         if lo and hi else ""),
                "employment_type": (m.get("employmentTypes") or [""])[0],
                "department": (m.get("areas") or [""])[0],
                "yoe": filters.parse_yoe(j.get("text", ""), m.get("title", "")),
            })
        page += 1
        if len(jobs) < size:
            break
    return out


def phenom(c):
    """c: {name, host, max_pages?, country?}  ->  Phenom People careers API

    Phenom powers a large share of Fortune 500 careers sites (PepsiCo, AMD,
    Dollar General...) and exposes the whole board at /api/jobs. Two quirks
    shape this fetcher:

      - the `keyword` parameter is decorative. Searching "demand planning",
        "supply chain" or a nonsense string all return the same postings in
        the same order, so there is nothing to be gained by asking the board
        to filter; the pages are pulled in full and filters.py decides.
      - `keyword` must still be *present*, even empty. Omit it and the
        endpoint answers with no jobs at all rather than an error.

    max_pages caps the sweep: the boards here run to a few hundred US
    postings, but a retailer's board can hold ten thousand store roles, and
    reading all of those to find six planners is not a trade worth making.
    """
    s = session()
    host = c["host"]
    limit, out = 100, []
    for page in range(1, int(c.get("max_pages", 6)) + 1):
        data = get_json(s, f"https://{host}/api/jobs", params={
            "keyword": "", "limit": limit, "page": page, "sortBy": "relevance",
            "locale": "en_US", "country": c.get("country", "United States"),
        })
        rows = data.get("jobs") or []
        for row in rows:
            j = row.get("data", row)
            body = " ".join(filter(None, [j.get("description", ""),
                                          j.get("qualifications", "")]))
            cats = j.get("categories") or []
            out.append({
                "company": c["name"],
                "title": j.get("title", ""),
                "location": j.get("full_location", "") or ", ".join(
                    filter(None, [j.get("city", ""), j.get("state", "")])),
                "country": j.get("country", ""),
                # the Phenom job page is client-rendered and 404s when fetched
                # directly, so the apply link is the one that actually works
                "url": j.get("apply_url", "") or f"https://{host}/job/{j.get('req_id','')}",
                "external_id": str(j.get("req_id", "") or j.get("slug", "")),
                "source": "phenom",
                "posted_at": iso_date(j.get("posted_date")),
                "deadline": iso_date(j.get("posting_expiry_date")),
                "employment_type": _sched(j.get("employment_type", "")),
                "department": (cats[0].get("name", "") if cats and
                               isinstance(cats[0], dict) else ""),
                "snippet": clean_text(j.get("description", "")),
                "yoe": filters.parse_yoe(clean_text(body, 6000), j.get("title", "")),
            })
        if len(rows) < limit:
            break
    return out
