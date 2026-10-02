"""State layer: job ids, the slug migration, de-duplication, source health."""
import re

from monitor import state


# ---- A2: ids have to survive being written into an HTML attribute ----------

def test_company_slug_strips_everything_but_alphanumerics():
    assert state.company_slug("Steven's Capital Management") == "steven-s-capital-management"
    assert state.company_slug("DuCharme, McMillen & Associates") == "ducharme-mcmillen-associates"
    assert state.company_slug("  Two  Sigma  ") == "two-sigma"
    assert state.company_slug("") == "unknown"
    assert state.company_slug("!!!") == "unknown"


def test_job_id_is_safe_in_markup():
    for company in ("Steven's Capital Management", 'A "quoted" Co', "<script>", "Lowe's"):
        jid = state.job_id(company, "abc123")
        assert re.fullmatch(r"[a-z0-9-]+:[0-9a-f]{12}", jid), jid


def test_job_id_digest_ignores_the_company_name():
    """Only the prefix is company-derived, which is what lets ids be migrated."""
    a = state.job_id("Steven's Capital Management", "", "https://x.test/jobs/1")
    b = state.job_id("stevens capital management", "", "https://x.test/jobs/1")
    assert a.split(":")[1] == b.split(":")[1]


def test_migrate_ids_rekeys_and_keeps_user_marks():
    st = {"jobs": {
        "steven's-capital-management:3af9029cef14": {
            "company": "Steven's Capital Management", "title": "SWE",
            "status": "applied", "applied_on": "2026-08-20",
        },
        "stripe:0123456789ab": {"company": "Stripe", "title": "SWE", "status": "new"},
    }}
    assert state.migrate_ids(st) == 1
    assert "steven's-capital-management:3af9029cef14" not in st["jobs"]
    moved = st["jobs"]["steven-s-capital-management:3af9029cef14"]
    assert moved["status"] == "applied" and moved["applied_on"] == "2026-08-20"
    assert "stripe:0123456789ab" in st["jobs"]      # already clean, left alone


def test_migrate_ids_is_idempotent():
    st = {"jobs": {"steven's-capital-management:3af9029cef14":
                   {"company": "Steven's Capital Management", "status": "new"}}}
    assert state.migrate_ids(st) == 1
    assert state.migrate_ids(st) == 0


def test_migrate_ids_collision_keeps_the_actioned_copy():
    st = {"jobs": {
        "steven's-capital-management:aaaaaaaaaaaa": {
            "company": "Steven's Capital Management", "status": "applied",
            "applied_on": "2026-08-20"},
        "steven-s-capital-management:aaaaaaaaaaaa": {
            "company": "Steven's Capital Management", "status": "new",
            "comp": "$200k"},
    }}
    state.migrate_ids(st)
    survivor = st["jobs"]["steven-s-capital-management:aaaaaaaaaaaa"]
    assert survivor["status"] == "applied"          # the user's mark wins
    assert survivor["comp"] == "$200k"              # the other copy's field folded in
    assert len(st["jobs"]) == 1


def test_migrate_ids_does_not_renotify_a_migrated_posting():
    """The whole point: after migrating, the next scan must recognise the job."""
    job = {"company": "Steven's Capital Management", "title": "SWE",
           "tier": "experienced", "url": "https://x.test/jobs/12345",
           "external_id": "12345"}
    st = {"jobs": {"steven's-capital-management:"
                   + state.job_id("x", "12345").split(":")[1]: dict(job, status="applied")}}
    state.migrate_ids(st)
    assert state.add_new(st, [dict(job)]) == []     # nothing new -> no Discord message
    assert len(st["jobs"]) == 1
    assert list(st["jobs"].values())[0]["status"] == "applied"


# ---- pre-existing behaviour that must not regress --------------------------

def test_add_new_backfills_enrichment_without_touching_status():
    st = {"jobs": {}}
    job = {"company": "Stripe", "title": "SWE", "tier": "experienced",
           "url": "https://boards.greenhouse.io/stripe/jobs/123456", "external_id": "123456"}
    assert len(state.add_new(st, [dict(job)])) == 1
    jid = next(iter(st["jobs"]))
    st["jobs"][jid]["status"] = "applied"
    assert state.add_new(st, [dict(job, comp="$200k", yoe=0)]) == []
    assert st["jobs"][jid]["status"] == "applied"
    assert st["jobs"][jid]["comp"] == "$200k"
    assert st["jobs"][jid]["yoe"] == 0          # yoe 0 is a real answer, not falsy


def test_canonical_key_collapses_the_same_posting_from_two_sources():
    direct = "https://boards.greenhouse.io/stripe/jobs/123456"
    via_agg = "https://job-boards.greenhouse.io/stripe/jobs/123456?gh_jid=123456"
    assert state.canonical_key("Stripe", direct) == state.canonical_key("Stripe", via_agg)
    assert state.canonical_key("Stripe", "https://stripe.com/careers") == ""


def test_dedupe_keeps_the_actioned_copy():
    st = {"jobs": {
        "a:1": {"company": "Stripe", "url": "https://boards.greenhouse.io/stripe/jobs/123456",
                "status": "new", "first_seen": "2026-01-01"},
        "b:2": {"company": "Stripe", "url": "https://x.test/jobs/123456?gh_jid=123456",
                "status": "applied", "first_seen": "2026-02-01", "comp": "$1"},
    }}
    assert state.dedupe(st) == 1
    survivor = next(iter(st["jobs"].values()))
    assert survivor["status"] == "applied" and survivor["comp"] == "$1"


