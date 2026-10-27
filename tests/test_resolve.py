"""Looking LinkedIn rows up on the employer's own board.

No network: the board reads are faked. What can break here is judgement -
finding the wrong board for a company, or linking a row to another office's
requisition for the same title, which sends you to apply for the wrong job.
"""
import pytest

from monitor import merge, resolve

TODAY = "2026-10-27"
CROWE_WD = "https://crowe.wd12.myworkdayjobs.com/en-US/external_careers"


def li(company="Crowe", title="Software Engineer", location="Chicago, IL, USA", **extra):
    return {"company": company, "title": title, "location": location,
            "url": "https://www.linkedin.com/jobs/view/1", "source": "jobspy-linkedin",
            "soft_dedupe": True, "first_seen": "2026-10-25", "status": "new", **extra}


def posting(title, location, url):
    return {"company": "", "title": title, "location": location, "url": url}


def tracked():
    """A Simplify row that tells us where Crowe posts."""
    return {"crowe:x": {"company": "Crowe LLP", "title": "Analyst", "source": "simplify-github",
                        "url": f"{CROWE_WD}/job/Chicago-IL/Analyst_R1"}}


# ---- which board -----------------------------------------------------------

@pytest.mark.parametrize("url,want", [
    ("https://job-boards.greenhouse.io/affirm/jobs/8011590003",
     {"fetcher": "greenhouse", "token": "affirm"}),
    ("https://boards.greenhouse.io/stripe/jobs/123456",
     {"fetcher": "greenhouse", "token": "stripe"}),
    ("https://boards.greenhouse.io/embed/job_app?for=figma&token=123456",
     {"fetcher": "greenhouse", "token": "figma"}),
    ("https://jobs.lever.co/palantir/0b1c2d3e-0000-4000-8000-123456789abc",
     {"fetcher": "lever", "token": "palantir"}),
    ("https://jobs.ashbyhq.com/niantic-spatial/ed68b062-4dcb-4deb-9cc0-ed38030f6e43",
     {"fetcher": "ashby", "token": "niantic-spatial"}),
    (f"{CROWE_WD}/job/Chicago-IL/SWE_R1",
     {"fetcher": "workday", "host": "crowe.wd12.myworkdayjobs.com", "tenant": "crowe",
      "site": "external_careers"}),
    ("https://abb.wd3.myworkdayjobs.com/external_career_page/job/USA-NC-Cary/Intern_JR1",
     {"fetcher": "workday", "host": "abb.wd3.myworkdayjobs.com", "tenant": "abb",
      "site": "external_career_page"}),
    ("https://wd5.myworkdaysite.com/recruiting/angc/ANGC/job/Augusta-Office/Data-Engineer_R1",
     {"fetcher": "workday", "host": "wd5.myworkdaysite.com", "tenant": "angc", "site": "ANGC",
      "url_prefix": "https://wd5.myworkdaysite.com/en-US/recruiting/angc/ANGC"}),
    ("https://www.linkedin.com/jobs/view/123", None),
    ("https://careers.example.com/jobs/123456", None),
])
def test_board_is_read_from_a_posting_url(url, want):
    assert resolve.board_from_url(url) == want


def test_a_company_board_comes_from_its_tracked_postings():
    known = resolve.boards([], tracked())
    assert known["crowe"]["site"] == "external_careers"


def test_the_registry_wins_over_a_tracked_url():
    reg = [{"name": "Crowe", "fetcher": "greenhouse", "token": "crowe", "searches": ["x"]},
           {"name": "Unsearchable", "fetcher": "phenom", "host": "x"}]
    known = resolve.boards(reg, tracked())
    assert known["crowe"] == {"name": "Crowe", "fetcher": "greenhouse", "token": "crowe"}
    assert "unsearchable" not in known


def test_linkedin_rows_never_say_where_a_company_posts():
    jobs = {"a": li(url=f"{CROWE_WD}/job/Chicago-IL/SWE_R1")}
    assert resolve.boards([], jobs) == {}


# ---- which rows ------------------------------------------------------------

def test_only_open_recent_unlinked_unsearched_rows_at_known_companies_qualify():
    jobs = dict(tracked(), **{
        "want": li(),
        "linked": li(employer_url="https://crowe.example/1"),
        "closed": li(status="closed"),
        "gone": li(closed_at="2026-10-26"),
        "old": li(first_seen="2026-09-01"),
        "searched": li(employer_checked="2026-10-24"),
        "searched-long-ago": li(employer_checked="2026-10-01", first_seen="2026-10-02"),
        "unknown-company": li(company="Nobody Inc"),
    })
    known = resolve.boards([], jobs)
    assert resolve.candidates(jobs, known, TODAY) == ["want", "searched-long-ago"]


