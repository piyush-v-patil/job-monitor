"""The offline pass: dating the LinkedIn backlog, and linking it to employers.

No network. What can break here is judgement about which rows qualify: dating
a posting whose board simply never published a date (where first_seen says
nothing), overwriting a real date, quietly passing a derived date off as a
stated one, or rewriting the board url the closure sweep needs.
"""
from monitor import backfill


def rows(**overrides):
    base = {
        # the case this exists for: LinkedIn called it under a day old, so the
        # date was dropped, so first_seen is within a day of the posting date
        "li-undated": {"company": "Lyft", "title": "SWE", "source": "jobspy-linkedin",
                       "first_seen": "2026-09-24", "status": "new"},
        "li-dated": {"company": "Affirm", "title": "SWE", "source": "jobspy-linkedin",
                     "first_seen": "2026-09-24", "posted_at": "2026-09-22", "status": "new"},
        # an ATS may have sat on a posting for a month before we arrived, so
        # first_seen says nothing about when it went up
        "ats-undated": {"company": "Stripe", "title": "SWE", "source": "greenhouse",
                        "first_seen": "2026-09-24", "status": "new"},
    }
    base.update(overrides)
    return base


def test_an_undated_linkedin_posting_is_dated_from_when_it_was_first_seen():
    assert backfill.find_dates(rows(), "2026-09-30") == {"li-undated": "2026-09-24"}


def test_a_posting_that_already_has_a_date_is_never_touched():
    jobs = rows()
    backfill.apply_dates(jobs, backfill.find_dates(jobs, "2026-09-30"))
    assert jobs["li-dated"]["posted_at"] == "2026-09-22"
    assert "posted_approx" not in jobs["li-dated"]


def test_other_sources_are_left_alone():
    """first_seen only bounds the posting date where the board told us the
    posting was fresh. Nowhere else does it mean anything."""
    assert "ats-undated" not in backfill.find_dates(rows(), "2026-09-30")


def test_the_derived_date_says_that_it_is_derived():
    jobs = rows()
    backfill.apply_dates(jobs, backfill.find_dates(jobs, "2026-09-30"))
    assert jobs["li-undated"]["posted_at"] == "2026-09-24"
    assert jobs["li-undated"]["posted_approx"] is True


def test_the_cutoff_excludes_postings_seen_after_the_fix_went_live():
    """Past that day, an undated row means LinkedIn served a card with no age
    on it at all - a different failure, where the inference does not hold."""
    assert backfill.find_dates(rows(), "2026-09-23") == {}


def test_a_posting_with_no_first_seen_is_skipped():
    jobs = rows(broken={"company": "X", "title": "Y", "source": "jobspy-linkedin",
                        "status": "new"})
    assert "broken" not in backfill.find_dates(jobs, "2026-09-30")


def test_the_pass_is_idempotent():
    jobs = rows()
    backfill.apply_dates(jobs, backfill.find_dates(jobs, "2026-09-30"))
    assert backfill.find_dates(jobs, "2026-09-30") == {}


# ---- the employer's link, for rows a scan will never see again -------------
# A LinkedIn sweep only asks for the last 72 hours, so a posting found last
# week never passes through a scan again. Everything stored is matched here.

WORKDAY = "https://adobe.wd5.myworkdayjobs.com/jobs/job/12345"


def pair():
    return {
        "li:1": {"company": "Adobe", "title": "Software Development Engineer",
                 "location": "Lehi, UT", "url": "https://www.linkedin.com/jobs/view/1",
                 "source": "jobspy-linkedin", "soft_dedupe": True,
                 "first_seen": "2026-09-20", "status": "new"},
        "wd:2": {"company": "Adobe", "title": "Software Development Engineer",
                 "location": "3 Locations", "url": WORKDAY, "source": "workday",
                 "first_seen": "2026-09-20", "status": "new"},
    }


def test_a_stored_board_row_gains_the_employers_link():
    assert backfill.find_links(pair()) == {"li:1": WORKDAY}


def test_the_board_url_is_left_exactly_as_it_was():
    """It is what monitor/expire.py probes and what canonical_key reads; a
    rewrite would drop the row out of the closure sweep and let dedupe()
    delete one of the two copies."""
    jobs = pair()
    backfill.apply_links(jobs, backfill.find_links(jobs))
    assert jobs["li:1"]["url"] == "https://www.linkedin.com/jobs/view/1"
    assert jobs["li:1"]["employer_url"] == WORKDAY
    assert jobs["li:1"]["soft_dedupe"] is True


def test_a_row_that_already_has_a_link_is_not_revisited():
    jobs = pair()
    jobs["li:1"]["employer_url"] = "https://kept.test/jobs/9"
    assert backfill.find_links(jobs) == {}


def test_an_employers_own_row_is_never_given_a_link():
    """Only board rows link to a board; the rest already point at the source."""
    assert "wd:2" not in backfill.find_links(pair())


def test_the_link_pass_is_idempotent():
    jobs = pair()
    backfill.apply_links(jobs, backfill.find_links(jobs))
    assert backfill.find_links(jobs) == {}
