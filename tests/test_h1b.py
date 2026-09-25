"""The H-1B index: name matching, the three answer states, and integrity.

No network. What can actually go wrong here is not the download - it is a name
matched to the wrong employer, an employer quietly dropped from the output, or
"we found nothing" being read as "they do not sponsor". Those are what these
cover.
"""
import gzip
import hashlib
import io
import json

import pytest

from monitor import h1b, names


def rec(name, filed=100, cert=None, ly=2026, sv=False, gc=True):
    return {"k": "k-" + name[:8], "n": name, "fy": 2019, "ly": ly,
            "filed": filed, "cert": filed if cert is None else cert,
            "pwd": 0, "perm": 0, "gc": gc, "sv": sv, "sh": 0, "ns": 0}


FILERS = [
    rec("Anthropic, PBC", 433, 422),
    rec("Whatnot Inc.", 125),
    rec("Stripe, LLC", 1819),
    rec("Notion Labs, Inc.", 135),
    rec("Notional Something Foundation, Inc.", 0),
    rec("Booz Allen Hamilton Inc.", 181, 151),
    rec("Space Exploration Technologies Corp.", 80, 73),
    rec("WAL-MART ASSOCIATES, INC.", 23024),
    rec("BEACONFIRE STAFFING SOLUTIONS INC.", 396, sv=True),
    rec("Flex Consulting Group Inc", 124),
    rec("Tiny Shop LLC", 0),
]


@pytest.fixture
def index():
    return h1b.Index(FILERS)


# ---- normalization ---------------------------------------------------------

def test_legal_suffixes_and_punctuation_fall_away(index):
    r, how = index.lookup("Anthropic")
    assert r["n"] == "Anthropic, PBC" and how == "exact"
    assert index.lookup("Whatnot, Inc.")[1] == "exact"
    assert index.lookup("stripe llc")[1] == "exact"


def test_industry_words_are_kept():
    """Dropping "Technologies" would fold distinct filers together - only the
    job de-duplicator may do that, and only because two postings differing by
    it are the same employer."""
    assert "technologies" not in names.LEGAL_SUFFIX
    assert h1b._norm("Space Exploration Technologies Corp.") == "space exploration technologies"


def test_a_word_break_only_one_side_writes(index):
    """DOL files Walmart as WAL-MART, which normalizes to "wal mart"."""
    r, how = index.lookup("Walmart")
    assert r["n"] == "WAL-MART ASSOCIATES, INC."
    # reached by the alias table; the squashed tier is what catches the general case
    assert how in ("alias", "squashed")
    assert h1b._squash("WAL-MART ASSOCIATES, INC.").startswith("walmart")


# ---- aliases ---------------------------------------------------------------

def test_alias_bridges_a_name_sharing_no_token(index):
    r, how = index.lookup("SpaceX")
    assert r["n"] == "Space Exploration Technologies Corp." and how == "alias"


def test_alias_wins_over_a_loose_guess(index):
    """"Walmart Global Tech" would otherwise prefix-match on "walmart"."""
    r, how = index.lookup("Walmart Global Tech")
    assert r["n"] == "WAL-MART ASSOCIATES, INC." and how == "alias"


# ---- prefix matching -------------------------------------------------------

def test_two_token_prefix_is_reported_as_prefix(index):
    r, how = index.lookup("Booz Allen")
    assert r["n"] == "Booz Allen Hamilton Inc." and how == "prefix"


def test_one_token_prefix_is_reported_as_loose(index):
    """A single token is the riskiest tier, so it is named differently - the
    dashboard marks it and shows which employer it landed on."""
    r, how = index.lookup("Notion")
    assert r["n"] == "Notion Labs, Inc." and how == "loose"


def test_prefix_prefers_the_busiest_filer(index):
    """"Notion" must not land on the zero-filing foundation that shares it."""
    assert index.lookup("Notion")[0]["filed"] == 135


def test_a_prefix_candidate_that_never_filed_is_not_used(index):
    """Nothing is gained by badging a non-filer, and it could mislabel."""
    assert index.lookup("Tiny")[0] is None


def test_short_tokens_below_the_floor_are_refused(index, monkeypatch):
    monkeypatch.setattr(h1b, "MIN_PREFIX_TOKEN", 5)
    assert index.lookup("Flex")[0] is None
    monkeypatch.setattr(h1b, "MIN_PREFIX_TOKEN", 3)
    assert index.lookup("Flex")[0]["n"] == "Flex Consulting Group Inc"


