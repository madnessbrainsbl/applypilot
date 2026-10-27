"""remote_visa_boards adapters and discover() wiring. No network: `_get`, `_final_url` and the site probe are
faked, and urllib itself is made to fail so a stray request cannot pass unnoticed."""
import json
import urllib.error
import urllib.request
from datetime import date, timedelta

import pytest

import osint_sources as O
import remote_visa_boards as R
import sponsors

TODAY = date.today()
NEW = (TODAY - timedelta(days=2)).isoformat() + "T10:00:00.000Z"
OLD = (TODAY - timedelta(days=40)).isoformat() + "T10:00:00.000Z"


def classify(t):
    t = t.lower()
    if any(w in t for w in ("senior", "lead", "sales")):
        return None
    if any(w in t for w in ("photograph", "video")):
        return "creative"
    if any(w in t for w in ("security", "pentest", "penetration")):
        return "appsec"
    return None


def visa_ok(job, persona):
    if persona == "creative" and sponsors.sponsorship_waived(job, persona):
        return True
    return "sponsor" in job["description"].lower() or "anywhere" in job["location"].lower()


def cfg(persona, **adapters):
    return {"active_persona": persona, "discovery": {R.NAME: adapters}}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def no_net(*a, **k):
        raise AssertionError("network access in a unit test")
    monkeypatch.setattr(urllib.request, "urlopen", no_net)
    monkeypatch.setattr(O, "CACHE", tmp_path)
    monkeypatch.setattr(R.J, "_wait", lambda host: None)
    monkeypatch.setattr(O, "_seed", lambda f: {"remote_visa_queries.json": {"appsec": ["security engineer"], "creative": ["photographer"]}}.get(f, {}))


def routes(table):
    def get(url, headers=None, gap=1.5, timeout=30):
        for needle, body in table.items():
            if needle in url:
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, str) else json.dumps(body)
        raise OSError("no route " + url)
    return get


def run(persona, table, monkeypatch, only, **adapters):
    monkeypatch.setattr(R, "_get", routes(table))
    c = cfg(persona, **adapters)
    return R.discover(c, classify, sponsors.europe_location, visa_ok, adapters=[only])


def wk(id, title, created=NEW, workplace="remote", country="France", company="Acme"):
    return {"id": id, "title": title, "state": "published", "description": "<p>We sponsor visas.</p>", "workplace": workplace,
            "url": f"https://jobs.workable.com/view/{id}/x", "locations": (["TELECOMMUTE"] if workplace == "remote" else []) + [country], "created": created,
            "location": {"city": "", "countryName": country}, "company": {"title": company, "website": "https://acme.io/"}}


def test_workable_keeps_fresh_persona_rows_and_the_visa_gate(monkeypatch):
    listing = {"jobs": [wk("1", "Security Engineer"), wk("2", "Senior Security Engineer"), wk("3", "Security Analyst", created=OLD),
                        wk("4", "Pentester", country="United States")], "nextPageToken": None}
    table = {"apply.workable.com/api/v1/widget": OSError("none"), "jobs.workable.com/api/v1/jobs": listing}
    out = run("appsec", table, monkeypatch, "workable_aggregator")
    assert [r["title"] for r in out] == ["Security Engineer", "Pentester"]   # senior and 40-day-old rows dropped
    r = out[0]
    assert r["location"] == "Remote, France" and r["company"] == "Acme" and r["src"] == "workable_aggregator"
    assert f"Posted: {NEW[:10]}" in r["description"] and r["url"].startswith("https://jobs.workable.com/view/")


def test_workable_points_to_the_employer_page_when_the_account_matches(monkeypatch):
    table = {"apply.workable.com/api/v1/widget/accounts/acme": {"jobs": [{"title": "Security Engineer", "url": "https://apply.workable.com/acme/j/ABC123/"}]},
             "jobs.workable.com/api/v1/jobs": {"jobs": [wk("1", "Security Engineer")]}}
    out = run("appsec", table, monkeypatch, "workable_aggregator")
    assert out[0]["url"] == "https://apply.workable.com/acme/j/ABC123/" and out[0]["listing_url"].startswith("https://jobs.workable.com/view/1")


