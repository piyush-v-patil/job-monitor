"""Generic ATS fetchers: Greenhouse, Lever, Ashby, Workday, Eightfold, SmartRecruiters.

Every fetcher takes (company: dict from companies.yaml) and returns a list of
raw job dicts: {company, title, location, url, external_id, source, country?}.
Filtering happens later in filters.py.
"""
from .http import session, get_json, post_json


def greenhouse(c):
    """c: {name, token}  ->  boards-api.greenhouse.io"""
    s = session()
    data = get_json(s, f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs")
    out = []
    for j in data.get("jobs", []):
        out.append({
            "company": c["name"],
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "external_id": str(j.get("id", "")),
            "source": "greenhouse",
        })
    return out


def lever(c):
    """c: {name, token}  ->  api.lever.co"""
    s = session()
    data = get_json(s, f"https://api.lever.co/v0/postings/{c['token']}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append({
            "company": c["name"],
            "title": j.get("text", ""),
            "location": cats.get("location", "") or str(j.get("workplaceType", "")),
            "country": j.get("country", ""),
            "url": j.get("hostedUrl", ""),
            "external_id": j.get("id", ""),
            "source": "lever",
        })
    return out


def ashby(c):
    """c: {name, token}  ->  Ashby posting API"""
    s = session()
    data = post_json(
        s, "https://api.ashbyhq.com/posting-api/job-board/" + c["token"],
        json={"includeCompensation": False},
    )
    out = []
    for j in data.get("jobs", []):
        out.append({
            "company": c["name"],
            "title": j.get("title", ""),
            "location": j.get("location", "") or (j.get("address") or {}).get(
                "postalAddress", {}).get("addressLocality", ""),
            "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
            "external_id": j.get("id", ""),
            "source": "ashby",
        })
    return out


def workday(c):
    """c: {name, host, tenant, site, search?}  e.g. host=nvidia.wd5.myworkdayjobs.com"""
    s = session()
    url = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    out, offset, limit = [], 0, 20
    while offset < int(c.get("max_results", 100)):
        body = {"appliedFacets": c.get("facets", {}), "limit": limit, "offset": offset,
                "searchText": c.get("search", "software engineer")}
        data = post_json(s, url, json=body,
                         headers={"Content-Type": "application/json"})
        posts = data.get("jobPostings", [])
        if not posts:
            break
        for j in posts:
            path = j.get("externalPath", "")
            out.append({
                "company": c["name"],
                "title": j.get("title", ""),
                "location": j.get("locationsText", ""),
                "url": f"https://{c['host']}/en-US/{c['site']}{path}" if path else "",
                "external_id": j.get("bulletFields", [""])[0] if j.get("bulletFields") else path,
                "source": "workday",
            })
        offset += limit
    return out


def eightfold(c):
    """c: {name, host, domain, search?}  e.g. Netflix: explore.jobs.netflix.net"""
    s = session()
    q = c.get("search", "software engineer").replace(" ", "%20")
    url = (f"https://{c['host']}/api/apply/v2/jobs?domain={c['domain']}"
           f"&num=100&query={q}&location=United%20States&sort_by=timestamp")
    data = get_json(s, url)
    out = []
    for j in data.get("positions", []):
        out.append({
            "company": c["name"],
            "title": j.get("name", ""),
            "location": j.get("location", "") or "; ".join(j.get("locations", []) or []),
            "url": j.get("canonicalPositionUrl", "")
                   or f"https://{c['host']}/careers/job/{j.get('id','')}",
            "external_id": str(j.get("id", "")),
            "source": "eightfold",
        })
    return out


def smartrecruiters(c):
    """c: {name, token}  ->  public postings API (e.g. Visa)"""
    s = session()
    data = get_json(
        s, f"https://api.smartrecruiters.com/v1/companies/{c['token']}/postings?limit=100")
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        out.append({
            "company": c["name"],
            "title": j.get("name", ""),
            "location": ", ".join(filter(None, [loc.get("city", ""), loc.get("region", "")])),
            "country": loc.get("country", ""),
            "url": f"https://jobs.smartrecruiters.com/{c['token']}/{j.get('id','')}",
            "external_id": str(j.get("id", "")),
            "source": "smartrecruiters",
        })
    return out