# ---- what is accepted ------------------------------------------------------

def test_the_same_title_in_the_same_city_links():
    row = li(location="Morrisville, NC, USA")
    posts = [posting("Software Engineer", "United States-North Carolina-Morrisville", "u1"),
             posting("Software Engineer", "Austin, TX", "u2")]
    assert resolve.match(row, posts, whole_board=False) == "u1"


def test_a_posting_that_names_no_city_links():
    posts = [posting("Software Engineer (R12345)", "3 Locations", "u1")]
    assert resolve.match(li(), posts, whole_board=False) == "u1"


def test_the_only_hit_of_a_search_in_another_city_is_not_taken():
    """A top-20 search is not the whole board: the one 'Software Engineer II' it
    returned can be another office's req (RTX Cambridge vs El Segundo)."""
    row = li(company="RTX", title="Software Engineer II", location="Cambridge, MA, USA")
    posts = [posting("Software Engineer II", "US-CA-EL SEGUNDO-E01", "u1")]
    assert resolve.match(row, posts, whole_board=False) == ""
    assert resolve.match(row, posts, whole_board=True) == ""


def test_york_is_not_new_york():
    row = li(location="York, PA, USA")
    assert resolve.match(row, [posting("Software Engineer", "New York, NY", "u1")], False) == ""


def test_two_offices_with_the_same_title_link_nothing():
    posts = [posting("Software Engineer", "Remote", "u1"), posting("Software Engineer", "2 Locations", "u2")]
    assert resolve.match(li(), posts, whole_board=False) == ""


def test_a_row_naming_no_city_takes_the_only_posting_on_a_whole_board():
    row = li(location="United States")
    posts = [posting("Software Engineer", "Seattle, WA", "u1"), posting("Data Engineer", "Seattle", "u2")]
    assert resolve.match(row, posts, whole_board=True) == "u1"
    assert resolve.match(row, posts, whole_board=False) == ""


def test_another_title_never_links():
    assert resolve.match(li(), [posting("Senior Software Engineer", "Chicago, IL", "u1")], False) == ""


# ---- the run ---------------------------------------------------------------

def test_a_run_links_matches_stamps_misses_and_leaves_url_alone():
    jobs = dict(tracked(), hit=li(), miss=li(title="Data Engineer"))
    known = resolve.boards([], jobs)
    searched = []

    def fetch(board, title):
        searched.append(resolve.search_text(title))
        return [posting("Software Engineer", "Chicago, IL", f"{CROWE_WD}/job/Chicago/SWE_R9")]

    links, checked = resolve.resolve(jobs, known, TODAY, resolve.Resolver(10, fetch, pace=0))
    resolve.apply(jobs, links, checked, TODAY)
    assert jobs["hit"]["employer_url"] == f"{CROWE_WD}/job/Chicago/SWE_R9"
    assert jobs["hit"]["url"] == "https://www.linkedin.com/jobs/view/1"
    assert "employer_url" not in jobs["miss"]
    assert jobs["miss"]["employer_checked"] == jobs["hit"]["employer_checked"] == TODAY
    assert sorted(searched) == ["Data Engineer", "Software Engineer"]   # Workday: one search per title


def test_a_whole_board_is_read_once_for_every_row_at_that_company():
    jobs = {"a": li(company="Stripe"), "b": li(company="Stripe", title="Data Engineer")}
    known = resolve.boards([{"name": "Stripe", "fetcher": "greenhouse", "token": "stripe"}], jobs)
    calls = []
    resolver = resolve.Resolver(10, lambda b, t: calls.append(t) or [], pace=0)
    resolve.resolve(jobs, known, TODAY, resolver)
    assert len(calls) == 1 and resolver.requests == {"board": 1}