OFFER = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Junior Security Engineer","description":"<p>Visa sponsorship offered.</p>",'
         '"datePosted":"2026-10-15"}</script><a href="https://job-boards.greenhouse.io/acme/jobs/7997051003?utm_source=Jobgether&utm_medium=web">Apply</a>'
         '<a href="https://www.linkedin.com/company/acme/">in</a>')


def test_jobgether_reads_the_offer_page_for_description_and_employer_ats_link(monkeypatch):
    listing = {"jobs": [{"id": "a1", "title": "Junior Security Engineer", "company": "Acme", "url": "https://jobgether.com/offer/a1-junior",
                         "location": "Europe", "remote": "Full Remote", "postedAt": NEW},
                        {"id": "a2", "title": "Marketing Lead", "company": "Acme", "url": "https://jobgether.com/offer/a2", "postedAt": NEW}],
               "pagination": {"hasMore": False}}
    out = run("appsec", {"astroapi/ai/jobs.json": listing, "jobgether.com/offer/a1": OFFER}, monkeypatch, "jobgether")
    assert len(out) == 1 and out[0]["url"] == "https://job-boards.greenhouse.io/acme/jobs/7997051003"   # utm stripped
    assert out[0]["listing_url"] == "https://jobgether.com/offer/a1-junior" and "Visa sponsorship offered" in out[0]["description"]


def test_jobgether_drops_rows_without_an_employer_link(monkeypatch):
    listing = {"jobs": [{"id": "a1", "title": "Junior Security Engineer", "company": "Acme", "url": "https://jobgether.com/offer/a1",
                         "location": "Anywhere", "postedAt": NEW}], "pagination": {"hasMore": False}}
    page = OFFER.split("<a href")[0]
    assert run("appsec", {"astroapi/ai/jobs.json": listing, "jobgether.com/offer/a1": page}, monkeypatch, "jobgether") == []


def test_jobscollider_resolves_the_employer_site(monkeypatch):
    api = {"jobs": [{"id": "pt-1", "url": "https://remotefirstjobs.com/companies/acme/jobs/pt-1", "company_name": "Acme", "title": "Penetration Tester",
                     "description": "<p>We sponsor.</p>", "locations": ["Germany"], "published_at": NEW[:19]}]}
    page = '<script>{"hiringOrganization":{"@type":"Organization","name":"Acme","sameAs":"https://www.acme.io/"}}</script>'
    probed = []
    monkeypatch.setattr(R.J, "probe_site", lambda site, cl, **k: probed.append(site) or {"postings": [
        {"title": "Penetration Tester", "url": "https://acme.io/careers/1", "apply_url": "https://boards.greenhouse.io/acme/jobs/9"}]})
    out = run("appsec", {"api/search-jobs": api, "remotefirstjobs.com/companies/acme": page}, monkeypatch, "jobscollider")
    assert probed == ["https://www.acme.io/"] and out[0]["url"] == "https://boards.greenhouse.io/acme/jobs/9"
    assert "remotefirstjobs.com" in out[0]["description"]   # credit line required by the API terms
    # the second run answers from the cache: no site probe, no page read
    probed.clear()
    again = run("appsec", {"api/search-jobs": api}, monkeypatch, "jobscollider")
    assert probed == [] and again[0]["url"] == out[0]["url"]


CARDS = ('<a class="job-card" href="/job/junior-pentester"><div class="job-card-top"><h2 class="job-title">Junior Pentester</h2></div>'
         '<p class="job-company">Acme GmbH</p><hr/><p class="job-location"><span>Berlin, Germany</span></p>'
         '<div class="job-pills"><span class="pill pill-visa">EU Blue Card</span><span class="pill pill-visa">Other / unspecified</span></div>'
         '<p class="job-card-footer"><span class="date-label">Publish date </span>{d}</p></a>')


