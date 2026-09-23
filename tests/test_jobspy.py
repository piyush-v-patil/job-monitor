"""The JobSpy fetcher: board rows in, this repo's job dicts out.

No network here. The boards are mocked, because what can actually break in
this module is the translation: a NaN reaching the dashboard as the string
"nan", a proxy URL read out of a committed config, a LinkedIn row tracked as
a second copy of a job the employer's own board already gave us.
"""
import pytest

from monitor import state
from monitor.fetchers import FETCHERS, jobspy_board


class FakeFrame:
    """Stands in for the DataFrame scrape_jobs returns."""

    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def to_dict(self, _orient):
        return list(self._rows)


def fake_scraper(rows, captured=None):
    def scrape_jobs(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return FakeFrame(rows)
    return lambda: scrape_jobs


ROW = {
    "id": "li-4461500405",
    "site": "linkedin",
    "job_url": "https://www.linkedin.com/jobs/view/4461500405",
    "job_url_direct": None,
    "title": "Software Engineer, 2027 New Grad",
    "company": "Whatnot",
    "location": "New York, NY",
    "date_posted": "2026-09-22",
    "job_type": "fulltime",
    "min_amount": 120000.0,
    "max_amount": 160000.0,
    "interval": "yearly",
    "currency": "USD",
    "is_remote": False,
    "job_function": "Engineering",
    "description": None,
}


def run(monkeypatch, cfg, rows=(ROW,), captured=None):
    monkeypatch.setattr(jobspy_board, "_scrape_jobs", fake_scraper(list(rows), captured))
    cfg = dict({"name": "LinkedIn (JobSpy)", "delay_seconds": 0}, **cfg)
    return jobspy_board.jobspy(cfg)


# ---- registration ----------------------------------------------------------

def test_fetcher_is_registered_under_the_name_the_config_uses():
    assert FETCHERS["jobspy"] is jobspy_board.jobspy


# ---- field mapping ---------------------------------------------------------

def test_a_board_row_becomes_a_job_dict(monkeypatch):
    job, = run(monkeypatch, {"search": "software engineer"})
    assert job["company"] == "Whatnot"
    assert job["title"] == "Software Engineer, 2027 New Grad"
    assert job["location"] == "New York, NY"
    assert job["url"] == "https://www.linkedin.com/jobs/view/4461500405"
    assert job["external_id"] == "li-4461500405"
    assert job["source"] == "jobspy-linkedin"
    assert job["posted_at"] == "2026-09-22"
    assert job["comp"] == "$120,000 - $160,000 / yearly"
    assert job["employment_type"] == "Full-time"
    assert job["department"] == "Engineering"


def test_the_employers_own_link_is_preferred_over_the_boards(monkeypatch):
    """job_url_direct is the apply page; the LinkedIn URL is a wrapper."""
    row = dict(ROW, job_url_direct="https://boards.greenhouse.io/whatnot/jobs/4055123")
    job, = run(monkeypatch, {"search": "x"}, [row])
    assert job["url"] == "https://boards.greenhouse.io/whatnot/jobs/4055123"
    # and that URL carries an ATS id, so the ordinary de-duplication now works
    assert state.canonical_key(job["company"], job["url"]) == "whatnot#4055123"


def test_missing_cells_never_reach_the_dashboard_as_the_word_nan(monkeypatch):
    """pandas fills every column a board skipped, and str(NaN) is 'nan'."""
    row = dict(ROW, date_posted=float("nan"), min_amount=float("nan"),
               max_amount=float("nan"), interval=float("nan"),
               job_type=float("nan"), job_function=float("nan"))
    job, = run(monkeypatch, {"search": "x"}, [row])
    for field in ("posted_at", "comp", "employment_type", "department"):
        assert job[field] == "", (field, job[field])


def test_a_half_open_salary_range_still_reads(monkeypatch):
    row = dict(ROW, max_amount=None)
    job, = run(monkeypatch, {"search": "x"}, [row])
    assert job["comp"] == "$120,000 / yearly"


def test_rows_missing_an_identity_are_dropped(monkeypatch):
    rows = [dict(ROW, company=""), dict(ROW, title=None), dict(ROW, job_url="")]
    assert run(monkeypatch, {"search": "x"}, rows) == []


def test_remote_postings_are_marked(monkeypatch):
    job, = run(monkeypatch, {"search": "x"}, [dict(ROW, is_remote=True)])
    assert job["workplace"] == "Remote"


# ---- search arguments ------------------------------------------------------

def test_config_drives_the_search_arguments(monkeypatch):
    got = {}
    run(monkeypatch, {"search": "data engineer", "sites": ["linkedin", "indeed"],
                      "location": "Austin, TX", "results_wanted": 50,
                      "hours_old": 24, "distance": 25}, captured=got)
    assert got["site_name"] == ["linkedin", "indeed"]
    assert got["search_term"] == "data engineer"
    assert got["location"] == "Austin, TX"
    assert got["results_wanted"] == 50
    assert got["hours_old"] == 24
    assert got["distance"] == 25
    assert got["linkedin_fetch_description"] is False


def test_descriptions_are_off_unless_asked_for(monkeypatch):
    """One extra request per job: opt-in, and the snippet follows it."""
    row = dict(ROW, description="We ask for 3 years of professional experience.")
    job, = run(monkeypatch, {"search": "x"}, [row])
    assert job["snippet"] == "" and job["yoe"] is None

    got = {}
    job, = run(monkeypatch, {"search": "x", "fetch_description": True}, [row], got)
    assert got["linkedin_fetch_description"] is True
    assert job["snippet"].startswith("We ask for 3 years")
    assert job["yoe"] == 3


def test_proxies_come_from_the_environment_not_the_committed_config(monkeypatch):
    got = {}
    monkeypatch.delenv("JOBSPY_PROXIES", raising=False)
    run(monkeypatch, {"search": "x"}, captured=got)
    assert "proxies" not in got

    monkeypatch.setenv("LINKEDIN_PROXIES", "http://a:b@p1:8080, http://p2:8080")
    run(monkeypatch, {"search": "x", "proxies_env": "LINKEDIN_PROXIES"}, captured=got)
    assert got["proxies"] == ["http://a:b@p1:8080", "http://p2:8080"]


def test_a_missing_dependency_is_reported_not_raised_as_importerror():
    """monitor.fetchers imports this module at startup; an ImportError at
    module scope would take every ATS fetcher down with it."""
    import builtins
    real = builtins.__import__

    def refuse(name, *a, **k):
        if name == "jobspy":
            raise ImportError("no module named jobspy")
        return real(name, *a, **k)

    builtins.__import__ = refuse
    try:
        with pytest.raises(RuntimeError, match="python-jobspy is not installed"):
            jobspy_board._scrape_jobs()
    finally:
        builtins.__import__ = real


# ---- de-duplication against the employers' own boards ----------------------

def test_soft_key_matches_the_same_posting_across_two_listings():
    board = state.soft_key("Whatnot", "Software Engineer, 2027 New Grad", "New York, NY")
    ats = state.soft_key("Whatnot, Inc.", "Software Engineer 2027 New Grad",
                         "New York, New York, United States")
    assert board and board == ats


def test_soft_key_ignores_req_numbers():
    assert (state.soft_key("Stripe", "Software Engineer (R12345)", "Seattle, WA")
            == state.soft_key("Stripe", "Software Engineer", "Seattle, WA"))


def test_soft_key_keeps_genuinely_different_postings_apart():
    a = state.soft_key("Stripe", "Software Engineer", "Seattle, WA")
    assert a != state.soft_key("Stripe", "Data Engineer", "Seattle, WA")
    assert a != state.soft_key("Stripe", "Software Engineer", "New York, NY")
    assert a != state.soft_key("Databricks", "Software Engineer", "Seattle, WA")


def test_soft_key_is_empty_when_a_part_is_missing():
    """Better a duplicate row than a match loose enough to hide a posting."""
    assert state.soft_key("Stripe", "Software Engineer", "") == ""
    assert state.soft_key("", "Software Engineer", "Seattle, WA") == ""
    assert state.soft_key("Stripe", "", "Seattle, WA") == ""


def ats_job(**kw):
    return dict({"company": "Whatnot", "title": "Software Engineer, 2027 New Grad",
                 "location": "New York, NY", "tier": "newgrad",
                 "url": "https://boards.greenhouse.io/whatnot/jobs/4055123",
                 "external_id": "4055123", "source": "greenhouse"}, **kw)


def board_job(**kw):
    return dict({"company": "Whatnot", "title": "Software Engineer, 2027 New Grad",
                 "location": "New York, NY", "tier": "newgrad",
                 "url": "https://www.linkedin.com/jobs/view/4461500405",
                 "external_id": "li-4461500405", "source": "jobspy-linkedin",
                 "posted_at": "2026-09-22", "soft_dedupe": True}, **kw)


def test_one_job_on_two_boards_is_tracked_and_notified_once():
    st = {"jobs": {}}
    new = state.add_new(st, [board_job(), ats_job()])
    assert len(new) == 1 and len(st["jobs"]) == 1
    entry, = st["jobs"].values()
    # the employer's own listing is the one kept, whichever order they arrive in
    assert entry["source"] == "greenhouse"
    assert entry["url"] == "https://boards.greenhouse.io/whatnot/jobs/4055123"
    # and the board's enrichment is folded onto it rather than lost
    assert entry["posted_at"] == "2026-09-22"


def test_the_board_row_does_not_re_notify_a_job_already_tracked():
    st = {"jobs": {}}
    state.add_new(st, [ats_job()])
    assert state.add_new(st, [board_job()]) == []
    assert len(st["jobs"]) == 1


def test_an_ats_listing_upgrades_a_job_first_seen_on_a_board():
    st = {"jobs": {}}
    state.add_new(st, [board_job()])
    assert state.add_new(st, [ats_job()]) == []
    entry, = st["jobs"].values()
    assert entry["url"] == "https://boards.greenhouse.io/whatnot/jobs/4055123"
    assert entry["source"] == "greenhouse"
    assert "soft_dedupe" not in entry     # no longer the weaker copy


def test_two_ats_listings_are_never_merged_by_the_soft_key():
    """The soft match is for aggregator rows only - two ATS rows that happen
    to share a title are two requisitions until an id says otherwise."""
    st = {"jobs": {}}
    a = ats_job()
    b = ats_job(url="https://boards.greenhouse.io/whatnot/jobs/4055999", external_id="4055999")
    assert len(state.add_new(st, [a, b])) == 2