def test_a_failing_board_is_skipped_and_its_rows_are_tried_again_next_run():
    jobs = {"a": li(company="Stripe"), "b": li(company="Stripe", title="Data Engineer"),
            "c": li(company="Plaid")}
    reg = [{"name": "Stripe", "fetcher": "greenhouse", "token": "stripe"},
           {"name": "Plaid", "fetcher": "lever", "token": "plaid"}]

    def fetch(board, title):
        if board["token"] == "stripe":
            raise RuntimeError("404")
        return [posting("Software Engineer", "Chicago", "https://jobs.lever.co/plaid/1")]

    resolver = resolve.Resolver(10, fetch, pace=0)
    links, checked = resolve.resolve(jobs, resolve.boards(reg, jobs), TODAY, resolver)
    assert checked == ["c"] and links == {"c": "https://jobs.lever.co/plaid/1"}
    assert "greenhouse:stripe" in resolver.failed and resolver.requests["board"] == 2


def test_the_request_limit_stops_the_run_and_leaves_the_rest_unstamped():
    jobs = dict(tracked(), **{f"r{i}": li(title=f"Engineer {i}") for i in range(5)})
    resolver = resolve.Resolver(2, lambda b, t: [], pace=0)
    links, checked = resolve.resolve(jobs, resolve.boards([], jobs), TODAY, resolver)
    assert len(checked) == 2 and sum(resolver.requests.values()) == 2


def test_a_dry_run_saves_nothing(tmp_path, monkeypatch):
    path = tmp_path / "jobs.json"
    path.write_text('{"version": 1, "jobs": {}}')

    class P:
        key = "test"
        state_path = str(path)
        config_path = str(tmp_path / "companies.yaml")
    (tmp_path / "companies.yaml").write_text("other: []\n")
    monkeypatch.setattr(resolve.profiles, "get", lambda key: P)
    monkeypatch.setattr(resolve, "known_boards", lambda profile, jobs: {})
    monkeypatch.setattr(resolve.state, "save", lambda *a: pytest.fail("saved on a dry run"))
    assert resolve.main(["--dry-run"]) == 0


def test_the_later_search_date_survives_a_merge():
    ours = {"jobs": {"a": {"employer_checked": "2026-10-27"}}}
    theirs = {"jobs": {"a": {"employer_checked": "2026-10-20"}}}
    assert merge.merge(ours, theirs)["jobs"]["a"]["employer_checked"] == "2026-10-27"
    assert merge.merge(theirs, ours)["jobs"]["a"]["employer_checked"] == "2026-10-27"


# ---- more boards -----------------------------------------------------------