def test_source_health_reports_a_source_that_just_went_dark():
    st = {"jobs": {}, "sources": {"Stripe": {"best": 500, "last": 480, "last_ok": "2026-08-25"}}}
    broke = state.source_health(st, {"Stripe": 0})
    assert [b["name"] for b in broke] == ["Stripe"]
    assert broke[0]["was"] == 480


def test_source_health_reports_a_never_working_source_exactly_once():
    st = {"jobs": {}, "sources": {}}
    assert [b["name"] for b in state.source_health(st, {"Citadel": 0})] == ["Citadel"]
    assert state.source_health(st, {"Citadel": 0}) == []      # latched
    state.source_health(st, {"Citadel": 12})                  # works -> re-armed
    assert [b["name"] for b in state.source_health(st, {"Citadel": 0})] == ["Citadel"]


# ---- the employer's own link, for a row that came off a board ---------------
# A board row links to the board. Where the employer's own posting for the same
# role is already tracked, that link is the one worth opening - but only when
# the match cannot be a different requisition, because sending someone to the
# wrong office is worse than sending them to LinkedIn.

WORKDAY = "https://adobe.wd5.myworkdayjobs.com/jobs/job/12345"


def ats_row(**kw):
    return dict({"company": "Adobe", "title": "Software Development Engineer",
                 "location": "3 Locations", "url": WORKDAY, "source": "workday"}, **kw)


def board_row(**kw):
    return dict({"company": "Adobe", "title": "Software Development Engineer",
                 "location": "Lehi, UT", "url": "https://www.linkedin.com/jobs/view/1",
                 "external_id": "li-1", "source": "jobspy-linkedin", "tier": "experienced",
                 "soft_dedupe": True}, **kw)


def test_role_key_ignores_the_location_the_two_listings_disagree_about():
    assert state.role_key("Adobe, Inc.", "Software Engineer (R12345)") \
        == state.role_key("Adobe", "Software Engineer")
    assert state.role_key("", "SWE") == "" and state.role_key("Adobe", "") == ""


def test_a_location_naming_a_country_still_names_a_city():
    """The bug this guards: a rule reading the whole string calls
    "Costa Mesa, California, United States" vague, because it ends in a
    country. 151 postings in the tracker are written that way."""
    vague = lambda loc: bool(state.NON_CITY.match(state._city(loc)))
    assert vague("") and vague("3 Locations") and vague("Remote US") and vague("US")
    assert not vague("Costa Mesa, California, United States")
    assert not vague("Lehi, UT")


def test_the_employers_link_reaches_a_board_row_listed_under_another_city():
    """The case this exists for: Workday says "3 Locations", LinkedIn says Lehi."""
    index = state.ats_index({}, [ats_row()])
    assert state.employer_link(board_row(), index) == WORKDAY


def test_one_candidate_is_enough_even_when_the_cities_disagree():
    index = state.ats_index({}, [ats_row(location="San Jose")])
    assert state.employer_link(board_row(), index) == WORKDAY


def test_two_named_cities_hand_the_row_nothing():
    """Two offices advertising one title are two jobs, and guessing between
    them would link to the wrong requisition."""
    index = state.ats_index({}, [ats_row(location="San Jose", url=WORKDAY),
                                 ats_row(location="Seattle", url=WORKDAY + "-b")])
    assert state.employer_link(board_row(), index) == ""


def test_a_matching_city_wins_among_several_candidates():
    index = state.ats_index({}, [ats_row(location="Seattle", url=WORKDAY + "-b"),
                                 ats_row(location="Lehi, UT", url=WORKDAY)])
    assert state.employer_link(board_row(), index) == WORKDAY


def test_the_index_ignores_other_board_rows():
    """Aggregator rows all carry the board's link, so they can only ever
    confirm each other's - the index is employers' own listings only."""
    assert state.ats_index({}, [board_row()]) == {}


def test_add_new_gives_a_board_row_the_employers_link_and_keeps_its_own():
    st = {"jobs": {}}
    state.add_new(st, [ats_row(tier="experienced", external_id="wd-1")])
    state.add_new(st, [board_row(location="Lehi, UT")])
    board = [e for e in st["jobs"].values() if e["source"] == "jobspy-linkedin"]
    assert len(board) == 1, "the two listings are separate rows here, not merged"
    assert board[0]["employer_url"] == WORKDAY
    # the stored url is what the closure sweep probes and what canonical_key
    # reads, so it must be left exactly as the board gave it
    assert board[0]["url"] == "https://www.linkedin.com/jobs/view/1"
    assert board[0]["soft_dedupe"] is True


def test_a_board_row_with_no_employer_listing_gets_no_link():
    st = {"jobs": {}}
    state.add_new(st, [board_row()])
    assert "employer_url" not in next(iter(st["jobs"].values()))


# ---- a derived date is a placeholder, not an answer ------------------------

def test_a_stated_date_replaces_a_derived_one():
    """monitor/backfill.py works a date out from first_seen and flags it. Plain
    backfill semantics would keep that placeholder forever once a board
    published the real date, and the dashboard would keep showing "≈"."""
    entry = {"posted_at": "2026-09-28", "posted_approx": True}
    state.enrich(entry, {"posted_at": "2026-09-24", "comp": "$1"})
    assert entry["posted_at"] == "2026-09-24"
    assert "posted_approx" not in entry
    assert entry["comp"] == "$1"


def test_a_stated_date_is_never_overwritten_by_a_later_scan():
    entry = {"posted_at": "2026-09-24"}
    state.enrich(entry, {"posted_at": "2026-09-28"})
    assert entry["posted_at"] == "2026-09-24"
