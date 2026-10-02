"""Closed-posting detection: what counts as an answer, and what is left alone.

No network. What can actually break here is judgement, not HTTP: reading a
sign-in wall as "gone", reading an employer's own prose as a closure, spending
the whole budget re-asking about postings already known closed, or writing over
a mark the user made.
"""
from monitor import expire

TOPCARD = '<section class="top-card-layout"><h2 class="topcard__title">SWE</h2></section>'
CLOSED_FIGURE = ('<figure class="closed-job closed-job__flavor topcard__flavor-row">'
                 '<figcaption class="closed-job__flavor--closed">'
                 'No longer accepting applications</figcaption></figure>')


class FakeResponse:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


class FakeSession:
    """Answers by URL; records what it was asked, in order."""

    def __init__(self, answers, default=None):
        self.answers = answers
        self.default = default or FakeResponse(200, TOPCARD)
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(url)
        answer = self.answers.get(url, self.default)
        if isinstance(answer, Exception):
            raise answer
        return answer


def job(jid="brightway:aaa", **extra):
    j = {"company": "Brightway", "title": "Mid-Level Software Engineer",
         "location": "Tampa, FL",
         # one URL per id: the fake session answers by URL, and a shared one
         # would quietly make every posting in a test look like the same page
         "url": "https://www.linkedin.com/jobs/view/" + jid.replace(":", "-"),
         "source": "jobspy-linkedin", "first_seen": "2026-09-21", "status": "new"}
    j.update(extra)
    return {jid: j}


# ---- what the page has to say before a verdict is drawn --------------------

def test_closed_marker_closes_the_posting():
    s = FakeSession({}, default=FakeResponse(200, TOPCARD + CLOSED_FIGURE))
    assert expire.probe(s, "u") == expire.CLOSED_STATE


def test_a_job_page_without_the_marker_is_open():
    s = FakeSession({}, default=FakeResponse(200, TOPCARD))
    assert expire.probe(s, "u") == expire.OPEN


def test_a_deleted_posting_is_gone():
    s = FakeSession({}, default=FakeResponse(404, ""))
    assert expire.probe(s, "u") == expire.GONE


def test_a_sign_in_wall_is_not_an_answer():
    """The failure that would empty the tracker: 200, but not the job page.

    LinkedIn answers a throttled or logged-out request with a page that has no
    apply state on it. Reading "no closed marker" off that is how every posting
    in the feed gets marked open forever; reading the 200 as gone is how they
    all get marked closed.
    """
    s = FakeSession({}, default=FakeResponse(200, "<html>Sign in to continue</html>"))
    assert expire.probe(s, "u") == expire.UNKNOWN


def test_throttling_is_not_an_answer():
    for code in (429, 999, 503):
        s = FakeSession({}, default=FakeResponse(code, ""))
        assert expire.probe(s, "u") == expire.UNKNOWN


def test_a_dead_connection_is_not_an_answer():
    import requests
    s = FakeSession({}, default=requests.RequestException("reset"))
    assert expire.probe(s, "u") == expire.UNKNOWN


def test_the_employers_own_prose_is_not_a_closure():
    """A description can say the words; only the top card's markup counts."""
    body = (TOPCARD + "<div class='description'>We are no longer accepting "
            "applications for the 2026 cohort; the 2027 posting opens in May."
            "</div>")
    s = FakeSession({}, default=FakeResponse(200, body))
    assert expire.probe(s, "u") == expire.OPEN


# ---- what a run writes ----------------------------------------------------

def test_a_closure_is_recorded_without_touching_status():
    jobs = job(status="applied", applied_on="2026-09-22")
    s = FakeSession({}, default=FakeResponse(200, TOPCARD + CLOSED_FIGURE))
    expire.run(jobs, limit=10, delay=0, s=s, today="2026-09-29")
    entry = jobs["brightway:aaa"]
    assert entry["closed_at"] == "2026-09-29"
    assert entry["status"] == "applied"        # the mark is the user's, not ours
    assert entry["applied_on"] == "2026-09-22"


def test_an_open_posting_records_only_that_it_was_asked():
    jobs = job()
    s = FakeSession({}, default=FakeResponse(200, TOPCARD))
    expire.run(jobs, limit=10, delay=0, s=s, today="2026-09-29")
    entry = jobs["brightway:aaa"]
    assert entry["checked_at"] == "2026-09-29"
    assert "closed_at" not in entry


def test_no_answer_writes_nothing_at_all():
    """An unreadable response must not look like a check that happened."""
    jobs = job()
    s = FakeSession({}, default=FakeResponse(429, ""))
    expire.run(jobs, limit=10, delay=0, s=s, today="2026-09-29")
    assert jobs["brightway:aaa"].keys() == job()["brightway:aaa"].keys()


def test_a_run_stops_once_the_answers_stop():
    """Ten unreadable responses in a row is a block; carrying on deepens it."""
    jobs = {}
    for i in range(40):
        jobs.update(job(f"co{i:02d}:aaa"))
    s = FakeSession({}, default=FakeResponse(999, ""))
    expire.run(jobs, limit=40, delay=0, s=s)
    assert len(s.asked) == 10


