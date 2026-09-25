"""Company-name normalization, shared by everything that has to match one.

Two parts of this repo compare company names written by different people for
the same employer, and neither can use the string as given:

  state.soft_key   decides whether a LinkedIn row and a Greenhouse row are the
                   same posting ("Whatnot" vs "Whatnot, Inc.")
  h1b.lookup       decides whether a tracked employer is the one that filed a
                   visa petition ("Anthropic" vs "Anthropic, PBC")

They want slightly different suffix lists - the job matcher also drops
"Technologies", which the visa matcher must keep so that "Space Exploration
Technologies" stays distinguishable - so this module supplies the shared core
and lets each caller extend it, rather than holding one list that is wrong for
somebody or two lists that drift apart.
"""
import re

# Anything that is not a letter, a digit or a space. Punctuation is the most
# common difference between two spellings of one employer ("Whatnot, Inc." /
# "Whatnot Inc"), so it goes before anything else is decided.
NOISE = re.compile(r"[^a-z0-9 ]+")

# Legal-entity words that say nothing about which company this is. Deliberately
# not a place to put industry words: dropping "Technologies" or "Systems" here
# would make the visa matcher confuse companies that differ only by that word.
LEGAL_SUFFIX = ("inc|llc|ltd|corp|corporation|co|company|plc|gmbh|sa|nv|ag"
                "|holdings|group")


def suffix_pattern(*extra: str) -> re.Pattern:
    """The legal-suffix pattern, optionally widened by the caller's own words."""
    parts = "|".join((LEGAL_SUFFIX, *extra)) if extra else LEGAL_SUFFIX
    return re.compile(rf"\b({parts})\b", re.I)


def normalize(text: str, strip: re.Pattern | None = None) -> str:
    """Lowercase, drop punctuation, optionally drop suffixes, collapse spaces."""
    s = NOISE.sub(" ", (text or "").lower())
    if strip is not None:
        s = strip.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def squash(text: str, strip: re.Pattern | None = None) -> str:
    """normalize(), with the spaces removed too.

    The one thing normalization alone cannot reconcile is a word break that
    only one side writes: DOL files Walmart as "WAL-MART ASSOCIATES", which
    normalizes to "wal mart" and never meets "walmart". Removing spaces makes
    those meet, at the cost of also letting genuinely different names collide,
    so callers try it only after an exact match has failed.
    """
    return normalize(text, strip).replace(" ", "")
