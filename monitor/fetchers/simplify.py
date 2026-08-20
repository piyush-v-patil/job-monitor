"""Aggregator fetcher: SimplifyJobs GitHub repos (updated daily).

Covers companies whose careers sites have no stable public API (Meta,
LinkedIn, many startups). Parses the HTML tables in:
  - SimplifyJobs/New-Grad-Positions        (new grad, tier=newgrad)
  - SimplifyJobs/Summer2027-Internships    (interns, tier=intern)

Row shape (5 cells): company | title | location | apply links | age.
Rows whose apply cell is 🔒 are closed and are skipped. A company cell of
"↳" means "same company as the row above".
"""
import re
from datetime import datetime, timedelta, timezone

from .http import session

REPOS = [
    ("https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
     "newgrad"),
    ("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
     "intern"),
]

ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
LINKS = re.compile(r'href="(https?://[^"]+)"')
TAGS = re.compile(r"<[^>]+>")
AGE = re.compile(r"(\d+)\s*(h|d|mo|yr)")
NOT_APPLY = ("camo.githubusercontent", "simplify.jobs", "i.imgur.com")


def _text(cell: str) -> str:
    """Strip tags, turn <br> into '; ', collapse whitespace."""
    s = re.sub(r"</?br\s*/?>", "; ", cell, flags=re.I)
    s = TAGS.sub("", s)
    return re.sub(r"\s+", " ", s).replace("&amp;", "&").strip()


def _age_days(text: str):
    """'0d' -> 0, '3mo' -> 90, '2h' -> 0. None if unparseable."""
    m = AGE.search(text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"h": 0, "d": n, "mo": n * 30, "yr": n * 365}.get(unit, n)


def _apply_link(cell: str) -> str:
    """Pick the real application URL, skipping badge images and Simplify pages."""
    urls = LINKS.findall(cell)
    for u in urls:
        if not any(x in u for x in NOT_APPLY):
            return u.split("?utm_source")[0].split("&utm_source")[0]
    for u in urls:  # fall back to the simplify.jobs posting page
        if "simplify.jobs/p/" in u:
            return u.split("?utm_source")[0]
    return ""


def simplify(c):
    """c: {name: 'Simplify Aggregator', max_age_days?: 7}"""
    max_age = int(c.get("max_age_days", 7))
    today = datetime.now(timezone.utc)
    s = session()
    out = []
    for url, tier in REPOS:
        try:
            text = s.get(url, timeout=60).text
        except Exception as e:  # noqa: BLE001
            print(f"simplify: failed {url}: {e}")
            continue
        last_company = ""
        for row in ROW.findall(text):
            cells = CELL.findall(row)
            if len(cells) < 5:
                continue
            company = _text(cells[0]).lstrip("🔥").strip()
            if company == "↳" or not company:
                company = last_company
            else:
                last_company = company
            if not company:
                continue
            apply_cell = cells[3]
            if "🔒" in apply_cell:      # closed / no longer accepting
                continue
            link = _apply_link(apply_cell)
            if not link:
                continue
            age = _age_days(_text(cells[4]))
            if age is not None and age > max_age:
                continue
            posted = ((today - timedelta(days=age)).strftime("%Y-%m-%d")
                      if age is not None else "")
            out.append({
                "company": company,
                "title": _text(cells[1]),
                "location": _text(cells[2]),
                "url": link,
                "external_id": link,
                "source": "simplify-github",
                "tier_hint": tier,
                "posted_at": posted,
            })
    return out
