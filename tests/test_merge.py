"""Reconciling two copies of jobs.json after a push race."""
from monitor import merge


def base(**extra):
    doc = {"version": 1, "updated": "2026-08-26T12:00:00Z",
           "jobs": {"stripe:aaa": {"company": "Stripe", "title": "SWE", "status": "new"}}}
    doc.update(extra)
    return doc


# ---- A1: the source-health block must survive the merge --------------------

def test_sources_survive_the_merge():
    ours = base(sources={"Stripe": {"best": 500, "last": 480, "last_ok": "2026-08-26"}})
    theirs = base(sources={"Stripe": {"best": 500, "last": 480, "last_ok": "2026-08-26"}})
    out = merge.merge(ours, theirs)
    assert "sources" in out
    assert out["sources"]["Stripe"]["best"] == 500


def test_sources_are_unioned_across_both_copies():
    """A big-tech run and a full sweep each hold health for a different tier."""
    ours = base(sources={"Stripe": {"best": 500, "last": 480, "last_ok": "2026-08-26"}})
    theirs = base(sources={"Target": {"best": 190, "last": 188, "last_ok": "2026-08-25"}})
    out = merge.merge(ours, theirs)
    assert set(out["sources"]) == {"Stripe", "Target"}


def test_unknown_top_level_keys_are_not_dropped():
    """The bug was structural - the result named its keys, so new ones vanished."""
    out = merge.merge(base(somethingNew={"a": 1}), base())
    assert "somethingNew" not in out          # ours does not invent keys on the branch
    out = merge.merge(base(), base(somethingNew={"a": 1}))
    assert out["somethingNew"] == {"a": 1}    # ...but the branch's are carried through


def test_source_high_water_mark_is_monotone():
    ours = base(sources={"Stripe": {"best": 592, "last": 0, "last_ok": "2026-08-20"}})
    theirs = base(sources={"Stripe": {"best": 500, "last": 480, "last_ok": "2026-08-26"}})
    rec = merge.merge(ours, theirs)["sources"]["Stripe"]
    assert rec["best"] == 592                 # larger count reached wins
    assert rec["last_ok"] == "2026-08-26"     # later successful run wins


def test_source_last_count_keeps_the_healthier_side():
    """A 0 may just mean that run did not cover this tier.

    Keeping a stale non-zero only delays an alert by one scan; keeping a stale
    0 clears the trigger and the breakage is never reported at all.
    """
    ours = base(sources={"Stripe": {"best": 500, "last": 0, "last_ok": "2026-08-20"}})
    theirs = base(sources={"Stripe": {"best": 500, "last": 480, "last_ok": "2026-08-26"}})
    assert merge.merge(ours, theirs)["sources"]["Stripe"]["last"] == 480


def test_reported_dead_latch_clears_if_either_side_saw_the_source_work():
    both = base(sources={"Citadel": {"best": 0, "last": 0, "last_ok": None,
                                     "reported_dead": "2026-08-24"}})
    kept = merge.merge(both, both)["sources"]["Citadel"]
    assert kept["reported_dead"] == "2026-08-24"          # neither side saw it work

    recovered = base(sources={"Citadel": {"best": 9, "last": 9, "last_ok": "2026-08-26"}})
    rearmed = merge.merge(recovered, both)["sources"]["Citadel"]
    assert "reported_dead" not in rearmed                 # one side did -> re-arm


# ---- pre-existing ownership rules that must not regress --------------------

def test_branch_wins_on_user_owned_fields():
    ours = base()
    theirs = base()
    theirs["jobs"]["stripe:aaa"] = {"company": "Stripe", "title": "SWE",
                                    "status": "applied", "applied_on": "2026-08-26"}
    out = merge.merge(ours, theirs)
    assert out["jobs"]["stripe:aaa"]["status"] == "applied"
    assert out["jobs"]["stripe:aaa"]["applied_on"] == "2026-08-26"


def test_this_run_contributes_jobs_the_branch_has_not_seen():
    ours = base()
    ours["jobs"]["figma:bbb"] = {"company": "Figma", "title": "SWE", "status": "new"}
    out = merge.merge(ours, base())
    assert set(out["jobs"]) == {"stripe:aaa", "figma:bbb"}


def test_enrichment_backfills_but_never_overwrites():
    ours = base()
    ours["jobs"]["stripe:aaa"] = {"company": "Stripe", "title": "SWE",
                                  "comp": "$200k", "department": "Payments"}
    theirs = base()
    theirs["jobs"]["stripe:aaa"] = {"company": "Stripe", "title": "SWE",
                                    "status": "applied", "department": "Core"}
    out = merge.merge(ours, theirs)["jobs"]["stripe:aaa"]
    assert out["comp"] == "$200k"          # missing -> filled in
    assert out["department"] == "Core"     # already present -> untouched
    assert out["status"] == "applied"


def test_updated_takes_the_later_stamp():
    ours = base(updated="2026-08-26T12:00:00Z")
    theirs = base(updated="2026-08-26T14:00:00Z")
    assert merge.merge(ours, theirs)["updated"] == "2026-08-26T14:00:00Z"
