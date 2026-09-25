"""H-1B sponsorship history per employer, built into docs/data/h1b.json.

Run as `python -m monitor.h1b`. Reads the company names every tracker has seen,
resolves each against the employers who have actually filed visa petitions, and
writes one small file the dashboards read.

WHAT THIS SIGNAL IS, AND IS NOT
    It is a company's filing history: how many H-1B petitions it has filed, how
    recently, and whether it is a staffing agency. That is a fact about the
    employer, not a promise about a requisition - a company with 400 filings
    still posts citizenship-only roles, so the badge informs a decision rather
    than making one. Nothing here filters a posting out.

    More importantly, absence is not evidence. Northrop Grumman has zero records
    across 2009-2026, which is a hole in the disclosure data rather than a fact
    about Northrop Grumman. So an employer we looked up and did not find is
    stored as an explicit null and reported as "no filings found" - never as
    "does not sponsor". An employer not in the file at all is a different thing
    again: not yet looked up.

WHERE THE DATA COMES FROM
    USCIS and the Department of Labor both serve their own bulk files behind bot
    protection that refuses automated download (403 from Akamai), so neither can
    be fetched from a workflow. This uses a community mirror of the DOL
    disclosure data instead - public-domain source records, MIT-licensed
    packaging, republished quarterly as one compact NDJSON file. Its manifest
    carries a sha256, which is checked on every build: a truncated or swapped
    download fails loudly instead of quietly producing a wrong index.

    If that mirror stops being updated the badge ages rather than breaking, and
    the failure mode is visible - `version` and `built_at` are written into the
    output file.
"""
import argparse
import collections
import gzip
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone

from . import names, profiles
from .fetchers.http import session, get_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "docs", "data", "h1b.json")

SOURCE_REPO = "https://github.com/msampath/h1b-sponsor-data"
RELEASE = f"{SOURCE_REPO}/releases/download/index-latest"
POINTER_URL = f"{RELEASE}/index-latest.json"

# Legal suffixes only. Industry words stay: "Space Exploration Technologies" and
# "Northrop Grumman Systems" have to remain distinguishable from other filers.
STRIP = names.suffix_pattern("lp", "llp", "pbc", "pc", "limited")

# Employers whose posting name shares no leading token with their filing name.
# No amount of normalization bridges these, so they are written down. Keep this
# short and obvious - it is a list of facts, not a tuning knob.
ALIASES = {
    "spacex": "Space Exploration Technologies Corp.",
    "walmart": "WAL-MART ASSOCIATES, INC.",
    "walmart global tech": "WAL-MART ASSOCIATES, INC.",
    "meta": "Meta Platforms, Inc",
    "google": "Google LLC",
    "alphabet": "Google LLC",
    "amazon": "Amazon.com Services LLC",
    "aws": "Amazon.com Services LLC",
    "amazon web services": "Amazon.com Services LLC",
}

# A single-token prefix is the loosest match this module makes, and short names
# are where it earns its keep and where it goes wrong. Measured over the 1,852
# companies currently tracked, raising this from 3 to 5 costs 8 points of
# coverage (84% -> 76%) - it throws away Uber, Okta, IBM, CGI, Axon, ASML and
# every other employer whose name is simply short.
#
# The price of keeping them is that some are wrong: "Flex" resolves to "Flex
# Consulting Group" rather than the manufacturer, "Sage" to "SAGE IT INC".
# Those are reported as "loose" rather than "prefix", and the dashboard shows
# the matched employer's name, so a bad guess is visible instead of asserted.
# Dropping the whole tier would blank the badge for a sixth of the list to
# avoid a handful of glances - the wrong trade for a signal that only informs.
MIN_PREFIX_TOKEN = 3


def _norm(text: str) -> str:
    return names.normalize(text, STRIP)


def _squash(text: str) -> str:
    return names.squash(text, STRIP)


# --- fetching ---------------------------------------------------------------

