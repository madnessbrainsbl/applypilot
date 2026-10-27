"""JobOps-inspired discovery additions; isolated cache/DB and no network."""
import copy
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import autopilot as A
import db
import employer_url as EU
import osint_sources as O
import remote_visa_boards as R
import sponsors


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    def no_net(*args, **kwargs):
        raise AssertionError("unexpected network request")
    monkeypatch.setattr(urllib.request, "urlopen", no_net)
    monkeypatch.setattr(O, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(R, "SUMMARY", {})
    monkeypatch.setattr(O, "_seed", lambda _: {"appsec": ["security engineer"], "creative": ["photographer"]})


WAIVER = {"apply": {"sponsorship_waived": {"creative": "europe"}}}

def config(persona="appsec"):
    return {"active_persona": persona, "personas": {persona: {"search": {"keywords": ["security engineer"]}}},
            "discovery": {R.NAME: {name: {"enabled": name == "freehire"} for name in R.ADAPTERS}}}


def posting(**changes):
    return {"public_slug": "demo", "title": "Security Engineer", "company": "Demo", "location": "Berlin, Germany",
            "url": "https://jobs.lever.co/demo/123", "posted_at": date.today().isoformat(),
            "description": "We offer visa sponsorship and relocation.", **changes}


def discover(cfg=None):
    return R.discover(cfg or config(), lambda t: "creative" if "Photographer" in t else "appsec" if t == "Security Engineer" else None,
                      sponsors.europe_location, lambda j, p: sponsors.visa_path(j, p, A.offers_sponsorship, A.needs_no_visa,
                          A.visa_free_location, cfg=WAIVER) if p == "creative" else "visa sponsorship" in j["description"])


def test_freehire_contract_and_existing_gates(monkeypatch):
    calls = []
    jobs = [posting(), posting(), posting(title="Sales Manager"), posting(company="Old", posted_at=(date.today() - timedelta(days=40)).isoformat()),
            posting(company="No visa", description="Not specified"), posting(company="US", location="Remote, US only"),
            posting(company="Closed", closed_at="2026-10-01"), posting(company="Clearance", requires_clearance=True)]
    def get(url, **kwargs):
        calls.append((urllib.parse.parse_qs(urllib.parse.urlsplit(url).query), kwargs))
        return json.dumps({"data": jobs})
    monkeypatch.setattr(R, "_get", get)
    out = discover()
    assert len(out) == 1 and out[0]["url"] == posting()["url"]
    assert out[0]["src"] == "freehire" and "visa sponsorship" in out[0]["description"]
    params, headers = calls[0]
    assert params["q_fields"] == ["title"] and params["description_format"] == ["text"]
    assert params["q"] == ['"security engineer"']
    assert params["posted_within_days"] == ["14"] and params["limit"] == ["50"]
    assert set(params) == {"q", "q_fields", "description_format", "posted_within_days", "sort", "order", "limit"}
    assert "applypilot" in headers["headers"]["User-Agent"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/private", "https://127.0.0.1/jobs/1",
    "http://localhost/jobs/1", "https://user:pass@example.com/jobs/1", "https://jobs.example.local/1",
    "https://127.0.0.1./jobs/1", "https://example.com:invalid/jobs/1",
    "https://freehire.me/jobs/1", "https://uk.whatjobs.com/job/1", "https://startup.jobs/job/1",
    "https://www.linkedin.com/jobs/view/1"])
def test_freehire_drops_unsafe_and_aggregator_targets(monkeypatch, url):
    monkeypatch.setattr(R, "_get", lambda *a, **k: json.dumps({"data": [posting(url=url)]}))
    assert discover() == []
    assert R.SUMMARY["freehire"]["dropped_unresolved"] == 1


@pytest.mark.parametrize("response", [{}, {"data": {}}, {"data": [], "meta": {"ignored_params": [{"name": "q_fields"}]}}])
def test_bad_response_is_an_error_not_no_matches(monkeypatch, response):
    monkeypatch.setattr(R, "_get", lambda *a, **k: json.dumps(response))
    assert discover() == []
    health = {r["adapter"]: r for r in R.adapter_health(config())}
    assert health["freehire"]["status"] == "error" and health["freehire"]["errors"] == 1


def test_freehire_preserves_full_description_and_regional_remote(monkeypatch):
    jobs = [posting(title="Photographer", location="Germany", work_mode="remote", description="x" * 9000),
            posting(title="Photographer", company="US", location="", countries=["us"], work_mode="remote")]
    monkeypatch.setattr(R, "_get", lambda *a, **k: json.dumps({"data": jobs}))
    out = discover(config("creative"))
    assert len(out) == 1 and out[0]["location"] == "Remote, Germany"
    assert "x" * 9000 in out[0]["description"]