def test_an_unrelated_name_matches_nothing(index):
    assert index.lookup("Northrop Grumman") == (None, "")
    assert index.lookup("") == (None, "")


# ---- staffing flag ---------------------------------------------------------

def test_staffing_agencies_are_flagged_not_excluded(index):
    r, _ = index.lookup("BeaconFire Inc.")
    assert r["sv"] is True and r["filed"] == 396
    assert h1b.summarize(r, "loose")["staffing"] is True


# ---- the three answer states ----------------------------------------------

def test_a_company_with_no_match_is_kept_as_null_never_dropped():
    """null means "looked up, nothing found". An absent key means "not looked
    up". Collapsing the two would turn a gap in the disclosure data into a
    claim that the employer does not sponsor."""
    out = h1b.build(FILERS, {"version": "t"}, ["Anthropic", "Northrop Grumman"])
    assert set(out["companies"]) == {"Anthropic", "Northrop Grumman"}
    assert out["companies"]["Northrop Grumman"] is None
    assert out["companies"]["Anthropic"]["filed"] == 433


def test_summarize_carries_only_what_the_surfaces_use():
    s = h1b.summarize(rec("Acme Inc.", 12, 10, ly=2025, sv=True), "exact")
    assert s == {"filed": 12, "cert": 10, "last": 2025, "staffing": True,
                 "green_card": True, "matched": "Acme Inc.", "confidence": "exact"}


def test_build_records_the_data_version_so_staleness_is_visible():
    out = h1b.build(FILERS, {"version": "2026Q3.1", "built_at": "2026-08-27",
                             "employers": 321465}, ["Stripe"])
    assert out["version"] == "2026Q3.1"
    assert out["data_built_at"] == "2026-08-27"
    assert out["source"] == h1b.SOURCE_REPO


# ---- integrity -------------------------------------------------------------

def _gz(records):
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content):
        self.content = content


def _patch_fetch(monkeypatch, blob, sha):
    monkeypatch.setattr(h1b, "session", lambda: FakeSession(blob))
    monkeypatch.setattr(h1b, "get_json",
                        lambda s, url, **kw: {"filename": "index-test.ndjson.gz",
                                              "sha256": sha, "version": "test"})


class FakeSession:
    def __init__(self, blob):
        self.blob = blob

    def get(self, url, **kw):
        return FakeResponse(self.blob)


def test_a_verified_download_parses(monkeypatch):
    blob = _gz(FILERS)
    _patch_fetch(monkeypatch, blob, hashlib.sha256(blob).hexdigest())
    records, manifest = h1b.fetch_index("https://example.test/pointer.json")
    assert len(records) == len(FILERS) and manifest["version"] == "test"


def test_a_checksum_mismatch_aborts_the_build(monkeypatch):
    """Half a dataset reads as "none of these employers ever sponsored anyone",
    which is the one wrong answer this module must never publish."""
    _patch_fetch(monkeypatch, _gz(FILERS), "0" * 64)
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        h1b.fetch_index("https://example.test/pointer.json")


def test_a_manifest_without_a_checksum_still_works(monkeypatch):
    """Absent is not the same as wrong; only a stated-and-different hash fails."""
    blob = _gz(FILERS)
    monkeypatch.setattr(h1b, "session", lambda: FakeSession(blob))
    monkeypatch.setattr(h1b, "get_json",
                        lambda s, url, **kw: {"filename": "x.gz", "version": "test"})
    assert len(h1b.fetch_index("https://example.test/p.json")[0]) == len(FILERS)


# ---- reading it back -------------------------------------------------------

def test_load_treats_a_missing_file_as_no_data(tmp_path):
    """A scan may run before the index has ever been built."""
    assert h1b.load(str(tmp_path / "nope.json")) == {}


def test_load_survives_a_corrupt_file(tmp_path):
    p = tmp_path / "h1b.json"
    p.write_text("{not json", encoding="utf-8")
    assert h1b.load(str(p)) == {}


def test_load_returns_the_companies_map(tmp_path):
    p = tmp_path / "h1b.json"
    p.write_text(json.dumps({"companies": {"Stripe": {"filed": 1819}}}), encoding="utf-8")
    assert h1b.load(str(p))["Stripe"]["filed"] == 1819
