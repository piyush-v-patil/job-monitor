"""Role, tier, and US-location filtering for the supply-chain tracker.

Scope (the second profile - see monitor/profiles.py):
  - Roles: demand planning and forecasting first, then the planning family
    around it: supply / production / materials / inventory / capacity
    planning, S&OP and IBP, merchandise and allocation planning, logistics
    and network planning, procurement, and the program-management and
    analytics roles that sit on top of those.
  - Band: analyst through manager. Director and above are out by default;
    interns and campus programs are kept, tiered apart so they can be
    filtered off the dashboard in one click.
  - US locations (incl. US-remote) only - the location rules are shared with
    the tech tracker, since geography does not care what the job is.

Why the include pattern is deliberately loose and the excludes do the work:
a posting the filter drops is invisible forever, whereas one it wrongly keeps
costs a single glance on the dashboard. Every exclusion below was written
against a real title this scanner saw, and the ones with a company beside
them are the exact strings that motivated the rule.
"""
import re

# Location and years-of-experience are job-family agnostic, so both trackers
# read them from the same place rather than keeping two copies to drift apart.
from .filters import US_STATES, is_us, parse_yoe  # noqa: F401  (re-exported)

# ---- role scope ------------------------------------------------------------
# Anything naming a planning discipline, the systems that run one, or the
# supply-chain functions immediately around it. Bare "planner"/"planning"/
# "forecast" are in on purpose - they catch titles no keyword list predicts
# ("Sr Planner", "Consultant - Demand Planning") - and the excludes below
# take back the other professions that use those same words.
ROLE_INCLUDE = re.compile(
    r"demand (plan|forecast|analy|manage|scien)"
    r"|supply (plan|chain)|supply.?demand"
    r"|production (plan|schedul|control)|material(s)? (plan|manage|control)"
    r"|master (schedul|production)|inventory"
    r"|capacity plan|replenishment|allocation"
    r"|merchandis(e|ing) (plan|financial)|assortment|\bmfp\b|open.to.buy|\botb\b"
    r"|s ?& ?op\b|\bsiop\b|sales (and|&) operations planning"
    r"|integrated business planning|\bibp\b"
    r"|forecast"
    # the planning systems - a title naming one is always this job family
    r"|\bmrp\b|\bdrp\b|\bapo\b|kinaxis|blue ?yonder|\bo9\b|anaplan|logility|e2open"
    r"|network (design|plan|optimi|strateg)|distribution (plan|center manage)"
    r"|transportation (plan|manage)|logistics|fulfillment|\bwms\b|\boms\b"
    r"|procurement|strategic sourcing|purchasing|category manage|commodity manage"
    r"|buyer|supplier (manage|develop|performance|quality)|vendor manage"
    r"|\bplanner\b|\bplanning\b|scheduler"
    # program / project management, but only where it is anchored to the domain
    r"|supply chain (program|project|transformation)|\bscpm\b",
    re.I,
)

# Other professions that own the words "planning", "forecast", "buyer" and
# "sourcing". Checked before anything else, so they never reach the tiering.
DOMAIN_EXCLUDE = re.compile(
    # finance & personal planning
    r"financial planning|\bfp&?a\b|financial planner|wealth|estate plan|retirement"
    r"|tax plan|succession plan|benefits? plan|actuar|treasur|audit"
    # revenue / pricing planning is FP&A wearing a planning hat
    r"|revenue (plan|manage|operations)|pricing|\bsales plan"
    # marketing, media and events
    r"|marketing|media (plan|buyer)|brand plan|event plan|meeting plan|wedding"
    r"|communications? plan|content plan"
    # buildings, land and facilities
    r"|urban plan|city plan|land use|space plan|floor plan|facilit|real estate"
    r"|campus plan|site plan|architect"
    # clinical, education and other unrelated "plans"
    r"|treatment plan|care plan|discharge plan|lesson plan|meal plan|menu plan"
    # a "Finance Analyst, Sales Strategy & Planning" is FP&A under another name
    r"|\bfinance\b|financial analyst"
    # maintenance / outage planning is a trades discipline, not supply chain
    r"|maintenance plan|maintenance planner|turnaround plan|outage plan|shutdown plan"
    r"|test plan|flight plan|mission plan|estate|travel plan"
    # recruiting reuses "sourcing" wholesale, and HR business partners sit
    # inside supply-chain orgs without doing any of this work
    r"|recruit|sourcer\b|talent acquisition|staffing|\bhr\b|human resources"
    r"|people (lead|partner|manager)|business partner"
    # software roles that merely mention the domain ("Software Engineer,
    # Supply Chain") - a different job family with a different resume
    r"|software (engineer|developer)|\bsde\b|front.?end|back.?end|full.?stack"
    r"|devops|site reliability|\bsre\b|mobile engineer|\bios\b|android developer"
    r"|security engineer|network engineer|systems engineer|solutions architect"
    r"|machine learning engineer|\bml engineer\b",
    re.I,
)

# Hourly, floor and shift roles. These outnumber the planning jobs at every
# retailer and manufacturer on the list, and none of them are this search.
FLOOR_EXCLUDE = re.compile(
    r"warehouse (associate|worker|clerk|selector|specialist)|order (picker|selector)"
    r"|forklift|material handler|\bloader\b|unloader|\bdriver\b|\bcdl\b|delivery"
    r"|courier|technician|mechanic|machinist|welder|electrician|operator"
    r"|janitor|sanitation|custodian|\bcook\b|cashier|stocker|merchandiser\b"
    r"|team member|crew member|inspector|assembler|picker|packer|line lead"
    r"|\d(st|nd|rd|th) shift|\bnights?\b|swing shift|weekend shift|overnight"
    r"|seasonal|part.?time|temporary"
    r"|apprentice|trainee\b(?! program)",
    re.I,
)