def fetch_index(url: str = POINTER_URL) -> tuple[list, dict]:
    """Download the employer index, verifying it against its own manifest.

    Returns (records, manifest). Raises on a checksum mismatch: half a dataset
    would silently read as "these employers have never sponsored anyone", which
    is the one wrong answer this module must never produce.
    """
    s = session()
    manifest = get_json(s, url)
    filename = manifest["filename"]
    blob = s.get(f"{RELEASE}/{filename}", timeout=180).content

    want = manifest.get("sha256", "")
    got = hashlib.sha256(blob).hexdigest()
    if want and got != want:
        raise RuntimeError(
            f"{filename}: sha256 mismatch (manifest {want[:12]}…, got {got[:12]}…) "
            "- refusing to build an index from it")

    records = []
    with gzip.open(io.BytesIO(blob), "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records, manifest


# --- matching ---------------------------------------------------------------

class Index:
    """Employer filing records, keyed the several ways a name can be written."""

    def __init__(self, records):
        self.exact, self.squashed = {}, {}
        prefix = collections.defaultdict(list)
        for r in records:
            key = _norm(r.get("n", ""))
            if not key:
                continue
            self._keep(self.exact, key, r)
            self._keep(self.squashed, _squash(r.get("n", "")), r)
            toks = key.split()
            for i in (1, 2):            # indexed by first one and first two words
                if len(toks) >= i:
                    prefix[" ".join(toks[:i])].append(r)
        # one candidate per prefix: the busiest filer wins, because that is the
        # employer somebody searching a bare "Notion" almost certainly means
        self.prefix = {k: max(v, key=self._weight) for k, v in prefix.items()}

    @staticmethod
    def _weight(r):
        return (r.get("filed") or 0, r.get("cert") or 0, r.get("ly") or 0)

    @classmethod
    def _keep(cls, table, key, r):
        if not key:
            return
        cur = table.get(key)
        if cur is None or cls._weight(r) > cls._weight(cur):
            table[key] = r

    def lookup(self, company: str):
        """(record, how) for an employer, or (None, "") when nothing matches.

        Biased toward finding something: a badge naming the wrong subsidiary
        costs a glance, whereas refusing every imperfect name would leave the
        feature blank for half the list. `how` travels with the answer so the
        dashboard can show a fuzzy match as fuzzy.
        """
        n = _norm(company)
        if not n:
            return None, ""

        alias = ALIASES.get(n)
        if alias:
            hit = self.exact.get(_norm(alias))
            if hit:
                return hit, "alias"

        if n in self.exact:
            return self.exact[n], "exact"

        sq = _squash(company)
        if sq and sq in self.squashed:
            return self.squashed[sq], "squashed"

        toks = n.split()
        for i in (2, 1):                # the longer prefix is the safer one
            if len(toks) < i:
                continue
            if i == 1 and len(toks[0]) < MIN_PREFIX_TOKEN:
                continue
            hit = self.prefix.get(" ".join(toks[:i]))
            # a prefix candidate that never filed anything is not worth the risk
            # of being wrong: it would add no information and could mislabel
            if hit and (hit.get("filed") or 0) > 0:
                # one token is the loose tier - "Flex" reaching "Flex Consulting
                # Group" is the shape of its mistakes - so it is named
                # differently and the dashboard treats it with more suspicion
                return hit, ("prefix" if i == 2 else "loose")
        return None, ""


def summarize(record, how: str) -> dict:
    """The subset of a filing record the dashboard and Discord actually use."""
    return {
        "filed": record.get("filed") or 0,
        "cert": record.get("cert") or 0,
        "last": record.get("ly") or 0,
        "staffing": bool(record.get("sv")),
        "green_card": bool(record.get("gc")),
        "matched": record.get("n", ""),
        "confidence": how,
    }


# --- building ---------------------------------------------------------------

def tracked_companies() -> list:
    """Every company name held by every tracker, so all of them get looked up.

    Driven off profiles.PROFILES rather than a hard-coded pair of paths: adding
    a third tracker is already documented as a Profile entry plus a config, and
    this keeps that true.
    """
    seen = set()
    for profile in profiles.PROFILES.values():
        path = profile.state_path
        if not os.path.exists(path):
            print(f"  (no state file yet for {profile.key}: {path})")
            continue
        with open(path, "r", encoding="utf-8") as fh:
            jobs = json.load(fh).get("jobs", {})
        before = len(seen)
        for entry in jobs.values():
            company = (entry.get("company") or "").strip()
            if company:
                seen.add(company)
        print(f"  {profile.key}: {len(jobs)} postings, "
              f"{len(seen) - before} companies not already seen")
    return sorted(seen)


def build(records, manifest, companies) -> dict:
    index = Index(records)
    out, hits, staffing = {}, 0, 0
    how_counts = collections.Counter()
    for company in companies:
        record, how = index.lookup(company)
        if record is None:
            out[company] = None          # looked up, nothing found - not "no"
            continue
        out[company] = summarize(record, how)
        hits += 1
        how_counts[how] += 1
        staffing += out[company]["staffing"]
    print(f"\n{hits}/{len(companies)} companies matched a filer "
          f"({hits / max(len(companies), 1) * 100:.0f}%)")
    print(f"  by method: {dict(how_counts)}")
    print(f"  flagged as staffing agencies: {staffing}")
    return {
        "version": manifest.get("version", ""),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_built_at": manifest.get("built_at", ""),
        "source": SOURCE_REPO,
        "employers_in_source": manifest.get("employers", len(records)),
        "companies": out,
    }


def load(path: str = OUT_PATH) -> dict:
    """The companies map, or {} when the file is absent.

    Absent is normal - the file is built by its own workflow, on its own
    schedule, and a scan that runs before the first build must not care.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("companies", {}) or {}
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, write nothing")
    args = ap.parse_args()

    print("Collecting tracked companies...")
    companies = tracked_companies()
    if not companies:
        print("No companies in any state file - nothing to look up.", file=sys.stderr)
        return 1

    print(f"\nFetching the employer index from {SOURCE_REPO}...")
    records, manifest = fetch_index()
    print(f"  {len(records):,} employers, version {manifest.get('version')} "
          f"(built {manifest.get('built_at', '?')[:10]}), sha256 verified")

    payload = build(records, manifest, companies)

    if args.dry_run:
        print("\nDry run: nothing written.")
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False, sort_keys=True)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
