"""Discord delivery. A failed message must never take the scan down with it."""
import monitor.notify as notify


class FakeResponse:
    def __init__(self, status=204):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"retry_after": 0}


def jobs(n):
    return [{"company": "Stripe", "title": f"SWE {i}", "tier": "experienced",
             "url": f"https://x.test/{i}", "source": "greenhouse",
             "first_seen": "2026-08-26", "location": "NYC"} for i in range(n)]


def setup_webhook(monkeypatch, responses):
    """Wire a webhook url and a scripted sequence of POST outcomes."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setattr(notify.time, "sleep", lambda *_: None)
    calls = []

    def fake_post(url, **kw):
        calls.append(kw.get("json"))
        outcome = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    monkeypatch.setattr(notify.requests, "post", fake_post)
    return calls


# ---- A4 --------------------------------------------------------------------

def test_a_failed_chunk_does_not_raise(monkeypatch, capsys):
    """25 jobs = 3 messages; the middle one fails and the run continues."""
    calls = setup_webhook(monkeypatch, [204, 500, 204])
    notify.send(jobs(25), run_label="(scan: all)")
    assert len(calls) == 3                       # it kept going past the failure
    out = capsys.readouterr().out
    assert "could not deliver" in out
    assert "Notified Discord: 15 job(s); 10 could not be delivered" in out


def test_a_connection_error_does_not_raise(monkeypatch, capsys):
    setup_webhook(monkeypatch, [ConnectionError("network down")])
    notify.send(jobs(10))                        # must not propagate
    assert "could not deliver" in capsys.readouterr().out


def test_every_chunk_failing_still_returns_normally(monkeypatch, capsys):
    setup_webhook(monkeypatch, [500])
    notify.send(jobs(30))
    assert "Notified Discord: 0 job(s); 30 could not be delivered" in capsys.readouterr().out


def test_happy_path_reports_what_it_sent(monkeypatch, capsys):
    calls = setup_webhook(monkeypatch, [204])
    notify.send(jobs(12), run_label="(scan: bigtech)")
    assert len(calls) == 2                       # 10 embeds per message
    assert calls[0]["content"].startswith("**12 new posting(s)**")
    assert "content" not in calls[1]             # header on the first message only
    assert capsys.readouterr().out.strip() == "Notified Discord: 12 job(s)"


def test_rate_limit_is_retried_once(monkeypatch):
    calls = setup_webhook(monkeypatch, [429, 204])
    notify.send(jobs(5))
    assert len(calls) == 2                       # one 429, one successful retry


def test_no_webhook_configured_is_not_an_error(monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    notify.send(jobs(5))
    assert "not set" in capsys.readouterr().out


def test_source_alert_still_never_raises(monkeypatch, capsys):
    setup_webhook(monkeypatch, [500])
    notify.send_alert([{"name": "Citadel", "was": 0, "since": None, "never": True}])
    assert "could not send source alert" in capsys.readouterr().out


# ---- new grad urgency ------------------------------------------------------

def newgrad(i=0, **kw):
    j = {"company": "Stripe", "title": f"New Grad SWE {i}", "tier": "newgrad",
         "url": f"https://x.test/ng{i}", "source": "greenhouse", "priority": 100,
         "apply_now": True, "newgrad_signal": "title",
         "first_seen": "2026-08-26", "location": "NYC"}
    j.update(kw)
    return j


def test_new_grad_postings_lead_the_message(monkeypatch):
    calls = setup_webhook(monkeypatch, [204])
    notify.send(jobs(9) + [newgrad()])
    titles = [e["title"] for e in calls[0]["embeds"]]
    assert titles[0].startswith("🚨")           # first embed of the first message
    assert not any(t.startswith("🚨") for t in titles[1:])


def test_the_header_says_to_apply_immediately(monkeypatch):
    calls = setup_webhook(monkeypatch, [204])
    notify.send(jobs(2) + [newgrad(1), newgrad(2)])
    content = calls[0]["content"]
    assert "APPLY IMMEDIATELY" in content and "2 new grad" in content
    assert content.startswith("@here")


def test_the_mention_can_be_turned_off(monkeypatch):
    calls = setup_webhook(monkeypatch, [204])
    monkeypatch.setenv("DISCORD_MENTION", "none")
    notify.send([newgrad()])
    assert calls[0]["content"].startswith("🚨")


def test_an_unset_actions_variable_does_not_silence_the_ping(monkeypatch):
    """GitHub hands an unset `vars.X` through as "" - that is not "no ping"."""
    calls = setup_webhook(monkeypatch, [204])
    monkeypatch.setenv("DISCORD_MENTION", "")
    notify.send([newgrad()])
    assert calls[0]["content"].startswith("@here")


def test_a_custom_mention_is_used_verbatim(monkeypatch):
    calls = setup_webhook(monkeypatch, [204])
    monkeypatch.setenv("DISCORD_MENTION", "<@12345>")
    notify.send([newgrad()])
    assert calls[0]["content"].startswith("<@12345> 🚨")


def test_an_ordinary_batch_says_nothing_urgent(monkeypatch):
    calls = setup_webhook(monkeypatch, [204])
    notify.send(jobs(3))
    assert "APPLY IMMEDIATELY" not in calls[0]["content"]
    assert not any("⚡ Action" in f["name"] for f in calls[0]["embeds"][0]["fields"])


def test_a_new_grad_embed_carries_the_call_to_action(monkeypatch):
    calls = setup_webhook(monkeypatch, [204])
    notify.send([newgrad(newgrad_signal="description")])
    embed = calls[0]["embeds"][0]
    action = [f for f in embed["fields"] if f["name"] == "⚡ Action"]
    assert action and "Apply now" in action[0]["value"]
    assert "the description says new grad" in action[0]["value"]
    assert "APPLY IMMEDIATELY" in embed["description"]
    assert embed["color"] == notify.APPLY_NOW_COLOR


def test_an_entry_stored_before_the_flag_existed_is_still_urgent(monkeypatch):
    """Only the tier is needed; apply_now is backfilled on a later scan."""
    calls = setup_webhook(monkeypatch, [204])
    notify.send([{"company": "Stripe", "title": "SWE I", "tier": "newgrad",
                  "url": "https://x.test/old", "source": "greenhouse",
                  "first_seen": "2026-08-01", "location": "NYC"}])
    assert "APPLY IMMEDIATELY" in calls[0]["content"]


def test_within_a_tier_the_newest_posting_comes_first(monkeypatch):
    calls = setup_webhook(monkeypatch, [204])
    notify.send([newgrad(1, posted_at="2026-08-01"), newgrad(2, posted_at="2026-08-20")])
    assert [e["url"] for e in calls[0]["embeds"]] == ["https://x.test/ng2",
                                                     "https://x.test/ng1"]


def test_the_run_summary_counts_the_urgent_ones(monkeypatch, capsys):
    setup_webhook(monkeypatch, [204])
    notify.send(jobs(2) + [newgrad()])
    assert "3 job(s) (1 flagged apply-immediately)" in capsys.readouterr().out
