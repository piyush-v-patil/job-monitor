"""Discord webhook notifications. Set DISCORD_WEBHOOK_URL (GitHub secret)."""
import os
import time

import requests

TIER_LABEL = {"intern": "🎓 Intern", "newgrad": "🌱 New Grad", "experienced": "🛠 Experienced"}
TIER_COLOR = {"intern": 0x3498DB, "newgrad": 0x2ECC71, "experienced": 0xE67E22}
WORKPLACE_ICON = {"Remote": "🏠", "Hybrid": "🔀", "On-site": "🏢"}

# New grad / early career reqs open on a campus cycle and close in days, so
# they are pulled to the front of the message, given their own colour, and say
# out loud what to do about them.
APPLY_NOW_COLOR = 0xF1C40F
APPLY_NOW_BANNER = "🚨 **APPLY IMMEDIATELY**"
# Who to ping when an apply-now posting lands. "@here" by default (this is a
# personal feed); set DISCORD_MENTION to "none" to ping nobody, or to
# "<@your-user-id>" to be pinged even when Discord is closed. An EMPTY value
# means the default, not silence: an unset GitHub Actions variable arrives as
# an empty string, and that must not quietly disable the ping.
DEFAULT_MENTION = "@here"
MENTION_OFF = ("none", "off", "-")


def mention() -> str:
    value = os.environ.get("DISCORD_MENTION", "").strip()
    if not value:
        return DEFAULT_MENTION
    return "" if value.lower() in MENTION_OFF else value
SIGNAL_WHY = {
    "title": "the title says new grad / early career",
    "description": "the description says new grad / early career",
    "yoe": "it asks for 0-1 years of experience",
    "aggregator": "the aggregator lists it as a new grad role",
}


def is_apply_now(j: dict) -> bool:
    """New grad / early career - the postings worth dropping everything for."""
    return bool(j.get("apply_now")) or j.get("tier") == "newgrad"


def _posted(j: dict) -> str:
    return j.get("posted_at") or j.get("first_seen") or ""


def rank(jobs: list) -> list:
    """Highest priority first, newest first within a priority."""
    # sorted() is stable, so ordering by date and then by priority leaves the
    # date order intact inside each priority band - which a single tuple key
    # cannot express, there being no way to reverse a string comparison.
    return sorted(sorted(jobs, key=_posted, reverse=True),
                  key=lambda j: -int(j.get("priority") or 0))


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
    if is_apply_now(j):
        why = SIGNAL_WHY.get(j.get("newgrad_signal", ""), "it is a new grad tier posting")
        out.append({"name": "⚡ Action",
                    "value": f"**Apply now** - {why}. These close fast.",
                    "inline": False})
    return out


def send_alert(broken: list) -> None:
    """Tell Discord a source stopped returning anything.

    Silent breakage is the expensive kind - the Simplify aggregator returned
    zero for weeks behind a green check - so this is worth its own message.
    """
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
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


def send(new_jobs: list, run_label: str = "") -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("DISCORD_WEBHOOK_URL not set - skipping notification")
        return
    if not new_jobs:
        return

    # Urgent first: if the webhook rate-limits or a chunk fails, the postings
    # that had to be seen today are the ones that already went out.
    new_jobs = rank(new_jobs)
    urgent = [j for j in new_jobs if is_apply_now(j)]

    # Discord: max 10 embeds per message, 30 msg/min per webhook.
    sent = failed = 0
    for i in range(0, len(new_jobs), 10):
        chunk = new_jobs[i : i + 10]
        embeds = []
        for j in chunk:
            hot = is_apply_now(j)
            header = f"{TIER_LABEL.get(j['tier'], j['tier'])} · 📍 {j.get('location','')[:150]}"
            if hot:
                header = f"{APPLY_NOW_BANNER}\n{header}"
            snippet = j.get("snippet", "")
            embeds.append({
                "title": (f"🚨 {j['company']} — {j['title']}" if hot
                          else f"{j['company']} — {j['title']}")[:256],
                "url": j["url"],
                "color": APPLY_NOW_COLOR if hot else TIER_COLOR.get(j["tier"], 0x95A5A6),
                "description": (header + (f"\n\n{snippet}" if snippet else ""))[:4096],
                "fields": _fields(j),
                "footer": {"text": f"source: {j.get('source','')} · first seen {j.get('first_seen','')}"},
            })
        payload = {"embeds": embeds}
        if i == 0:
            content = f"**{len(new_jobs)} new posting(s)** {run_label}".strip()
            if urgent:
                banner = (f"{mention()} {APPLY_NOW_BANNER} - {len(urgent)} new grad / "
                          "early career posting(s) in this batch, listed first.").lstrip()
                content = f"{banner}\n{content}"
            payload["content"] = content[:2000]
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
          + (f" ({len(urgent)} flagged apply-immediately)" if urgent else "")
          + (f"; {failed} could not be delivered" if failed else ""))
