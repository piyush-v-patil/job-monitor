"""Generic ATS fetchers: Greenhouse, Lever, Ashby, Workday, Eightfold, SmartRecruiters.

Every fetcher takes (company: dict from companies.yaml) and returns a list of
raw job dicts: {company, title, location, url, external_id, source, country?}
plus best-effort enrichment: {posted_at, comp, employment_type, workplace,
department, snippet}. Any enrichment field the ATS does not expose is "".
Filtering happens later in filters.py.
"""
from .http import session, get_json, post_json, iso_date, rel_date, clean_text

# Ashby/SmartRecruiters use their own vocabulary; normalize to one set.
EMPLOYMENT = {
    "fulltime": "Full-time", "full-time": "Full-time", "permanent": "Full-time",
    "intern": "Intern", "internship": "Intern",
    "contract": "Contract", "temporary": "Contract", "parttime": "Part-time",
}
WORKPLACE = {"onsite": "On-site", "on-site": "On-site", "remote": "Remote",
             "hybrid": "Hybrid"}


def _norm(table, value):
    return table.get(str(value or "").strip().lower(), str(value or "").strip())


def greenhouse(c):
    """c: {name, token, with_content?}  ->  boards-api.greenhouse.io

    with_content=true also pulls departments + a description snippet, at the
    cost of a much larger response; leave it off for high-volume boards.
    """
    s = session()
    url = f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs"
    if c.get("with_content"):
        url += "?content=true"
    data = get_json(s, url)
    out = []
    for j in data.get("jobs", []):
        depts = [d.get("name", "") for d in (j.get("departments") or [])]
        out.append({
            "company": c["name"],
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "external_id": str(j.get("id", "")),
            "source": "greenhouse",
            "posted_at": iso_date(j.get("first_published") or j.get("updated_at")),
            "department": depts[0] if depts else "",
            "snippet": clean_text(j.get("content", "")),
        })
    return out


def lever(c):
    """c: {name, token}  ->  api.lever.co"""
    s = session()
    data = get_json(s, f"https://api.lever.co/v0/postings/{c['token']}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        sal = j.get("salaryRange") or {}
        comp = ""
        if sal.get("min") and sal.get("max"):
            cur = sal.get("currency", "USD")
            comp = f"{cur} {int(sal['min']):,} - {int(sal['max']):,}"
        out.append({
            "company": c["name"],
            "title": j.get("text", ""),
            "location": cats.get("location", "") or str(j.get("workplaceType", "")),
            "country": j.get("country", ""),
            "url": j.get("hostedUrl", ""),
            "external_id": j.get("id", ""),
            "source": "lever",
            "posted_at": iso_date(j.get("createdAt")),
            "comp": comp,
            "employment_type": _norm(EMPLOYMENT, cats.get("commitment", "")),
            "workplace": _norm(WORKPLACE, j.get("workplaceType", "")),
            "department": cats.get("team", "") or cats.get("department", ""),
            "snippet": clean_text(j.get("descriptionPlain", "")),
        })
    return out


def ashby(c):
    """c: {name, token}  ->  Ashby posting API (GET; POST returns 401)"""
    s = session()
    data = get_json(
        s, "https://api.ashbyhq.com/posting-api/job-board/" + c["token"]
           + "?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = j.get("compensation") or {}
        workplace = j.get("workplaceType", "")
        if not workplace and j.get("isRemote"):
            workplace = "Remote"
        out.append({
            "company": c["name"],
            "title": j.get("title", ""),
            "location": j.get("location", "") or (j.get("address") or {}).get(
                "postalAddress", {}).get("addressLocality", ""),
            "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
            "external_id": j.get("id", ""),
            "source": "ashby",
            "posted_at": iso_date(j.get("publishedAt")),
            "comp": (comp.get("compensationTierSummary")
                     or comp.get("scrapeableCompensationSalarySummary") or ""),
            "employment_type": _norm(EMPLOYMENT, j.get("employmentType", "")),
            "workplace": _norm(WORKPLACE, workplace),
            "department": j.get("department", "") or j.get("team", ""),
            "snippet": clean_text(j.get("descriptionPlain", "")),
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
                "posted_at": rel_date(j.get("postedOn", "")),
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
            "posted_at": iso_date(j.get("t_create") or j.get("t_update")),
            "department": j.get("department", "") or j.get("business_unit", ""),
            "workplace": _norm(WORKPLACE, j.get("work_location_option", "")),
            "snippet": clean_text(j.get("job_description", "")),
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
            "posted_at": iso_date(j.get("releasedDate")),
            "employment_type": _norm(
                EMPLOYMENT, (j.get("typeOfEmployment") or {}).get("label", "")),
            "workplace": "Remote" if (loc.get("remote")) else "",
            "department": (j.get("department") or {}).get("label", ""),
        })
    return out
