"""Role, tier, and US-location filtering.

Scope (from project spec):
  - Roles: SWE + adjacent tech (data, ML, DevOps/SRE, security, QA, mobile/web).
  - Tiers: intern, new grad / entry level, experienced up to ~5 years.
  - Exclude: staff/principal/architect/management and (by default) "senior" titles.
  - US locations (incl. US-remote) only.
"""
import re

# ---- role scope ------------------------------------------------------------
ROLE_INCLUDE = re.compile(
    r"software|swe\b|sde\b|developer|full.?stack|front.?end|back.?end|mobile engineer"
    r"|ios engineer|android|machine learning|\bml\b|\bai engineer|data engineer"
    r"|data scientist|devops|site reliability|\bsre\b|security engineer"
    r"|infrastructure engineer|platform engineer|cloud engineer|systems engineer"
    r"|test engineer|quality (assurance|engineer)|\bqa engineer",
    re.I,
)

ROLE_EXCLUDE = re.compile(
    r"\bstaff\b|principal|distinguished|architect|manager|director|\bvp\b"
    r"|vice president|head of|chief|fellow|executive|recruiter|sales|account"
    r"|\bhr\b|attorney|counsel|technician\b|electrical|mechanical|civil engineer"
    r"|manufacturing|hvac|facilities",
    re.I,
)

SENIOR = re.compile(r"\bsenior\b|\bsr\.?\s", re.I)

# ---- tier detection --------------------------------------------------------
INTERN = re.compile(r"\bintern(ship)?\b|co-?op\b", re.I)
NEWGRAD = re.compile(
    r"new ?grad|university grad|campus|early.?career|entry.?level|college grad"
    r"|\bgraduate\b|(engineer|swe|sde)\s*(i|1)\b|\bl3\b|\be3\b|associate (software|engineer)"
    r"|\b20\d{2}\b",  # year in title (e.g. "SDE - 2026") = campus-cycle role
    re.I,
)
# Explicit mid-level markers (II/III/2/3). Plain "Software Engineer" titles also
# land in "experienced" — verify years-of-experience in the posting yourself.
EXPERIENCED = re.compile(r"(engineer|swe|sde)\s*(ii|iii|2|3)\b|mid.?level", re.I)

# ---- US location -----------------------------------------------------------
US_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS"
    "|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
US_HINT = re.compile(
    rf"united states|\busa?\b|,\s*({US_STATES})\b|\b({US_STATES})\s*,?\s*(us|usa)?$"
    r"|new york|san francisco|seattle|austin|boston|chicago|los angeles|denver"
    r"|atlanta|dallas|houston|miami|phoenix|portland|philadelphia|washington"
    r"|bellevue|redmond|mountain view|sunnyvale|palo alto|menlo park|cupertino"
    r"|san jose|santa clara|irvine|san diego|cambridge|pittsburgh|raleigh"
    r"|nashville|salt lake|remote.*(us|america)|us.*remote",
    re.I,
)
NON_US = re.compile(
    r"canada|toronto|vancouver|london|dublin|india|bangalore|bengaluru|hyderabad"
    r"|singapore|tokyo|japan|china|shanghai|beijing|germany|berlin|munich|paris"
    r"|france|israel|tel aviv|australia|sydney|mexico|brazil|poland|warsaw"
    r"|netherlands|amsterdam|spain|madrid|zurich|switzerland|korea|seoul|taiwan"
    r"|ireland|uk\b|united kingdom|remote.*(emea|apac|latam)",
    re.I,
)


def is_us(location: str, country: str = "") -> bool:
    if country:
        return country.strip().lower() in (
            "us", "usa", "united states", "united states of america", "u.s.", "u.s.a.")
    if not location:
        return False
    if NON_US.search(location) and not US_HINT.search(location):
        return False
    return bool(US_HINT.search(location))


def classify(title: str, include_senior: bool = False) -> str | None:
    """Return tier string if the job is in scope, else None."""
    if not title or ROLE_EXCLUDE.search(title):
        return None
    if INTERN.search(title):
        return "intern" if ROLE_INCLUDE.search(title) else None
    if not ROLE_INCLUDE.search(title):
        return None
    if SENIOR.search(title) and not include_senior:
        return None
    if NEWGRAD.search(title):
        return "newgrad"
    if EXPERIENCED.search(title):
        return "experienced"
    # Plain engineer title with no level marker: treat as experienced-unknown.
    return "experienced"


def in_scope(job: dict, include_senior: bool = False) -> dict | None:
    tier = classify(job.get("title", ""), include_senior)
    if tier is None:
        return None
    if not is_us(job.get("location", ""), job.get("country", "")):
        return None
    job["tier"] = tier
    return job
