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


# ---- new grad detection and priority ---------------------------------------

@pytest.mark.parametrize("text", [
    "We are hiring new grads for our 2027 engineering cohort.",
    "This is an early career role on the platform team.",
    "Open to recent graduates from any accredited university.",
    "Our university recruiting team will be in touch.",
    "An entry level position with mentorship built in.",
    "You are graduating in 2027 with a degree in computer science.",
    "You will complete a Bachelor's degree before the start date.",
    "No prior professional experience required.",
    "0-2 years of experience.",
    "Part of our two-year rotational program.",
])
def test_a_description_can_say_new_grad_when_the_title_does_not(text):
    assert F.newgrad_signal("Software Engineer", text) == "description"


@pytest.mark.parametrize("text", [
    "You have 10 years of experience shipping distributed systems.",
    # an experienced req naming the new grad track to send new grads away
    "Note: if you are an intern, new grad, or staff applicant, please do not "
    "apply using this link and visit our jobs page.",
    "It is not intended for internship, new graduate, or entry-level applicants.",
    # a culture note, not a level
    "A fast-shipping team where early-career engineers get real surface area.",
    "Join a team that has spent 15 years in the industry.",
    "",
])
def test_an_ordinary_description_is_not_a_new_grad_signal(text):
    assert F.newgrad_signal("Software Engineer", text) is None


def test_the_title_outranks_the_description_as_the_reported_signal():
    assert F.newgrad_signal("New Grad Software Engineer", "early career") == "title"


def test_a_stated_experience_bar_beats_new_grad_wording():
    """An experienced req that name-drops the campus program is still senior."""
    assert F.newgrad_signal("Software Engineer", "our new grad program is great",
                            yoe=6) is None
    assert F.newgrad_signal("Software Engineer", "", yoe=0) == "yoe"


def test_a_one_to_three_year_role_is_ranked_up_but_not_called_new_grad():
    """"1-3 years" is reachable; it is not a campus req, and must not shout."""
    assert F.newgrad_signal("Backend Engineer", "1-3 years of experience", yoe=1) is None
    job = F.in_scope({"title": "Backend Engineer", "location": "NYC",
                      "desc": "1-3 years of backend experience", "yoe": 1})
    assert job["tier"] == "experienced" and "apply_now" not in job
    assert job["priority"] > F.TIER_PRIORITY["experienced"]


def test_a_posting_that_rules_new_grads_out_is_not_flagged():
    assert F.newgrad_signal(
        "Software Engineer",
        "This is not an entry level role; new grads are not eligible.") is None


def test_an_internship_is_its_own_tier_not_a_new_grad():
    assert F.newgrad_signal("Software Engineering Intern", "new grad program") is None
    assert F.classify("Software Engineering Intern", text="new grad") == "intern"


def test_the_description_promotes_an_ambiguous_title_to_newgrad():
    assert F.classify("Software Engineer", text="hiring recent graduates") == "newgrad"
    # ...but an explicit level marker in the title is the author being deliberate
    assert F.classify("Software Engineer II", text="hiring recent graduates") == "experienced"


def test_in_scope_flags_new_grad_roles_for_immediate_action():
    job = F.in_scope({"title": "Software Engineer", "location": "Austin, TX",
                      "snippet": "New grad role, class of 2027."})
    assert job["tier"] == "newgrad"
    assert job["apply_now"] is True
    assert job["newgrad_signal"] == "description"
    assert job["priority"] == F.TIER_PRIORITY["newgrad"]


def test_in_scope_leaves_an_ordinary_role_unflagged():
    job = F.in_scope({"title": "Backend Engineer", "location": "Austin, TX",
                      "snippet": "5+ years of experience with Go.", "yoe": 5})
    assert job["tier"] == "experienced"
    assert "apply_now" not in job and "newgrad_signal" not in job
    assert job["priority"] == F.TIER_PRIORITY["experienced"]


def test_new_grad_outranks_every_other_tier():
    p = lambda tier, **kw: F.priority(dict(tier=tier, **kw))
    assert p("newgrad") > p("intern") > p("experienced")
    # an entry-level bar without the words still beats a plain experienced req
    assert p("experienced", yoe=0) > p("experienced")
    # ...but never overtakes an actual new grad posting
    assert p("experienced", yoe=0) < p("newgrad")


def test_a_closing_deadline_lifts_a_posting_within_its_tier():
    from datetime import datetime, timedelta, timezone
    soon = (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat()
    far = (datetime.now(timezone.utc).date() + timedelta(days=90)).isoformat()
    assert (F.priority({"tier": "experienced", "deadline": soon})
            > F.priority({"tier": "experienced", "deadline": far}))
    assert F.priority({"tier": "newgrad", "deadline": "not-a-date"}) == 100