@pytest.mark.parametrize("url,want", [
    ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210512345",
     {"fetcher": "oraclecloud", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001"}),
    ("https://jobs.smartrecruiters.com/ServiceNow/744000012345678",
     {"fetcher": "smartrecruiters", "token": "ServiceNow"}),
])
def test_oracle_and_smartrecruiters_boards_are_read_from_posting_urls(url, want):
    assert resolve.board_from_url(url) == want


def test_an_alias_shares_the_board_of_the_company_it_names():
    reg = [{"name": "RTX", "fetcher": "workday", "host": "globalhr.wd5.myworkdayjobs.com",
            "tenant": "globalhr", "site": "REC_RTX_Ext_Gateway"}]
    known = resolve.boards(reg, {}, {"Collins Aerospace": "RTX", "Nobody": "Unknown Co"})
    assert known["collins aerospace"] is known["rtx"]
    assert "nobody" not in known


def test_an_alias_never_replaces_a_board_the_name_already_has():
    reg = [{"name": "RTX", "fetcher": "greenhouse", "token": "rtx"},
           {"name": "Raytheon", "fetcher": "greenhouse", "token": "raytheon"}]
    assert resolve.boards(reg, {}, {"Raytheon": "RTX"})["raytheon"]["token"] == "raytheon"


def test_listed_boards_outrank_a_board_read_from_tracked_postings():
    jobs = {"g": {"company": "GDIT", "url": "https://gdit.wd5.myworkdayjobs.com/en-US/gdit_earlytalent/job/x/Intern_R1"}}
    listed = [{"name": "GDIT", "fetcher": "workday", "host": "gdit.wd5.myworkdayjobs.com",
               "tenant": "gdit", "site": "External_Career_Site"}]
    assert resolve.boards([], jobs)["gdit"]["site"] == "gdit_earlytalent"
    assert resolve.boards(listed, jobs)["gdit"]["site"] == "External_Career_Site"


def test_scan_settings_never_reach_a_search():
    reg = [{"name": "Microsoft", "fetcher": "microsoft", "pages": 15, "searches": ["a", "b"]}]
    assert resolve.boards(reg, {})["microsoft"] == {"name": "Microsoft", "fetcher": "microsoft"}


def test_boards_that_cannot_search_are_left_out():
    reg = [{"name": c, "fetcher": f} for c, f in (("Pepsi", "phenom"), ("Apple", "apple"),
                                                   ("Two Sigma", "avature"), ("Feed", "jobspy"))]
    assert resolve.boards(reg, {}) == {}


def test_both_trackers_share_their_boards(monkeypatch, tmp_path):
    import json
    from monitor import profiles

    def tracker(key, rows):
        (tmp_path / f"{key}.json").write_text(json.dumps({"jobs": rows}))
        (tmp_path / f"{key}.yaml").write_text("other: []\n")

        class P:
            pass
        P.key, P.state_path, P.config_path = key, str(tmp_path / f"{key}.json"), str(tmp_path / f"{key}.yaml")
        return P

    tech = tracker("tech", tracked())
    scm = tracker("scm", {"li": li(title="Demand Planner")})
    monkeypatch.setattr(profiles, "PROFILES", {"tech": tech, "scm": scm})
    monkeypatch.setattr(resolve, "extra", lambda: ([], {}))
    assert resolve.known_boards(scm, {"li": li(title="Demand Planner")})["crowe"]["tenant"] == "crowe"


@pytest.mark.parametrize("fetcher,want", [
    ("oraclecloud", {"max_results": 20}),
    ("microsoft", {"pages": 2}),
    ("google", {"pages": 1}),
    ("walmart", {"max_results": 20}),
    ("amazon", {"category": ""}),
    ("smartrecruiters", {}),
    ("eightfold", {}),
    ("uber", {}),
])
def test_a_searchable_board_is_asked_for_the_title_once(monkeypatch, fetcher, want):
    calls = []
    monkeypatch.setitem(resolve.FETCHERS, fetcher, lambda c: calls.append(c) or [])
    resolve.fetch({"name": "Co", "fetcher": fetcher, "host": "h", "token": "t"},
                  "Software Engineer, Backend (R12345)")
    (c,) = calls
    assert c["search"] == "Software Engineer Backend" and c["skip_recent"] is True
    assert {k: c[k] for k in want} == want


def test_a_whole_board_is_read_without_a_search(monkeypatch):
    calls = []
    monkeypatch.setitem(resolve.FETCHERS, "lever", lambda c: calls.append(c) or [])
    resolve.fetch({"name": "Co", "fetcher": "lever", "token": "co"}, "Engineer")
    assert calls == [{"name": "Co", "fetcher": "lever", "token": "co"}]


def test_each_title_is_its_own_search_on_a_searching_board():
    jobs = {"a": li(company="JPMC"), "b": li(company="JPMC"), "c": li(company="JPMC", title="SRE")}
    reg = [{"name": "JPMC", "fetcher": "oraclecloud", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001"}]
    resolver = resolve.Resolver(10, lambda b, t: [], pace=0)
    resolve.resolve(jobs, resolve.boards(reg, {}), TODAY, resolver)
    assert resolver.requests == {"search": 2}


def test_the_listed_boards_and_aliases_load_and_point_at_searchable_boards():
    entries, aliases = resolve.extra()
    assert entries and aliases
    assert all(resolve.searchable(b) for b in entries)
    assert all(isinstance(a, str) and isinstance(o, str) for a, o in aliases.items())


def test_smartrecruiters_passes_a_search_through(monkeypatch):
    from monitor.fetchers import generic
    seen = {}
    monkeypatch.setattr(generic, "get_json", lambda s, url, **kw: seen.update(url=url, **kw) or {"content": []})
    generic.smartrecruiters({"name": "ServiceNow", "token": "ServiceNow", "search": "data engineer"})
    assert seen["params"] == {"limit": 100, "q": "data engineer"}
    generic.smartrecruiters({"name": "ServiceNow", "token": "ServiceNow"})
    assert seen["params"] == {"limit": 100}


def test_amazon_searches_every_job_family_only_when_asked(monkeypatch):
    from monitor.fetchers import custom
    urls = []
    monkeypatch.setattr(custom, "get_json", lambda s, url, **kw: urls.append(url) or {"jobs": []})
    custom.amazon({"name": "Amazon"})
    custom.amazon({"name": "Amazon", "category": "", "search": "demand planner"})
    assert "category%5B%5D=software-development" in urls[0]
    assert "category" not in urls[1] and "base_query=demand+planner" in urls[1]