def test_visasponsor_cards_detail_and_dice_links_are_dropped(monkeypatch):
    d = (TODAY - timedelta(days=1)).strftime("%d-%m-%Y")
    cards = R.parse_visasponsor_cards(CARDS.replace("{d}", d))
    assert cards[0]["title"] == "Junior Pentester" and cards[0]["company"] == "Acme GmbH" and cards[0]["visas"] == ["EU Blue Card"]
    assert R._day(cards[0]["posted"]) == TODAY - timedelta(days=1)
    detail = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Junior Pentester","description":"Test web apps."}</script>'
              '<a href="https://careers.acme.de/jobs/12">Apply Now</a>')
    table = {"visasponsor.jobs/jobs": CARDS.replace("{d}", d), "visasponsor.jobs/job/junior-pentester": detail}
    out = run("appsec", table, monkeypatch, "visasponsor_jobs", countries=[""], pages=1)
    assert len(out) == 1 and out[0]["url"] == "https://careers.acme.de/jobs/12"
    assert "EU Blue Card" in out[0]["description"] and "visa sponsorship" in out[0]["description"].lower()
    dice = detail.replace("https://careers.acme.de/jobs/12", "https://www.dice.com/job-detail/abc")
    table["visasponsor.jobs/job/junior-pentester"] = dice
    R.O._save(R.NAME, {})   # fresh cache
    assert run("appsec", table, monkeypatch, "visasponsor_jobs", countries=[""], pages=1) == []


def test_workingnomads_follows_the_go_link(monkeypatch):
    feed = [{"url": "https://www.workingnomads.com/job/go/1/", "title": "Video Editor", "company_name": "Acme", "location": "Anywhere",
             "description": "<p>Cut reels.</p>", "pub_date": NEW[:10] + "T10:00:00-04:00", "tags": "video"},
            {"url": "https://www.workingnomads.com/job/go/2/", "title": "Photographer", "company_name": "Old", "location": "Europe",
             "description": "x", "pub_date": OLD}]
    monkeypatch.setattr(R, "_final_url", lambda url, **k: "https://acme.recruitee.com/o/video-editor")
    out = run("creative", {"exposed_jobs": feed}, monkeypatch, "workingnomads")
    assert [r["title"] for r in out] == ["Video Editor"] and out[0]["url"] == "https://acme.recruitee.com/o/video-editor"


def test_a_wall_blocks_the_adapter_for_the_next_runs(monkeypatch):
    wall = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    assert run("appsec", {"api/search-jobs": wall}, monkeypatch, "jobscollider") == []
    assert R._cache()["blocked"]["jobscollider"]["why"]
    assert run("appsec", {}, monkeypatch, "jobscollider") == [] and R.SUMMARY["jobscollider"]["skipped"].startswith("blocked")


def test_disabled_adapter_and_master_switch(monkeypatch):
    assert run("appsec", {}, monkeypatch, "jobgether", jobgether={"enabled": False}) == []
    assert R.discover({"active_persona": "appsec", "discovery": {R.NAME: {"enabled": False}}}, classify, None) == []


def test_creative_rows_need_europe_or_remote(monkeypatch):
    listing = {"jobs": [wk("1", "Photographer", workplace="on_site", country="Germany"), wk("2", "Videographer", workplace="on_site", country="Brazil")]}
    out = run("creative", {"apply.workable.com": OSError("none"), "jobs.workable.com/api/v1/jobs": listing}, monkeypatch, "workable_aggregator")
    assert [r["title"] for r in out] == ["Photographer"]


def test_queries_add_ascii_persona_terms_and_respect_overrides():
    c = {"search": {"required_title_terms": ["devsecops", "soc", "sicherheitsingenieur!", "red team"]}}
    assert R._queries(c, "appsec", {}) == ["security engineer", "devsecops", "red team"]
    assert R._queries(c, "appsec", {"queries": {"appsec": ["x"]}, "extra_queries": {"appsec": ["y"]}}) == ["x", "y"]


def test_registered_as_a_discover_source():
    import autopilot as A
    assert "remote_visa_boards" in A.KNOWN_SOURCES and "remote_visa_boards" in A.DEFAULT_SOURCES
    assert "remote_visa_boards" in A._SINGLE_FETCH_SOURCES and A.source_every_hours({}, "remote_visa_boards") == 6
    assert A.source_every_hours({"discovery": {"remote_visa_boards": {"every_hours": 3}}}, "remote_visa_boards") == 3
    assert set(R.DEFAULTS) - {"enabled"} == set(R.ADAPTERS) == set(R.RUNNERS)


def test_shipped_seed_has_both_personas():
    seed = json.loads((R.O.SEEDS / "remote_visa_queries.json").read_text(encoding="utf-8"))
    assert seed["appsec"] and seed["creative"]