@pytest.mark.parametrize("location,countries", [("Bristol", ["gb"]), ("Luleå, SE", ["se"]), ("Monza", ["it"]), ("", ["de"])])
def test_freehire_keeps_european_countries_when_city_is_not_in_dictionary(monkeypatch, location, countries):
    jobs = [posting(title="Photographer", location=location, countries=countries)]
    monkeypatch.setattr(R, "_get", lambda *a, **k: json.dumps({"data": jobs}))
    out = discover(config("creative"))
    assert len(out) == 1
    assert sponsors.europe_location(out[0])
    assert sponsors.sponsorship_waived(out[0], "creative", WAIVER)


def test_freehire_world_wide_remote_has_the_existing_no_visa_path(monkeypatch):
    jobs = [posting(location="World Wide - Remote", work_mode="remote", countries=[],
                    description="Application security testing. Freelance contract.")]
    monkeypatch.setattr(R, "_get", lambda *a, **k: json.dumps({"data": jobs}))
    visa = lambda job, persona: sponsors.visa_path(job, persona, A.offers_sponsorship, A.needs_no_visa, A.visa_free_location, cfg=WAIVER)
    out = R.discover(config(), lambda _: "appsec", sponsors.europe_location, visa, adapters=["freehire"])
    assert len(out) == 1
    assert visa(out[0], "appsec") == "remote worldwide: no work permit involved"


@pytest.mark.parametrize("location,countries", [("Remote - Ireland", ["ie", "gb"]),
    ("United States", ["gb"]), ("London", ["us"]), ("London, Canada", ["gb"])])
def test_freehire_does_not_expand_a_stated_country_or_confuse_namesakes(location, countries):
    place = R._freehire_location({"location": location, "countries": countries})
    if location == "London":
        assert not sponsors.europe_location({"location": place})
    else:
        assert place == location


@pytest.mark.parametrize("location,description", [("World Wide - Remote, US only", ""),
    ("World-Wide - Remote, EU only", ""), ("World Wide - Remote", "Must reside in the United States."),
    ("Worldwide Security Engineer", "On-site office role.")])
def test_worldwide_spelling_does_not_remove_residency_or_remote_requirements(location, description):
    assert not A.needs_no_visa({"location": location, "description": description})


@pytest.mark.parametrize("location", ["World Wide - Remote", "World-Wide - Remote", "Remote, World Wide"])
def test_worldwide_spelling_in_shared_visa_helper(location):
    assert A.needs_no_visa({"location": location})


def test_country_code_does_not_grant_appsec_sponsorship(monkeypatch):
    monkeypatch.setattr(sponsors, "is_known_sponsor", lambda *a: False)
    place = R._freehire_location({"location": "Bristol", "countries": ["gb"]})
    assert sponsors.visa_path({"title": "Security Engineer", "company": "Example", "location": place},
        "appsec", A.offers_sponsorship, A.needs_no_visa, A.visa_free_location, cfg=WAIVER) is None


@pytest.mark.parametrize("persona", ["appsec", "creative"])
def test_european_or_registered_employer_does_not_override_explicit_visa_denial(monkeypatch, persona):
    monkeypatch.setattr(sponsors, "is_known_sponsor", lambda *a: True)
    job = {"title": "Photographer" if persona == "creative" else "Security Engineer", "company": "Example",
           "location": "Bristol, United Kingdom", "description": "Applicants must have the right to work in the UK. "
           "We are not currently able to offer visa sponsorship for this role."}
    assert sponsors.visa_path(job, persona, A.offers_sponsorship, A.needs_no_visa, A.visa_free_location, cfg=WAIVER) is None


def test_freehire_creative_keeps_europe_waiver_but_not_explicit_visa_denial(monkeypatch):
    jobs = [posting(title="Photographer", location="Bristol", countries=["gb"], description="Shoot portraits."),
            posting(title="Photographer", company="No visa", url="https://jobs.lever.co/no-visa/456", location="Bristol", countries=["gb"],
                    description="We are not currently able to offer visa sponsorship for this role.")]
    monkeypatch.setattr(R, "_get", lambda *a, **k: json.dumps({"data": jobs}))
    visa = lambda job, persona: sponsors.visa_path(job, persona, A.offers_sponsorship, A.needs_no_visa, A.visa_free_location, cfg=WAIVER)
    out = R.discover(config("creative"), lambda _: "creative", sponsors.europe_location, visa, adapters=["freehire"])
    assert len(out) == 1 and out[0]["company"] == "Demo"


def test_europe_waiver_does_not_override_visa_denial_in_html():
    job = {"title": "Photographer", "location": "Bristol, United Kingdom",
           "description": "We are not <b>currently</b> able to offer visa sponsorship for this role."}
    assert sponsors.visa_path(job, "creative", cfg=WAIVER) is None
    assert not A.offers_sponsorship(job)


def test_no_relocation_budget_is_not_mistaken_for_visa_denial():
    job = {"title": "Photographer", "location": "Berlin, Germany", "description": "We do not offer relocation assistance."}
    assert sponsors.visa_path(job, "creative", cfg=WAIVER) == "Europe creative role: sponsorship requirement waived by the user"


