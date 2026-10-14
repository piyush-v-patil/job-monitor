"""Ashby location reading and the Avature listing parser. No network.

Ashby's `location` is a label the employer types, and a run of US postings
said nothing filters.is_us could read ("Remote U.S.", "North America",
"CHI Office") - Vanta and Linear contributed nothing for weeks. The structured
address beside the label is what settles it.
"""
from monitor import filters
from monitor.fetchers import generic


def posting(location, country=None, secondary=()):
    j = {"id": "a1", "title": "Software Engineer", "location": location,
         "jobUrl": "https://jobs.ashbyhq.com/x/a1", "isListed": True,
         "secondaryLocations": [
             {"location": loc, "address": {"postalAddress": {"addressCountry": ctry}}}
             for loc, ctry in secondary]}
    if country:
        j["address"] = {"postalAddress": {"addressCountry": country}}
    return j


def fetch(monkeypatch, *jobs):
    monkeypatch.setattr(generic, "get_json", lambda s, url, **kw: {"jobs": list(jobs)})
    return generic.ashby({"name": "Testco", "token": "testco"})


def kept(job):
    return filters.is_us(job["location"], job.get("country", ""))


def test_the_address_country_keeps_a_label_with_no_us_signal(monkeypatch):
    (job,) = fetch(monkeypatch, posting("North America", "United States"))
    assert job["location"] == "North America"     # the label is still shown
    assert kept(job)


def test_a_us_secondary_location_keeps_a_posting_based_abroad(monkeypatch):
    (job,) = fetch(monkeypatch, posting(
        "London", "United Kingdom", [("Poland", "Poland"), ("New York", "United States")]))
    assert kept(job)


def test_a_bare_remote_secondary_does_not_make_a_posting_us(monkeypatch):
    (job,) = fetch(monkeypatch, posting("Belgrade", "Serbia", [("Remote", None)]))
    assert job["country"] == ""
    assert not kept(job)


def test_a_foreign_address_leaves_the_label_in_charge(monkeypatch):
    """Only a US answer is taken from the address; "London" still decides."""
    uk, us_label = fetch(monkeypatch, posting("London, UK", "United Kingdom"),
                         posting("San Francisco, CA", "Canada"))
    assert not kept(uk)
    assert kept(us_label)


CARD = '''<article class="article article--result" id="article--{n}">
  <h3 class="article__header__text__title title title--065">
    <a class="link" href="https://careers.example.com/careers/JobDetail/{slug}/{n}">
      {title}
    </a>
  </h3>
  <div class="article__header__content__text">
    <span class="paragraph_inner-span">{loc}</span>
    <div class="article__header__content__sub-text">
      <span class="paragraph_inner-span">Engineering</span>
    </div>
  </div>
</article>'''


def test_avature_pages_until_a_page_repeats_or_runs_dry(monkeypatch):
    pages = {
        0: CARD.format(n=1, slug="NY", title="Software Engineer &amp; Researcher",
                       loc="United States - NY New York")
           + CARD.format(n=2, slug="LDN", title="Quant Developer", loc="London"),
        10: CARD.format(n=3, slug="NY", title="SRE", loc="United States - NY New York"),
        20: "<html>no results</html>",
    }
    calls = []

    def get_text(s, url, params=None, **kw):
        calls.append(params["jobOffset"])
        return pages.get(params["jobOffset"], "")

    monkeypatch.setattr(generic, "get_text", get_text)
    jobs = generic.avature({"name": "Testco", "host": "careers.example.com"})
    assert [j["external_id"] for j in jobs] == ["1", "2", "3"]
    assert jobs[0]["title"] == "Software Engineer & Researcher"
    assert jobs[0]["location"] == "United States - NY New York"
    assert jobs[0]["url"].endswith("/JobDetail/NY/1")
    assert calls == [0, 10, 20]