def test_one_good_answer_re_arms_the_run():
    """A single throttled response mid-run is not an outage."""
    jobs = {}
    for i in range(20):
        jobs.update(job(f"co{i:02d}:aaa"))
    answers = {j["url"]: FakeResponse(429, "") for i, j in enumerate(jobs.values())
               if i % 2}
    s = FakeSession(answers, default=FakeResponse(200, TOPCARD))
    t = expire.run(jobs, limit=20, delay=0, s=s)
    assert len(s.asked) == 20 and t[expire.OPEN] == 10


# ---- which postings a run spends its budget on ----------------------------

def test_postings_already_known_closed_are_not_asked_again():
    jobs = {**job("a:aaa", closed_at="2026-09-25"), **job("b:bbb")}
    picked = [jid for jid, _ in expire.candidates(jobs, limit=10)]
    assert picked == ["b:bbb"]


def test_boards_this_cannot_read_are_left_alone():
    """Every board words its closed state differently; a guess hides a live job."""
    jobs = {**job("gh:aaa", url="https://boards.greenhouse.io/acme/jobs/12345",
                  source="greenhouse"),
            **job("li:bbb")}
    picked = [jid for jid, _ in expire.candidates(jobs, limit=10)]
    assert picked == ["li:bbb"]


def test_the_least_recently_asked_go_first():
    jobs = {**job("asked-today:aaa", checked_at="2026-09-29"),
            **job("asked-last-week:bbb", checked_at="2026-09-22"),
            **job("never-asked:ccc")}
    picked = [jid for jid, _ in expire.candidates(jobs, limit=10)]
    assert picked == ["never-asked:ccc", "asked-last-week:bbb", "asked-today:aaa"]


def test_among_equals_the_oldest_posting_goes_first():
    jobs = {**job("fresh:aaa", posted_at="2026-09-29"),
            **job("stale:bbb", posted_at="2026-09-10")}
    picked = [jid for jid, _ in expire.candidates(jobs, limit=10)]
    assert picked == ["stale:bbb", "fresh:aaa"]


def test_the_limit_is_a_budget_not_a_filter():
    jobs = {}
    for i in range(10):
        jobs.update(job(f"co{i:02d}:aaa"))
    assert len(expire.candidates(jobs, limit=3)) == 3
    assert len(expire.candidates(jobs, limit=0)) == 10


# ---- the date the page states, off the request we already made -------------

AGED = TOPCARD + ('<span class="posted-time-ago__text topcard__flavor--metadata">'
                  '\n            6 days ago\n          </span>')


def test_the_pages_own_age_dates_a_row_that_arrived_undated():
    """The closure sweep already holds this page, so a date costs no request."""
    import datetime
    jobs = job()
    s = FakeSession({}, default=FakeResponse(200, AGED))
    t = expire.run(jobs, limit=10, delay=0, s=s, today="2026-09-30")
    entry = jobs["brightway:aaa"]
    want = (datetime.datetime.now(datetime.timezone.utc).date()
            - datetime.timedelta(days=6)).isoformat()
    assert entry["posted_at"] == want
    # "6 days ago" is a phrase, not a date the employer published
    assert entry["posted_approx"] is True
    assert t["dated"] == 1


def test_a_row_that_already_has_a_date_keeps_it():
    jobs = job(posted_at="2026-09-21")
    s = FakeSession({}, default=FakeResponse(200, AGED))
    t = expire.run(jobs, limit=10, delay=0, s=s)
    assert jobs["brightway:aaa"]["posted_at"] == "2026-09-21"
    assert "posted_approx" not in jobs["brightway:aaa"]
    assert t["dated"] == 0


def test_a_page_that_states_no_age_leaves_the_row_undated():
    jobs = job()
    s = FakeSession({}, default=FakeResponse(200, TOPCARD))
    expire.run(jobs, limit=10, delay=0, s=s)
    assert "posted_at" not in jobs["brightway:aaa"]


def test_no_answer_never_dates_a_row():
    """A sign-in wall carries no age, and a throttled reply carries no page."""
    for answer in (FakeResponse(200, "<html>Sign in</html>"), FakeResponse(429, "")):
        jobs = job()
        expire.run(jobs, limit=10, delay=0, s=FakeSession({}, default=answer))
        assert "posted_at" not in jobs["brightway:aaa"]


def test_page_date_reads_the_phrase_and_nothing_else():
    assert expire.page_date(AGED)
    assert expire.page_date(TOPCARD) == ""
    assert expire.page_date("") == ""


def test_a_row_carrying_an_employer_link_is_still_probed():
    """The employer's link lives in its own field precisely so the board url
    stays put - if it ever replaced `url`, these rows would silently drop out
    of the sweep and never be checked again."""
    jobs = job(employer_url="https://adobe.wd5.myworkdayjobs.com/jobs/job/12345")
    assert [jid for jid, _ in expire.candidates(jobs, limit=10)] == ["brightway:aaa"]
