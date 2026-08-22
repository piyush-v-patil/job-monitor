"""Company-specific fetchers for big tech careers APIs.

These use unofficial-but-public JSON endpoints that back each company's own
careers site. They can change without notice — if one starts failing, check
the network tab of the careers page and update the endpoint.
"""
from .. import filters
from .http import session, get_json, post_json, iso_date, clean_text


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


def google(c):
    s = session()
    out = []
    for page in (1, 2):
        url = ("https://careers.google.com/api/v3/search/"
               f"?q={c.get('search', 'software engineer').replace(' ', '%20')}"
               f"&location=United%20States&page={page}")
        data = get_json(s, url)
        jobs = data.get("jobs", [])
        if not jobs:
            break
        for j in jobs:
            jid = str(j.get("id", "")).replace("jobs/", "")
            out.append({
                "company": "Google",
                "title": j.get("title", ""),
                "location": "; ".join(
                    loc.get("display", "") for loc in (j.get("locations") or [])),
                "url": ("https://www.google.com/about/careers/applications/jobs/results/"
                        + jid),
                "external_id": jid,
                "source": "google careers",
            })
    return out


def apple(c):
    s = session()
    # Warm up to get cookies/CSRF, then query the role search API.
    s.get("https://jobs.apple.com/en-us/search", timeout=30)
    body = {
        "query": c.get("search", "software engineer"),
        "filters": {"postingpostLocation": ["postLocation-USA"]},
        "page": 1, "locale": "en-us", "sort": "newest",
    }
    headers = {"Content-Type": "application/json"}
    csrf = s.cookies.get("csrf") or s.headers.get("X-Apple-CSRF-Token")
    if csrf:
        headers["X-Apple-CSRF-Token"] = csrf
    data = post_json(s, "https://jobs.apple.com/api/role/search", json=body, headers=headers)
    out = []
    for j in (data.get("searchResults") or []):
        out.append({
            "company": "Apple",
            "title": j.get("postingTitle", ""),
            "location": "; ".join(
                loc.get("name", "") for loc in (j.get("locations") or [])),
            "url": ("https://jobs.apple.com/en-us/details/"
                    f"{j.get('positionId','')}/{j.get('transformedPostingTitle','')}"),
            "external_id": str(j.get("positionId", "")),
            "source": "jobs.apple.com",
        })
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
