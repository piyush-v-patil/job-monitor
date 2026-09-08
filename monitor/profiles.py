"""Tracker profiles: two independent job monitors sharing one engine.

Everything that differs between the trackers lives here - which companies are
scanned, which role rules admit a posting, which file the results are stored
in, which dashboard reads that file, and which Discord webhook is notified.
Everything else (fetchers, de-duplication, state, source health, prune) is
profile-agnostic and shared, so a fix to the engine lands in both trackers at
once instead of being ported by hand.

  tech         - the original: SWE and adjacent, intern -> ~5 years.
  supplychain  - demand planning, forecasting and the planning family around
                 it; analyst -> manager.

Adding a third tracker is a Profile entry, a companies-*.yaml, a dashboard
page, and a workflow - no changes to the engine.
"""
import os
from dataclasses import dataclass
from types import ModuleType

from . import filters, filters_scm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    config: str        # company registry, under config/
    state: str         # tracker file, under docs/data/
    dashboard: str     # page that reads it, under docs/
    webhook_env: str   # env var holding this tracker's Discord webhook
    rules: ModuleType  # the filters module deciding what is in scope
    tiers: tuple       # (key, discord label, colour) in display order

    @property
    def config_path(self) -> str:
        return os.path.join(ROOT, "config", self.config)

    @property
    def state_path(self) -> str:
        return os.path.join(ROOT, "docs", "data", self.state)

    @property
    def tier_labels(self) -> dict:
        return {k: label for k, label, _ in self.tiers}

    @property
    def tier_colors(self) -> dict:
        return {k: color for k, _, color in self.tiers}


PROFILES = {
    "tech": Profile(
        key="tech",
        label="Software & adjacent",
        config="companies.yaml",
        state="jobs.json",
        dashboard="index.html",
        webhook_env="DISCORD_WEBHOOK_URL",
        rules=filters,
        tiers=(("intern", "🎓 Intern", 0x3498DB),
               ("newgrad", "🌱 New Grad", 0x2ECC71),
               ("experienced", "🛠 Experienced", 0xE67E22)),
    ),
    "supplychain": Profile(
        key="supplychain",
        label="Supply chain planning",
        config="companies-supplychain.yaml",
        state="supplychain.json",
        dashboard="supplychain.html",
        webhook_env="DISCORD_WEBHOOK_URL_SUPPLYCHAIN",
        rules=filters_scm,
        tiers=(("intern", "🎓 Intern / Co-op", 0x3498DB),
               ("entry", "🌱 Entry / Associate", 0x2ECC71),
               ("mid", "📈 Analyst / Planner", 0xE67E22),
               ("manager", "🧭 Manager / Lead", 0x9B59B6)),
    ),
}


def get(key: str) -> Profile:
    try:
        return PROFILES[key]
    except KeyError:
        raise SystemExit(f"unknown profile '{key}' "
                         f"(known: {', '.join(PROFILES)})") from None
