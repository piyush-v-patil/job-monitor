"""The two trackers, and the plumbing that keeps them apart.

The failure this guards against is quiet and expensive: one tracker writing
into the other's database, or notifying the other's Discord channel. Both
would look like a successful run.
"""
import os

import pytest
import yaml

from monitor import main, profiles


def test_both_profiles_are_registered():
    assert set(profiles.PROFILES) == {"tech", "supplychain"}


def test_unknown_profile_fails_loudly():
    with pytest.raises(SystemExit):
        profiles.get("nope")


@pytest.mark.parametrize("key", ["tech", "supplychain"])
def test_every_profile_points_at_files_that_exist(key):
    p = profiles.get(key)
    assert os.path.exists(p.config_path), p.config_path
    assert os.path.exists(os.path.join(profiles.ROOT, "docs", p.dashboard))


def test_the_trackers_never_share_a_file_or_a_webhook():
    tech, scm = profiles.get("tech"), profiles.get("supplychain")
    assert tech.state_path != scm.state_path
    assert tech.config_path != scm.config_path
    assert tech.dashboard != scm.dashboard
    assert tech.webhook_env != scm.webhook_env


@pytest.mark.parametrize("key", ["tech", "supplychain"])
def test_tier_vocabulary_covers_what_the_rules_emit(key):
    """Every tier a profile's filters can return has a Discord label."""
    p = profiles.get(key)
    labels = p.tier_labels
    assert labels and len(labels) == len(p.tiers)
    assert len(set(p.tier_colors.values())) == len(p.tiers)   # no repeated hue
    for tier in labels:
        assert isinstance(labels[tier], str) and labels[tier]


def test_config_entries_all_name_a_known_fetcher():
    """A typo in `fetcher:` is otherwise only visible as a skipped source."""
    from monitor.fetchers import FETCHERS
    for key in profiles.PROFILES:
        cfg = main.load_config(profiles.get(key).config_path)
        for section in ("bigtech", "other", "aggregators"):
            for company in cfg.get(section) or []:
                assert company.get("fetcher") in FETCHERS, (key, company)
                assert company.get("name"), (key, company)


# ---- multi-term searches ---------------------------------------------------

def test_searches_runs_the_fetcher_once_per_term_and_merges():
    """One phrase never covers a job family on a keyword-search board."""
    seen = []

    def fake(c):
        seen.append(c["search"])
        # each term finds one unique posting plus one they all share
        return [{"external_id": c["search"], "title": c["search"]},
                {"external_id": "shared", "title": "shared"}]

    main.FETCHERS["_fake"] = fake
    try:
        jobs = main.run_fetcher({"name": "T", "fetcher": "_fake",
                                 "searches": ["demand planning", "S&OP"]})
    finally:
        del main.FETCHERS["_fake"]
    assert seen == ["demand planning", "S&OP"]
    assert len(jobs) == 3                      # the duplicate is folded in
    assert {j["external_id"] for j in jobs} == {"demand planning", "S&OP", "shared"}


def test_the_newest_first_workday_pass_runs_only_once():
    """Repeating it per term would double the request count for nothing."""
    flags = []

    def fake(c):
        flags.append(c.get("skip_recent", False))
        return []

    main.FETCHERS["_fake"] = fake
    try:
        main.run_fetcher({"name": "T", "fetcher": "_fake", "searches": ["a", "b", "c"]})
    finally:
        del main.FETCHERS["_fake"]
    assert flags == [False, True, True]


def test_one_failing_term_does_not_lose_the_others():
    def fake(c):
        if c["search"] == "boom":
            raise RuntimeError("endpoint changed")
        return [{"external_id": c["search"], "title": c["search"]}]

    main.FETCHERS["_fake"] = fake
    try:
        jobs = main.run_fetcher({"name": "T", "fetcher": "_fake",
                                 "searches": ["boom", "fine"]})
    finally:
        del main.FETCHERS["_fake"]
    assert [j["external_id"] for j in jobs] == ["fine"]


def test_a_source_that_fails_every_term_reports_nothing():
    """An all-failed source must read as 0, so source-health flags it."""
    def fake(c):
        raise RuntimeError("dead")

    main.FETCHERS["_fake"] = fake
    try:
        assert main.run_fetcher({"name": "T", "fetcher": "_fake",
                                 "searches": ["a", "b"]}) == []
    finally:
        del main.FETCHERS["_fake"]


def test_yaml_anchor_block_is_not_scanned_as_companies():
    """companies-supplychain.yaml holds its shared search list at top level."""
    cfg = main.load_config(profiles.get("supplychain").config_path)
    assert "x-searches" in cfg                 # the anchor block is present...
    companies = (cfg.get("bigtech") or []) + (cfg.get("other") or [])
    assert all(isinstance(c, dict) and "fetcher" in c for c in companies)
