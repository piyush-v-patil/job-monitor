"""Scope rules: US location, role/tier classification, years-of-experience.

These are the judgements every posting passes through, and getting one wrong is
silent - the run succeeds, the log looks tidy, and postings quietly stop
arriving (or the wrong country's start to). The cases below are the ones the
regexes were actually built in response to.
"""
import pytest

from monitor import filters as F
from monitor.fetchers.generic import workday_country


# ---- US location -----------------------------------------------------------

@pytest.mark.parametrize("loc", [
    "Sunnyvale, CA", "Cambridge, MA", "New York, NY", "Vancouver, WA",
    "Paris, TX", "Austin", "Lehi", "Boise, ID", "Wilmington, DE",
    "San Francisco, CA, USA", "Remote - US", "Anywhere in the US",
    "New York, NY; London, UK",          # one US site carries the posting
])
def test_us_locations_are_kept(loc):
    assert F.is_us(loc) is True


@pytest.mark.parametrize("loc", [
    "Bengaluru, Karnataka, India", "Tel Aviv, Israel", "Zurich, Switzerland",
    "Kraków, Poland", "Mexico City, Mexico", "Buenos Aires", "Remote, EMEA",
    "Toronto, ON, CA",                   # a province makes "CA" Canada
    "Cambridge, England, GBR",           # trailing country beats the city name
    "London, UK; Berlin",
])
def test_non_us_locations_are_dropped(loc):
    assert F.is_us(loc) is False


@pytest.mark.parametrize("loc", ["", "Remote", "N/A", "3 Locations", "Multiple"])
def test_unknown_locations_are_kept(loc):
    """Deliberate bias: an unknown costs a glance, a drop loses the posting."""
    assert F.is_us(loc) is True


def test_an_explicit_country_overrides_the_location_text():
    assert F.is_us("Cambridge", country="United States") is True
    assert F.is_us("Cambridge", country="GB") is False


def test_workday_country_reads_the_url_slug():
    """locationsText collapses to "N Locations"; the slug still has the country."""
    assert workday_country("/job/US-CA-Santa-Clara/SWE_R1") == "US"
    assert workday_country("/job/McLean-VA/Lead-SWE_R2") == "US"      # no country lead
    assert workday_country("/job/Israel-Tel-Aviv/SWE_R3") == "Israel"
    assert workday_country("/job/GBR---Fleet-UK/SWE_R4") == "United Kingdom"
    assert workday_country("/job/BangaloreIndia/SWE_R5") == "India"
    assert workday_country("/job/Somewhere-Odd/SWE_R6") == ""         # leave it to the heuristics


# ---- role and tier ---------------------------------------------------------

@pytest.mark.parametrize("title,tier", [
    ("Software Engineering Intern", "intern"),
    ("Software Engineer Co-op", "intern"),
    ("New Grad Software Engineer", "newgrad"),
    ("Software Engineer I", "newgrad"),
    ("Software Engineer - University Graduate, 2027", "newgrad"),
    ("Software Engineer", "experienced"),
    ("Software Engineer II", "experienced"),
    ("SDE II, AWS", "experienced"),
    ("Site Reliability Engineer", "experienced"),
    ("Member of Technical Staff", "experienced"),   # not "Staff Engineer"
])
def test_in_scope_titles_get_a_tier(title, tier):
    assert F.classify(title) == tier


@pytest.mark.parametrize("title", [
    "Senior Software Engineer", "Sr. Software Engineer", "Staff Software Engineer",
    "Principal Engineer", "Distinguished Engineer", "Engineering Manager",
    "Director of Engineering", "Solutions Architect", "Technical Program Manager",
    "Product Manager", "Business Analyst", "Technical Recruiter",
    "Mechanical Engineer", "HVAC Technician",
])
def test_out_of_scope_titles_are_rejected(title):
    assert F.classify(title) is None


def test_include_senior_opens_the_door_deliberately():
    assert F.classify("Senior Software Engineer") is None
    assert F.classify("Senior Software Engineer", include_senior=True) == "experienced"


@pytest.mark.parametrize("title,family", [
    ("Machine Learning Engineer", "ml-ai"),
    ("Member of Technical Staff", "ml-ai"),
    ("Data Engineer", "data"),
    ("Security Engineer, AppSec", "security"),
    ("Site Reliability Engineer", "devops-sre"),
    ("iOS Engineer", "mobile"),
    ("Frontend Engineer", "frontend"),
    ("Full Stack Engineer", "fullstack"),
    ("Backend Engineer", "backend"),
    ("Firmware Engineer", "embedded"),
    ("QA Automation Engineer", "qa-test"),
    ("Forward Deployed Engineer", "solutions"),
    ("Software Engineer", "software"),
])
def test_role_family_buckets(title, family):
    assert F.role_family(title) == family


def test_in_scope_annotates_and_filters_in_one_pass():
    job = {"title": "Machine Learning Engineer", "location": "Sunnyvale, CA"}
    out = F.in_scope(dict(job))
    assert out["tier"] == "experienced" and out["role"] == "ml-ai"
    assert F.in_scope(dict(job, location="Bengaluru, India")) is None
    assert F.in_scope(dict(job, title="Engineering Manager")) is None


# ---- years of experience ---------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("We require 3+ years of professional software development experience.", 3),
    ("You have at least 1 year of relevant experience", 1),
    ("Minimum of 8 years in industry", 8),
    ("0-2 years of experience", 0),
    ("3-5 years of relevant experience required", 3),
    ("10+ years working on compilers", 10),
    # the lowest stated bar is the reachable one
    ("2+ years of experience with Python; 5+ years of distributed systems", 2),
])
def test_a_stated_requirement_is_read(text, want):
    assert F.parse_yoe(text) == want


@pytest.mark.parametrize("text", [
    "Founded 15 years ago, we...",              # company history, before
    "5 years of company history",               # ...and after
    "Over the past 10 years of innovation",
    "We've spent 20 years building trust",
    "A 4-year degree in CS",                    # length of study
    "Three years of experience",                # spelled out, not parsed
    "",
])
def test_things_that_are_not_a_requirement_are_ignored(text):
    assert F.parse_yoe(text) is None


def test_an_internship_never_states_a_real_bar():
    assert F.parse_yoe("5+ years of experience", "Software Engineering Intern") is None
