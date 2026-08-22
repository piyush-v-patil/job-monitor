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
    r"|test engineer|quality (assurance|engineer)|\bqa\b"
    # titles real employers use that the original list never matched:
    r"|member of technical staff|\bmts\b"           # OpenAI, Anthropic, Mistral
    r"|forward deployed"                            # Palantir
    r"|research engineer|research scientist|applied scientist"
    r"|deep learning|computer vision|\bnlp\b|\bllm\b|robotics engineer"
    r"|compiler|firmware|embedded (software|systems)|performance engineer"
    r"|distributed systems|web developer|game (engineer|developer)"
    r"|analytics engineer|\bsdet\b|network engineer|database engineer",
    re.I,
)

ROLE_EXCLUDE = re.compile(
    # "Staff Engineer" is out of scope, but "Member of Technical Staff" is an
    # ordinary IC title at OpenAI/Anthropic - do not let the first kill it.
    r"(?<!technical )\bstaff\b|principal|distinguished|architect|manager|director|\bvp\b"
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
    r"|nashville|salt lake|remote.*(us|america)|us.*remote"
    # shorthand the boards actually print - "SF" and "NYC" were being dropped
    r"|\bsf\b|\bnyc\b|\bd\.?c\.?\b|bay area|silicon valley"
    r"|brooklyn|manhattan|oakland|berkeley|san mateo|foster city|burlingame"
    r"|arlington|reston|herndon|mclean|bethesda|boulder|fort collins|ann arbor"
    r"|madison|minneapolis|st\.? louis|kansas city|columbus|cincinnati|cleveland"
    r"|detroit|indianapolis|charlotte|durham|chapel hill|orlando|tampa|jacksonville"
    r"|san antonio|el segundo|hawthorne|torrance|pasadena|culver city|santa monica"
    r"|long beach|sacramento|san bruno|redwood city|milpitas|fremont|plano|richardson"
    r"|round rock|provo|lehi|boise|omaha|tucson|albuquerque|las vegas|reno|spokane"
    r"|tacoma|kirkland|renton|everett|hillsboro|beaverton",
    re.I,
)

# Locations that say nothing either way. The boards emit these constantly
# (Ashby "N/A", Workday "3 Locations", a bare "Remote"), and discarding them
# silently loses real US postings - so they are kept instead.
AMBIGUOUS_LOC = re.compile(
    r"^\s*(n/?a|none|null|-|remote|remote friendly|anywhere|flexible|multiple"
    r"|multiple locations?|various( locations?)?|\d+\s*locations?|other)\s*$",
    re.I,
)
NON_US = re.compile(
    r"canada|toronto|vancouver|london|dublin|india|bangalore|bengaluru|hyderabad"
    r"|singapore|tokyo|japan|china|shanghai|beijing|germany|berlin|munich|paris"
    r"|france|israel|tel aviv|australia|sydney|mexico|brazil|poland|warsaw"
    r"|netherlands|amsterdam|spain|madrid|zurich|switzerland|korea|seoul|taiwan"
    r"|ireland|uk\b|united kingdom|england|scotland|wales|remote.*(emea|apac|latam)",
    re.I,
)

# A country named at the END of a location is the authoritative one: "Cambridge,
# England, GBR" is not Cambridge MA, however hard the city name hints US.
TRAILING_NON_US = re.compile(
    r",\s*(canada|can|gbr|uk|united kingdom|england|scotland|wales|ireland|irl"
    r"|india|ind|israel|isr|germany|deu|france|fra|spain|esp|italy|ita|poland|pol"
    r"|netherlands|nld|sweden|swe|switzerland|che|australia|aus|new zealand|nzl"
    r"|japan|jpn|china|chn|singapore|sgp|korea|kor|taiwan|twn|brazil|bra|mexico"
    r"|mex|argentina|arg|colombia|col|romania|rou|portugal|prt|denmark|dnk"
    r"|norway|nor|finland|fin|austria|aut|belgium|bel|czechia|cze|hungary|hun"
    r"|greece|grc|turkey|tur|ukraine|ukr|philippines|phl|indonesia|idn|malaysia"
    r"|mys|vietnam|vnm|thailand|tha|south africa|zaf|egypt|nigeria|kenya)\s*$",
    re.I,
)

# "Toronto, ON, CA" is Canada; a bare "CA" after a Canadian province is not
# California. Rewritten before the hints run so the province settles it.
CA_PROVINCE = re.compile(
    r"\b(ON|BC|QC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\s*,\s*CA\b")

# Boards list multi-site postings as one string ("Toronto, ON, Canada;
# Sunnyvale, CA"). One US site makes the posting reachable, so each site is
# judged on its own and any US one carries it.
LOC_SPLIT = re.compile(r"\s*[;|]\s*|\s+/\s+")


def _site_is_us(loc: str) -> bool:
    """Judge a single location string."""
    if not loc or AMBIGUOUS_LOC.match(loc):
        return True
    loc = CA_PROVINCE.sub("Canada", loc)
    if TRAILING_NON_US.search(loc):
        return False
    if NON_US.search(loc) and not US_HINT.search(loc):
        return False
    return bool(US_HINT.search(loc))


