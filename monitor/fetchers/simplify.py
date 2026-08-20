"""Aggregator fetcher: SimplifyJobs GitHub repos (updated daily).

Covers companies whose careers sites have no stable public API (Meta,
LinkedIn, many startups). Parses the markdown tables in:
  - SimplifyJobs/New-Grad-Positions        (new grad, tier=newgrad)
  - SimplifyJobs/Summer2027-Internships    (interns, tier=intern)
"""
import re

from .http import session

REPOS = [
    ("https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
     "newgrad"),
    ("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
     "intern"),
]

ROW = re.compile(
    r"^\|\s*(?:🔥\s*)?(?:\*\*\[(?P<company>[^\]]+)\]|\*\*(?P<company2>[^|*]+)\*\*|(?P<cont>↳))"
    r".*?\|\s*(?P<title>[^|]+?)\s*\|\s*(?P<location>[^|]+?)\s*\|",
)
LINKS = re.compile(r"\((https?://[^)\s]+)\)")
AGE = re.compile(r"\|\s*(\d+)\s*d\s*\|?\s*$")
NOT_APPLY = ("camo.githubusercontent", "simplify.jobs", "i.imgur.com")


def _apply_link(line: str) -> str:
    """Pick the real application URL, skipping badge images and Simplify pages."""
    urls = LINKS.findall(line)
    for u in urls:
        if not any(x in u for x in NOT_APPLY):
            return u.split("?utm_source")[0]
    for u in urls:  # fall back to the simplify.jobs posting page
        if "simplify.jobs/p/" in u:
            return u.split("?utm_source")[0]
    return ""


def simplify(c):
    """c: {name: 'Simplify Aggregator', max_age_days?: 7}"""
    max_age = int(c.get("max_age_days", 7))
    s = session()
    out = []
    for url, tier in REPOS:
        try:
            text = s.get(url, timeout=60).text
        except Exception as e:  # noqa: BLE001
            print(f"simplify: failed {url}: {e}")
            continue
        last_company = ""
        for line in text.splitlines():
            m = ROW.search(line)
            if not m:
                continue
            company = (m.group("company") or m.group("company2") or "").strip()
            if company:
                last_company = company
            elif m.group("cont"):
                company = last_company
            if not company:
                continue
            age_m = AGE.search(line)
            if age_m and int(age_m.group(1)) > max_age:
                continue
            link = _apply_link(line)
            if not link:
                continue
            out.append({
                "company": company,
                "title": m.group("title").strip().strip("*"),
                "location": re.sub(r"\s{2,}", "; ", m.group("location").strip()),
                "url": link,
                "external_id": link,
                "source": "simplify-github",
                "tier_hint": tier,
            })
    return out