def test_partial_results_survive_and_rate_limit_cools_down(monkeypatch):
    cfg = config()
    cfg["discovery"][R.NAME]["freehire"]["queries"] = {"appsec": ["good", "blocked"]}
    def get(url, **kwargs):
        if "blocked" in url:
            raise urllib.error.HTTPError(url, 429, "limited", {}, None)
        return json.dumps({"data": [posting()]})
    monkeypatch.setattr(R, "_get", get)
    assert len(discover(cfg)) == 1
    st = R.SUMMARY["freehire"]
    assert st["status"] == "partial" and st["errors"] == 1 and st["retry_at"]
    monkeypatch.setattr(R, "_get", lambda *a, **k: pytest.fail("cooldown must prevent requests"))
    assert discover(cfg) == [] and R.SUMMARY["freehire"]["status"] == "cooldown"
    health = {r["adapter"]: r for r in R.adapter_health(cfg)}
    assert health["freehire"]["status"] == "cooldown"
    cfg["discovery"][R.NAME]["freehire"]["enabled"] = False
    assert {r["adapter"]: r for r in R.adapter_health(cfg)}["freehire"]["status"] == "disabled"


def test_empty_success_and_unknown_health_are_distinct(monkeypatch):
    assert R.adapter_health(config()) == []
    monkeypatch.setattr(R, "_get", lambda *a, **k: '{"data": []}')
    discover()
    assert R.SUMMARY["freehire"]["status"] == "ok"
    assert R.SUMMARY["freehire"]["raw"] == R.SUMMARY["freehire"]["errors"] == 0
    R.discover({"discovery": {R.NAME: {"enabled": False}}}, None, None)
    assert R.SUMMARY == {}


def test_all_failed_queries_count_each_error_and_honor_any_wall(monkeypatch):
    cfg = config()
    cfg["discovery"][R.NAME]["freehire"]["queries"] = {"appsec": ["offline", "blocked"]}
    def get(url, **kwargs):
        if "blocked" in url:
            raise urllib.error.HTTPError(url, 403, "blocked", {}, None)
        raise OSError("offline")
    monkeypatch.setattr(R, "_get", get)
    assert discover(cfg) == []
    assert R.SUMMARY["freehire"]["errors"] == 2 and R.SUMMARY["freehire"]["retry_at"]


def test_successful_probe_clears_expired_cooldown(monkeypatch):
    O._save(R.NAME, {"blocked": {"freehire": {"t": (O._now() - timedelta(days=2)).isoformat(), "why": "old"}}})
    monkeypatch.setattr(R, "_get", lambda *a, **k: '{"data": []}')
    discover()
    assert R.SUMMARY["freehire"]["status"] == "ok" and R.SUMMARY["freehire"]["retry_at"] is None


def test_query_volume_is_bounded(monkeypatch):
    cfg = config()
    cfg["discovery"][R.NAME]["freehire"].update(queries={"appsec": ["one", "two", "three"]}, max_queries=2, page_size=9000)
    calls = []
    def get(url, **kwargs):
        calls.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
        return '{"data": []}'
    monkeypatch.setattr(R, "_get", get)
    discover(cfg)
    assert len(calls) == 2 and all(c["limit"] == ["100"] for c in calls)


def test_discovery_reports_adapter_errors_and_records_failed_source(monkeypatch, tmp_path, capsys):
    cfg = config()
    monkeypatch.setattr(A, "read_json", lambda _: copy.deepcopy(cfg))
    monkeypatch.setattr(A, "ROOT", tmp_path)
    monkeypatch.setattr(A, "write_progress", lambda *a: None)
    monkeypatch.setattr(A, "_quit_requested", lambda: False)
    monkeypatch.setattr(A, "_QUIT_RUN", {"v": False})
    def fail(*a, **k):
        raise OSError("unavailable")
    monkeypatch.setattr(R, "_get", fail)
    A.discover(SimpleNamespace(persona="appsec", source=[R.NAME], max_keywords=1, limit=10, min_score=3))
    report = json.loads(capsys.readouterr().out)
    assert report["added"] == 0 and report["source_errors"] == [
        {"source": R.NAME, "persona": "appsec", "adapter": "freehire", "error": "error"}]
    con = db.connect()
    try:
        row = {r["source"]: r for r in A.sources_overview(con, cfg)}[R.NAME]
        assert row["last_errors"] == 1
        assert next(r for r in row["adapters"] if r["adapter"] == "freehire")["status"] == "error"
    finally:
        con.close()


def test_new_aggregators_are_never_employer_pages():
    for host in ("freehire.me", "uk.whatjobs.com", "startup.jobs", "hiring.cafe", "hiringcafe.com"):
        assert EU.classify_url(f"https://{host}/jobs/1") == "aggregator"