def is_us(location: str, country: str = "") -> bool:
    """US, or plausibly US.

    Deliberately biased toward keeping: an unknown location costs one glance
    on the dashboard, whereas dropping it loses the posting entirely.
    """
    if country:
        return country.strip().lower() in (
            "us", "usa", "united states", "united states of america", "u.s.", "u.s.a.")
    loc = (location or "").strip()
    if not loc or AMBIGUOUS_LOC.match(loc):
        return True
    return any(_site_is_us(p.strip()) for p in LOC_SPLIT.split(loc) if p.strip())


# ---- role family -----------------------------------------------------------
# First match wins, so the specific patterns are listed before the generic
# "software" catch-all at the end.
ROLE_FAMILY = [
    ("ml-ai", re.compile(
        r"machine learning|\bml\b|\bai\b|deep learning|computer vision|\bnlp\b"
        r"|\bllm\b|applied scientist|research (engineer|scientist)|\bmts\b"
        r"|member of technical staff", re.I)),
    ("data", re.compile(r"data (engineer|scientist|platform)|analytics engineer"
                        r"|business intelligence|\betl\b", re.I)),
    ("security", re.compile(r"security|cryptograph|appsec|infosec|trust (and|&) safety", re.I)),
    ("devops-sre", re.compile(r"devops|site reliability|\bsre\b|infrastructure"
                              r"|platform engineer|cloud engineer|observability", re.I)),
    ("mobile", re.compile(r"\bios\b|android|mobile", re.I)),
    ("frontend", re.compile(r"front.?end|web developer|\bui engineer|javascript|react", re.I)),
    ("fullstack", re.compile(r"full.?stack", re.I)),
    ("backend", re.compile(r"back.?end|distributed systems|\bapi engineer|server", re.I)),
    ("embedded", re.compile(r"embedded|firmware|compiler|robotics|hardware|\bfpga\b", re.I)),
    ("qa-test", re.compile(r"\bqa\b|quality|test engineer|\bsdet\b", re.I)),
    ("solutions", re.compile(r"forward deployed|solutions engineer|field engineer"
                             r"|customer engineer|deployment", re.I)),
]


def role_family(title: str) -> str:
    """Coarse discipline bucket, for filtering the dashboard."""
    for name, pattern in ROLE_FAMILY:
        if pattern.search(title or ""):
            return name
    return "software"          # generic SWE with no discipline in the title


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
    job["role"] = role_family(job.get("title", ""))
    return job


# ---- years of experience ---------------------------------------------------
# The tier a title implies is often wrong ("Software Engineer" says nothing),
# so where a posting states its own bar in prose we read it. Only a stated
# requirement counts - company history ("founded 15 years ago"), degree length
# ("4-year degree") and anniversaries must not register.

# "years" only counts when what follows reads like a requirement, never "ago".
YOE_RE = re.compile(
    r"(?:(?:minimum|min\.?|at least)\s+(?:of\s+)?)?"
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–]|to)?\s*(?:\d{1,2})?\s*(?:\+|plus)?\s*"
    r"years?\b(?!\s+ago)"
    r"\s+(?:of|in|with|working|building|developing|professional|relevant"
    r"|industry|hands.?on|post|full.?time|prior|related|applicable)",
    re.I)

# Cues just before the number that mean it is describing the company, not you.
YOE_HISTORY = re.compile(
    r"(founded|formed|established|spent|celebrat|anniversary|history|been around"
    r"|over the (past|last)|in the (past|last)|for the (past|last)|since)\b[^.]{0,40}$",
    re.I)

# ...and cues just after it, for the same reason ("10 years of company history").
YOE_HISTORY_AFTER = re.compile(
    r"^\s*(of\s+)?(company|corporate|business|operation|growth|history|innovation"
    r"|partnership|service|success)", re.I)

# "4-year degree", "four year program" - length of study, not experience.
YOE_DEGREE = re.compile(r"\d\s*[-\s]?year\s+(degree|program|university|college|school)", re.I)


def parse_yoe(text: str, title: str = "") -> int | None:
    """Lowest stated years-of-experience requirement, or None if unstated.

    The minimum is taken rather than the maximum: a posting asking for "2+
    years backend, 5+ years distributed systems" is reachable at two.
    """
    if not text or INTERN.search(title or ""):
        return None                      # an internship never states a real bar
    best = None
    for m in YOE_RE.finditer(text):
        before = text[max(0, m.start() - 60): m.start()]
        if YOE_HISTORY.search(before):
            continue
        # the match already consumed the connector ("of"/"in"), so look at
        # what comes straight after it: "...years of | company history"
        if YOE_HISTORY_AFTER.search(text[m.end(): m.end() + 40]):
            continue
        if YOE_DEGREE.search(text[max(0, m.start() - 20): m.end() + 20]):
            continue
        n = int(m.group(1))
        if 0 <= n <= 20:
            best = n if best is None else min(best, n)
    return best