# Above the band the user asked for (analyst through manager). Kept as its own
# pattern so --include-senior can switch it off in one place.
LEVEL_EXCLUDE = re.compile(
    r"\bdirector\b|\bvp\b|vice president|head of|\bchief\b|president|\bcxo\b"
    r"|\bprincipal\b|\bfellow\b|general manager|\bgm\b|executive|board member",
    re.I,
)

# ---- tier detection --------------------------------------------------------
# "campus" only counts as a campus-hiring signal next to a hiring word -
# SpaceX's "Bastrop Campus Planning Manager" is a facilities job.
INTERN = re.compile(
    r"\bintern(ship)?\b|co-?op\b|campus (program|hire|recruit)"
    r"|university (program|recruit|graduate)", re.I)
ENTRY = re.compile(
    r"\bassociate\b|\bcoordinator\b|\bassistant\b|entry.?level|new ?grad"
    r"|graduate (program|scheme|development)|rotational|development program"
    r"|early.?career|\bjr\.?\b|junior|analyst (i|1)\b|planner (i|1)\b"
    r"|\b(i|1)$|\b20\d{2}\b", re.I)
# Director and above only ever reach this point under --include-senior, since
# LEVEL_EXCLUDE has already dropped them otherwise; naming them here means the
# widened band labels them "manager" rather than mislabelling them "mid".
MANAGER = re.compile(
    r"\bmanager\b|\bmgr\b|\blead\b|\bleader\b|supervisor|\bmanagement\b"
    r"|program manager|project manager|\bpmo\b|\bowner\b"
    r"|\bdirector\b|\bvp\b|vice president|head of|\bchief\b|\bprincipal\b", re.I)
# Campus pipelines whose names contain a manager word without being manager
# roles: "Management Trainee Program - Supply Chain" is an entry-level hire.
PROGRAM = re.compile(
    r"trainee|rotational|graduate (program|scheme)|development program"
    r"|leadership (program|development)|early career program", re.I)

# ---- role family -----------------------------------------------------------
# First match wins, so the specific disciplines are listed before the generic
# supply-chain catch-all at the end.
ROLE_FAMILY = [
    ("demand-planning", re.compile(
        r"demand (plan|forecast|analy|manage|scien)|forecast|s ?& ?op\b|\bsiop\b"
        r"|sales (and|&) operations planning|integrated business planning|\bibp\b",
        re.I)),
    ("supply-planning", re.compile(
        r"supply plan|production (plan|schedul|control)|material(s)? (plan|control)"
        r"|master (schedul|production)|capacity plan|\bmrp\b|\bdrp\b|scheduler"
        r"|supply.?demand", re.I)),
    ("inventory", re.compile(
        r"inventory|replenishment|allocation|\bdrp\b", re.I)),
    ("merch-planning", re.compile(
        r"merchandis(e|ing)|assortment|\bmfp\b|open.to.buy|\botb\b", re.I)),
    ("procurement", re.compile(
        r"procurement|sourcing|purchasing|buyer|category manage|commodity"
        r"|supplier|vendor", re.I)),
    ("logistics", re.compile(
        r"logistics|transportation|distribution|fulfillment|warehouse|freight"
        r"|network (design|plan|optimi)|\bwms\b|\boms\b|import|export|customs",
        re.I)),
    ("workforce", re.compile(
        r"workforce|\bwfm\b|contact cent|call cent|staffing plan", re.I)),
    ("analytics", re.compile(
        r"data scien|analytic|business intelligence|\bbi\b|operations research"
        r"|data analyst|statistic|optimi", re.I)),
    ("program-mgmt", re.compile(
        r"program manager|project manager|\bpmo\b|transformation|\bscpm\b"
        r"|process (owner|improvement)|continuous improvement", re.I)),
]


def role_family(title: str) -> str:
    """Coarse discipline bucket, for filtering the dashboard."""
    for name, pattern in ROLE_FAMILY:
        if pattern.search(title or ""):
            return name
    return "supply-chain"      # in scope, but the title names no discipline


def classify(title: str, include_senior: bool = False) -> str | None:
    """Return the tier if the posting is in scope, else None.

    include_senior widens the band upward to Director and above; the flag
    keeps the same name as the tech profile's so one --include-senior switch
    drives both trackers.
    """
    if not title:
        return None
    if DOMAIN_EXCLUDE.search(title) or FLOOR_EXCLUDE.search(title):
        return None
    if not ROLE_INCLUDE.search(title):
        return None
    if LEVEL_EXCLUDE.search(title) and not include_senior:
        return None
    if INTERN.search(title):
        return "intern"
    if PROGRAM.search(title):
        return "entry"
    # manager is checked before entry: "Associate Manager, Demand Planning"
    # and "Assistant Manager - Logistics" are manager-band roles whose first
    # word would otherwise read as entry level.
    if MANAGER.search(title):
        return "manager"
    if ENTRY.search(title):
        return "entry"
    # Plain "Demand Planner" / "Supply Chain Analyst" with no level marker.
    # Neither entry nor manager - the years-of-experience filter on the
    # dashboard is the honest way to split these.
    return "mid"


def in_scope(job: dict, include_senior: bool = False) -> dict | None:
    tier = classify(job.get("title", ""), include_senior)
    if tier is None:
        return None
    if not is_us(job.get("location", ""), job.get("country", "")):
        return None
    job["tier"] = tier
    job["role"] = role_family(job.get("title", ""))
    return job
