"""The Oracle Recruiting Cloud fetcher: requisitions in, job dicts out.

No network. What can actually break here is not the translation but the
reading: this ATS puts a whole corporation on one requisition list, answers
100 per page newest-first, and silently omits the list altogether if the
`finder` string is missing `facetsList`. Each of those cost a board's worth
of postings once, so each has a test.
"""
import re

import pytest

from monitor.fetchers import FETCHERS, generic

CFG = {"name": "Testco", "host": "abcd.fa.us2.oraclecloud.com", "site": "CX_1"}


def req(n, **over):
    """One requisition as the API hands it over."""
    j = {
        "Id": f"REQ{n}",
        "Title": "Software Engineer",
        "PrimaryLocation": "Austin, TX",
        "PrimaryLocationCountry": "US",
        "PostedDate": "2026-10-01",
        "PostingEndDate": "2026-11-01",
        "WorkerType": "fulltime",
        "WorkplaceTypeCode": "ORA_HYBRID",
        "JobFamily": "Engineering",
        "ShortDescriptionStr": "<p>5+ years of experience building services.</p>",
        "secondaryLocations": [{"Name": "Dallas, TX"}, {"Name": "Plano, TX"},
                               {"Name": "Houston, TX"}],
    }
    j.update(over)
    return j


def page(rows):
    return {"items": [{"requisitionList": rows}]}


def stub(monkeypatch, pages):
    """Serve `pages` in order, recording the params of each call.

    The requested `limit` is honoured, as the real endpoint does, so a test
    asking for a partial last page gets one.
    """
    calls = []

    def fake(_s, url, **kw):
        params = kw.get("params", {})
        calls.append({"url": url, **params})
        body = pages[min(len(calls) - 1, len(pages) - 1)]
        limit = int(re.search(r"limit=(\d+)", params.get("finder", "limit=100"))[1])
        rows = (body.get("items") or [{}])[0].get("requisitionList") or []
        return page(rows[:limit]) if len(rows) > limit else body

    monkeypatch.setattr(generic, "get_json", fake)
    monkeypatch.setattr(generic, "session", lambda *a, **k: None)
    return calls


def finders(calls):
    return [c["finder"] for c in calls]


def test_a_requisition_becomes_a_job_dict(monkeypatch):
    stub(monkeypatch, [page([req(1)])])
    job, = generic.oraclecloud(CFG)
    assert job["company"] == "Testco"
    assert job["external_id"] == "REQ1"
    assert job["source"] == "oraclecloud"
    assert job["url"] == ("https://abcd.fa.us2.oraclecloud.com/hcmUI/"
                          "CandidateExperience/en/sites/CX_1/job/REQ1")
    assert job["posted_at"] == "2026-10-01"
    assert job["workplace"] == "Hybrid"
    assert job["employment_type"] == "Full-time"
    assert job["department"] == "Engineering"
    assert job["yoe"] == 5


def test_only_two_secondary_locations_ride_along(monkeypatch):
    # A requisition open in fifteen cities would otherwise fill the column.
    stub(monkeypatch, [page([req(1)])])
    job, = generic.oraclecloud(CFG)
    assert job["location"] == "Austin, TX; Dallas, TX; Plano, TX"


def test_the_facets_list_is_always_asked_for(monkeypatch):
    # Without it the endpoint answers 200 with the requisition list omitted,
    # which reads as an employer with no openings rather than as an error.
    calls = stub(monkeypatch, [page([req(1)])])
    generic.oraclecloud(CFG)
    assert "facetsList=" in finders(calls)[0]


def test_a_short_page_ends_the_read(monkeypatch):
    calls = stub(monkeypatch, [page([req(n) for n in range(40)])])
    assert len(generic.oraclecloud(CFG)) == 40
    assert len(calls) == 1


def test_a_full_board_is_read_past_the_first_page(monkeypatch):
    # The defect this pins: one page is 100 requisitions of every function the
    # company is hiring for, so a bank's newest 100 can hold two engineers.
    # Whatever the page size, the fetcher keeps asking until the board ends.
    full = page([req(n) for n in range(100)])
    calls = stub(monkeypatch, [full, full, page([req(900)])])
    assert len(generic.oraclecloud(CFG)) == 201
    assert [c["finder"].count("offset=0") for c in calls] == [1, 0, 0]
    assert "offset=100" in finders(calls)[1]
    assert "offset=200" in finders(calls)[2]


def test_the_read_stops_at_max_results(monkeypatch):
    full = page([req(n) for n in range(100)])
    calls = stub(monkeypatch, [full] * 9)
    assert len(generic.oraclecloud(dict(CFG, max_results=250))) == 250
    assert len(calls) == 3
    assert "limit=50" in finders(calls)[-1]   # the last page asks for the remainder


def test_the_default_read_is_deep_enough_to_pass_one_page(monkeypatch):
    full = page([req(n) for n in range(100)])
    calls = stub(monkeypatch, [full] * 20)
    jobs = generic.oraclecloud(CFG)
    assert len(jobs) >= 500 and len(calls) >= 5


def test_a_search_term_narrows_server_side(monkeypatch):
    # The escape hatch for boards bigger than max_results: let the ATS filter
    # by keyword instead of having the page limit cut the list off by date.
    calls = stub(monkeypatch, [page([req(1)])])
    generic.oraclecloud(dict(CFG, search="software"))
    assert "keyword=software" in finders(calls)[0]


def test_no_search_term_means_no_keyword(monkeypatch):
    calls = stub(monkeypatch, [page([req(1)])])
    generic.oraclecloud(CFG)
    assert "keyword" not in finders(calls)[0]


def test_an_empty_board_is_not_an_error(monkeypatch):
    stub(monkeypatch, [{"items": []}])
    assert generic.oraclecloud(CFG) == []


def test_the_fetcher_is_registered_under_its_config_name(monkeypatch):
    assert FETCHERS["oraclecloud"] is generic.oraclecloud


@pytest.mark.parametrize("code,expected", [
    ("ORA_REMOTE", "Remote"), ("ORA_ONSITE", "On-site"),
    ("ORA_HYBRID", "Hybrid"), ("", ""), ("ORA_SOMETHING_NEW", ""),
])
def test_workplace_codes_translate_or_stay_empty(monkeypatch, code, expected):
    stub(monkeypatch, [page([req(1, WorkplaceTypeCode=code)])])
    job, = generic.oraclecloud(CFG)
    assert job["workplace"] == expected
