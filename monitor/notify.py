"""Discord webhook notifications. Set DISCORD_WEBHOOK_URL (GitHub secret)."""
import os
import time

import requests

TIER_LABEL = {"intern": "🎓 Intern", "newgrad": "🌱 New Grad", "experienced": "🛠 Experienced"}
TIER_COLOR = {"intern": 0x3498DB, "newgrad": 0x2ECC71, "experienced": 0xE67E22}
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


def send(new_jobs: list, run_label: str = "") -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("DISCORD_WEBHOOK_URL not set - skipping notification")
        return
    if not new_jobs:
        return

    # Discord: max 10 embeds per message, 30 msg/min per webhook.
    for i in range(0, len(new_jobs), 10):
        chunk = new_jobs[i : i + 10]
        embeds = []
        for j in chunk:
            header = f"{TIER_LABEL.get(j['tier'], j['tier'])} · 📍 {j.get('location','')[:150]}"
            snippet = j.get("snippet", "")
            embeds.append({
                "title": f"{j['company']} — {j['title']}"[:256],
                "url": j["url"],
                "color": TIER_COLOR.get(j["tier"], 0x95A5A6),
                "description": (header + (f"\n\n{snippet}" if snippet else ""))[:4096],
                "fields": _fields(j),
                "footer": {"text": f"source: {j.get('source','')} · first seen {j.get('first_seen','')}"},
            })
        payload = {"embeds": embeds}
        if i == 0:
            payload["content"] = f"**{len(new_jobs)} new posting(s)** {run_label}".strip()
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 429:
            time.sleep(float(resp.json().get("retry_after", 2)))
            resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        time.sleep(1)
    print(f"Notified Discord: {len(new_jobs)} job(s)")
