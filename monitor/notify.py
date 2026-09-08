"""Discord webhook notifications. Set DISCORD_WEBHOOK_URL (GitHub secret)."""
import os
import time

import requests

# The tech tracker's vocabulary, and the fallback for any caller that does
# not pass its own. A profile supplies its tiers via profiles.Profile.
TIER_LABEL = {"intern": "🎓 Intern", "newgrad": "🌱 New Grad", "experienced": "🛠 Experienced"}
TIER_COLOR = {"intern": 0x3498DB, "newgrad": 0x2ECC71, "experienced": 0xE67E22}

DEFAULT_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"
WORKPLACE_ICON = {"Remote": "🏠", "Hybrid": "🔀", "On-site": "🏢"}


def _fields(j: dict) -> list:
    """Inline embed fields, skipping anything the ATS did not provide."""
    out = []
    if j.get("comp"):
        out.append({"name": "💰 Compensation", "value": j["comp"][:1024], "inline": True})
    if j.get("posted_at"):
        out.append({"name": "📅 Posted", "value": j["posted_at"], "inline": True})
    workplace = j.get("workplace", "")
    etype = j.get("employment_type", "")
    if workplace or etype:
        icon = WORKPLACE_ICON.get(workplace, "")
        out.append({"name": "🧭 Type",
                    "value": " · ".join(filter(None, [f"{icon} {workplace}".strip(), etype])),
                    "inline": True})
    if j.get("department"):
        out.append({"name": "🗂 Team", "value": j["department"][:1024], "inline": True})
    return out


def send_alert(broken: list, webhook_env: str = DEFAULT_WEBHOOK_ENV) -> None:
    """Tell Discord a source stopped returning anything.

    Silent breakage is the expensive kind - the Simplify aggregator returned
    zero for weeks behind a green check - so this is worth its own message.
    """
    url = os.environ.get(webhook_env, "").strip()
    if not url or not broken:
        return
    lines = "\n".join(
        (f"- **{b['name']}** - has never returned a posting; the fetcher is "
         "broken, not the company"
         if b.get("never") else
         f"- **{b['name']}** - was returning {b['was']}, now 0"
         + (f" (last had postings {b['since']})" if b.get("since") else ""))
        for b in broken[:20])
    payload = {"embeds": [{
        "title": f"⚠️ {len(broken)} source(s) returning no postings",
        "description": (lines + "\n\nLikely a changed endpoint or an expired ATS "
                        "token. Jobs from these companies are being missed until "
                        "it is fixed.")[:4000],
        "color": 0xE67E22,
    }]}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        print(f"Alerted Discord: {len(broken)} broken source(s)")
    except Exception as e:  # noqa: BLE001 - an alert must never fail the run
        print(f"could not send source alert: {e}")


def send(new_jobs: list, run_label: str = "", webhook_env: str = DEFAULT_WEBHOOK_ENV,
         tier_labels: dict | None = None, tier_colors: dict | None = None) -> None:
    labels = tier_labels or TIER_LABEL
    colors = tier_colors or TIER_COLOR
    url = os.environ.get(webhook_env, "").strip()
    if not url:
        print(f"{webhook_env} not set - skipping notification")
        return
    if not new_jobs:
        return

    # Discord: max 10 embeds per message, 30 msg/min per webhook.
    sent = failed = 0
    for i in range(0, len(new_jobs), 10):
        chunk = new_jobs[i : i + 10]
        embeds = []
        for j in chunk:
            header = f"{labels.get(j['tier'], j['tier'])} · 📍 {j.get('location','')[:150]}"
            snippet = j.get("snippet", "")
            embeds.append({
                "title": f"{j['company']} — {j['title']}"[:256],
                "url": j["url"],
                "color": colors.get(j["tier"], 0x95A5A6),
                "description": (header + (f"\n\n{snippet}" if snippet else ""))[:4096],
                "fields": _fields(j),
                "footer": {"text": f"source: {j.get('source','')} · first seen {j.get('first_seen','')}"},
            })
        payload = {"embeds": embeds}
        if i == 0:
            payload["content"] = f"**{len(new_jobs)} new posting(s)** {run_label}".strip()
        # One bad message must not take the run down with it. The state has
        # already been saved by this point, so an exception here would kill the
        # process, skip the workflow's commit step, and throw the whole scan
        # away - after which the next run re-discovers these jobs and re-sends
        # every chunk that did get through. Losing one message beats that.
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 429:
                time.sleep(float(resp.json().get("retry_after", 2)))
                resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            sent += len(chunk)
        except Exception as e:  # noqa: BLE001
            failed += len(chunk)
            print(f"could not deliver {len(chunk)} posting(s): {e}")
        time.sleep(1)
    print(f"Notified Discord: {sent} job(s)"
          + (f"; {failed} could not be delivered" if failed else ""))
