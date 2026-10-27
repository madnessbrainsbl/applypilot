import copy
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

import autopilot as A
import medical_search as M


def job(description, **kwargs):
    return {"title": "Security Engineer", "company": "Example", "location": "Berlin",
            "persona": "appsec", "description": description, **kwargs}


def strict_config():
    return {"active_persona": "appsec", "search": {}, "personas": {"appsec": {"search": {
        "required_title_terms": ["security engineer"], "sector_focus": "healthtech_medtech",
        "sector_focus_terms": ["healthtech", "medical devices", "patient data"],
        "require_explicit_visa_and_relocation": True, "keywords": ["healthtech security"],
        "vendor_boards": {"greenhouse": ["https://example.test/board"]}}}}}


PROMISES = [
    "We offer visa sponsorship and a relocation package.",
    "Wir unterstützen bei der Beantragung von Visa. Wir bieten ein Umzugspaket.",
    "Nous prenons en charge le visa. Aide au déménagement.",
    "Ofrecemos patrocinio de visados y paquete de reubicación.",
    "Oferecemos patrocínio de visto e pacote de mudança.",
    "Wij regelen een visum. Wij bieden een relocatiepakket.",
    "Offriamo sponsorizzazione del visto e supporto per trasferimento.",
    "Zapewniamy pomoc w uzyskaniu wizy. Pakiet relokacyjny.",
    "ビザサポート。転居費用を提供します。",
    "提供工作签证和搬迁补助。",
]


@pytest.mark.parametrize("text", PROMISES)
def test_affirmative_support_in_ten_languages(text):
    assert M.support_reason(text) is None


@pytest.mark.parametrize("text", [
    "No visa sponsorship. Relocation package available.",
    "Wir bieten keine Visa-Unterstützung. Umzugspaket.",
    "Nous ne proposons pas de visa. Aide au déménagement.",
    "No ofrecemos patrocinio de visados. Paquete de reubicación.",
    "Não oferecemos suporte para visto. Pacote de mudança.",
    "Geen visumsponsoring. Relocatiepakket.",
    "Non offriamo sponsorizzazione del visto. Supporto per trasferimento.",
    "Nie zapewniamy wizy. Pakiet relokacyjny.",
    "ビザサポートなし。転居費用。",
    "不提供工作签证。搬迁补助。",
    "We offer visas. No relocation assistance.",
])
def test_negative_support_overrides_positive_promises(text):
    assert M.support_reason(text) == "support_excluded"


@pytest.mark.parametrize("text", [
    "Will you need visa sponsorship? Relocation package.",
    "Benötigen Sie Visa-Unterstützung? Umzugspaket.",
    "ビザサポートが必要ですか？転居費用。",
    "We may sponsor visas. Relocation assistance.",
    "We provide visas depending on business requirements. Relocation support.",
    "Wir bieten eventuell Visa-Unterstützung. Umzugspaket.",
    "Wij bieden een laptop. Relocatiepakket.",
    "Visa and relocation preferences: yes.",
])
def test_questions_weak_claims_and_unrelated_benefits_are_not_proof(text):
    assert M.support_reason(text) == "visa_support_not_proven"


@pytest.mark.parametrize("location", ["Russia", "Moscow, RU", "Россия", "Russland", "俄罗斯", "ロシア"])
def test_russian_job_locations_are_excluded(location):
    cfg = strict_config()
    cfg["personas"]["appsec"]["search"]["exclude_russia"] = True
    assert A._strict_discovery_reason(job("HealthTech. " + PROMISES[0], source="vendor", location=location), cfg) == "russia_excluded"


def test_resident_citizenship_and_peru_are_not_russian_job_locations():
    assert not M.in_russia(job("Applicants from Russia are welcome", location="Lima, Peru"))
    assert not M.in_russia(job("Russian citizenship accepted", location="Berlin, Germany"))


@pytest.mark.parametrize("location", ["", "Unknown", "2 Locations"])
def test_unresolved_geography_does_not_prove_a_foreign_role(location):
    cfg = strict_config()
    cfg["personas"]["appsec"]["search"]["exclude_russia"] = True
    assert A._strict_discovery_reason(job("HealthTech. " + PROMISES[0], source="vendor", location=location), cfg) == "location_not_proven"


def test_primary_description_replaces_aggregator_claims_and_location(monkeypatch):
    listing = job("HealthTech. We sponsor visas and relocate everyone.", source="arbeitnow",
                  url="https://job-boards.greenhouse.io/example/jobs/123")
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return json.dumps({"title": "Security Engineer", "content": "No visa sponsorship.", "location": {"name": "Paris"}})
    result = M.primary_posting(listing, fetch)
    assert result["description"] == "No visa sponsorship."
    assert result["location"] == "Paris" and result["source"] == "vendor"
    assert result["discovered_via"] == "arbeitnow"
    assert len(calls) == 1


def test_untrusted_host_is_never_fetched(monkeypatch):
    monkeypatch.setattr("ats_schema.find_on_company_board", lambda *args: "https://jobs.ashbyhq.com.evil.test/company/id")
    assert M.primary_posting(job("", url="http://127.0.0.1/private"), lambda *args, **kwargs: pytest.fail("untrusted fetch")) is None


def test_discovered_workday_posting_reads_exact_requisition_and_tenant():
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return json.dumps({"jobPostingInfo": {"title": "Security Engineer", "jobDescription": "Medical devices. " + PROMISES[0], "location": "Berlin, Germany"}})
    listing = {"url": "https://medical.wd5.myworkdayjobs.com/en-US/Careers/job/Berlin/Security_R12345", "title": "", "company": ""}
    result = M.primary_posting(listing, fetch)
    assert calls == ["https://medical.wd5.myworkdayjobs.com/wday/cxs/medical/Careers/job/Berlin/Security_R12345"]
    assert result["company"] == "medical" and result["title"] == "Security Engineer"


def test_discovered_gem_posting_preserves_api_employer(monkeypatch):
    monkeypatch.setattr("medical_boards.gem_board", lambda url, match: [{"url": "https://jobs.gem.com/medical/id", "title": "Security Engineer", "company": "Medical Inc.", "description": PROMISES[0], "location": "Berlin"}])
    result = M.primary_posting({"url": "https://jobs.gem.com/medical/id", "company": ""}, None)
    assert result["company"] == "Medical Inc." and result["description"] == PROMISES[0]


def test_global_boards_merge_without_modifying_config_or_dead_sync(monkeypatch):
    cfg = A.get_persona_cfg(strict_config(), "appsec")
    cfg["vendor_boards"] = {"greenhouse": ["https://example.test/global", "https://example.test/board"]}
    cfg["search"]["include_global_vendor_boards"] = True
    before, calls = copy.deepcopy(cfg), []
    def fetch(url, **kwargs):
        calls.append(url)
        return '{"jobs": []}'
    monkeypatch.setattr(A, "fetch_text", fetch)
    assert A.vendor_source(cfg, 10) == []
    assert set(calls) == {"https://example.test/global", "https://example.test/board"}
    assert cfg == before and not A._vendor_sync_safe["v"]


def test_jobspy_uses_each_market_country_and_keeps_later_market_results(monkeypatch):
    calls = []
    def scrape(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"site": "indeed", "title": "Security Engineer", "company": "HealthTech",
                              "job_url_direct": f"https://jobs.test/{len(calls)}", "description": "HealthTech"}])
    monkeypatch.setattr("jobspy.scrape_jobs", scrape)
    monkeypatch.setattr(A, "_jobspy_scrape", lambda kwargs, bounded=False: scrape(**kwargs))
    monkeypatch.setattr(A, "_jobspy_lenient_countries", lambda: None)
    cfg = {"jobspy": {"sites": ["indeed"], "markets_per_run": 2, "workers": 1, "locations": [
        {"country": "germany", "location": "Germany"}, {"country": "japan", "location": "Japan"}]}}
    assert len(A.jobspy_source("medical security", 1, cfg)) == 2
    assert [c["country_indeed"] for c in calls] == ["germany", "japan"]


def test_failed_jobspy_site_does_not_discard_other_sites_or_repeat_429(monkeypatch):
    calls = []
    def scrape(**kwargs):
        site = kwargs["site_name"][0]
        calls.append(site)
        if site == "google":
            raise RuntimeError("HTTP 429 with private captcha query token")
        return pd.DataFrame([{"site": site, "title": "Security Engineer", "company": "HealthTech",
                              "job_url_direct": "https://jobs.test/" + site, "description": "HealthTech"}])
    monkeypatch.setattr("jobspy.scrape_jobs", scrape)
    monkeypatch.setattr(A, "_jobspy_scrape", lambda kwargs, bounded=False: scrape(**kwargs))
    monkeypatch.setattr(A, "_jobspy_lenient_countries", lambda: None)
    monkeypatch.setattr(A, "_full_list_cache", {})
    cfg = {"jobspy": {"sites": ["indeed", "google"], "markets_per_run": 2, "workers": 1, "locations": ["Germany", "Japan"]}}
    assert len(A.jobspy_source("medical security", 1, cfg)) == 2
    assert calls == ["indeed", "google", "indeed"]
    assert A._full_list_cache["jobspy_errors"][0]["http_status"] == 429
    assert "private" not in json.dumps(A._full_list_cache["jobspy_errors"])


def test_medical_jobspy_skips_irrelevant_roles_before_company_lookup(monkeypatch):
    def scrape(**kwargs):
        assert kwargs["linkedin_fetch_description"] is False
        return pd.DataFrame([{"site": "linkedin", "title": "Nurse", "company": "HealthTech", "job_url": "https://www.linkedin.com/jobs/view/123"}])
    monkeypatch.setattr("jobspy.scrape_jobs", scrape)
    monkeypatch.setattr(A, "_jobspy_scrape", lambda kwargs, bounded=False: scrape(**kwargs))
    monkeypatch.setattr(A, "_jobspy_lenient_countries", lambda: None)
    monkeypatch.setattr(A, "_full_list_cache", {})
    monkeypatch.setattr(A, "linkedin_apply_url", lambda *args: pytest.fail("irrelevant LinkedIn lookup"))
    cfg = A.get_persona_cfg(strict_config(), "appsec")
    cfg["jobspy"] = {"sites": ["linkedin"], "locations": ["Germany"]}
    assert A.jobspy_source("medical security", 20, cfg) == []


def test_bounded_jobspy_provider_has_real_process_deadline(monkeypatch):
    import subprocess
    def run(command, **kwargs):
        assert kwargs["timeout"] == 60 and kwargs["capture_output"]
        assert command[-1].endswith("jobspy_vacancy_bridge.py")
        assert json.loads(kwargs["input"])["site_name"] == ["linkedin"]
        raise subprocess.TimeoutExpired(command, 60)
    monkeypatch.setattr(A.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        A._jobspy_scrape({"site_name": ["linkedin"]}, bounded=True)


def test_new_sources_hydrate_before_ingestion_and_rotate_markets(monkeypatch, tmp_path, capsys):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(A.db.SCHEMA)
    cfg = strict_config()
    cfg["personas"]["appsec"]["search"]["discovery_sources"] = ["jobspy"]
    cfg["personas"]["appsec"]["jobspy"] = {"markets_per_run": 1, "locations": ["Germany", "Japan"]}
    listing = job("HealthTech", source="jobspy", url="https://jobs.example/listing")
    monkeypatch.setattr(A, "read_json", lambda _: copy.deepcopy(cfg))
    monkeypatch.setattr(A.db, "connect", lambda: con)
    monkeypatch.setattr(A, "cli_source", lambda *args: [listing])
    monkeypatch.setattr(M, "primary_posting", lambda j, fetch: dict(j, source="vendor", url="https://jobs.ashbyhq.com/new/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", description="HealthTech. " + PROMISES[0]))
    monkeypatch.setattr(A, "write_progress", lambda *args: None)
    monkeypatch.setattr(A, "_quit_requested", lambda: False)
    monkeypatch.setattr(A, "_QUIT_RUN", {"v": False})
    monkeypatch.setattr(A, "ROOT", tmp_path)
    args = SimpleNamespace(persona="appsec", source=None, max_keywords=1, limit=10, min_score=-999)
    A.discover(args)
    first = json.loads(capsys.readouterr().out)
    assert first["added"] == 1 and first["markets"] == ["Germany"]
    row = dict(con.execute("select * from jobs").fetchone())
    assert row["source"] == "vendor" and row["keyword"] == "jobspy:healthtech security"
    A.db.insert_application(con, {"id": row["id"], "applied_at": A.now(), "needs_manual": 1})
    A.discover(args)
    second = json.loads(capsys.readouterr().out)
    assert second["markets"] == ["Japan"] and second["added"] == 0
    assert second["excluded"]["already_attempted_or_closed"] == 1


def test_yc_medical_source_has_no_europe_only_gate(monkeypatch):
    monkeypatch.setattr("ats_schema.company_board", lambda *args, **kwargs: (("Security Engineer", "https://jobs.ashbyhq.com/new/id", "Singapore"),))
    monkeypatch.setattr(M, "primary_posting", lambda j, fetch: dict(j, source="vendor", description=PROMISES[0]))
    data = [{"status": "Active", "name": "Clinic", "one_liner": "Clinical software", "regions": ["Southeast Asia"]},
            {"status": "Active", "name": "Shop", "one_liner": "Retail software"}]
    jobs, summary = M.startups(lambda *args, **kwargs: json.dumps(data), lambda j: True)
    assert summary["medical_companies"] == 1 and len(jobs) == 1
    assert "HealthTech/MedTech" in jobs[0]["description"]


def test_himalayas_continues_cursor_and_checks_newest_jobs(monkeypatch, tmp_path):
    archive = tmp_path / "autopilot/archive"
    archive.mkdir(parents=True)
    (archive / "himalayas-medical-cursor.json").write_text('{"cursor":"older-page"}', encoding="utf-8")
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return json.dumps({"jobs": [{"title": "Security Engineer", "applicationLink": "https://jobs.test/" + str(len(calls))}],
                           "nextCursor": "", "totalCount": 100000})
    monkeypatch.setattr(A, "ROOT", tmp_path)
    monkeypatch.setattr(A, "fetch_text", fetch)
    monkeypatch.setattr(A, "_full_list_cache", {})
    cfg = {"search": {"include_global_vendor_boards": True}}
    assert len(A.himalayas_source("security", 50, cfg)) == 2
    assert "cursor=" not in calls[0] and "cursor=older-page" in calls[1]
    assert json.loads((archive / "himalayas-medical-cursor.json").read_text())["cursor"] == ""


def test_non_security_jobs_do_not_pay_full_description_scoring_cost(monkeypatch, tmp_path, capsys):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(A.db.SCHEMA)
    monkeypatch.setattr(A, "read_json", lambda _: strict_config())
    monkeypatch.setattr(A.db, "connect", lambda: con)
    monkeypatch.setattr(A, "cli_source", lambda *args: [{"title": "Nurse", "description": "x" * 1000000}])
    monkeypatch.setattr(A, "normalize", lambda *args, **kwargs: pytest.fail("irrelevant full scoring"))
    monkeypatch.setattr(A, "write_progress", lambda *args: None)
    monkeypatch.setattr(A, "_quit_requested", lambda: False)
    monkeypatch.setattr(A, "_QUIT_RUN", {"v": False})
    monkeypatch.setattr(A, "_vendor_sync_safe", {"v": False})
    monkeypatch.setattr(A, "ROOT", tmp_path)
    A.discover(SimpleNamespace(persona="appsec", source=["vendor"], max_keywords=1, limit=10, min_score=3))
    result = json.loads(capsys.readouterr().out)
    assert result["scanned"] == 1 and result["excluded"] == {"role_mismatch": 1}


def test_transient_public_fetch_file_error_retries_once(monkeypatch):
    from io import BytesIO
    from urllib.error import URLError
    calls = []
    class Response(BytesIO):
        headers = {}
    def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise URLError(FileNotFoundError("transient context initialization"))
        return Response(b'{"jobs": []}')
    monkeypatch.setattr(A.urllib.request, "urlopen", fetch)
    monkeypatch.setattr(A.time, "sleep", lambda _: None)
    assert A.fetch_text("https://boards-api.greenhouse.io/v1/boards/test/jobs") == '{"jobs": []}'
    assert len(calls) == 2


def test_public_fetch_never_retries_cloudflare_or_permanent_failure(monkeypatch):
    from urllib.error import HTTPError, URLError
    from unittest.mock import Mock
    for error, count in [(HTTPError("https://jobs.test", 403, "Forbidden", {}, None), 1),
                         (URLError(FileNotFoundError("permanent")), 2)]:
        fetch = Mock(side_effect=error)
        monkeypatch.setattr(A.urllib.request, "urlopen", fetch)
        monkeypatch.setattr(A.time, "sleep", lambda _: None)
        with pytest.raises(type(error)):
            A.fetch_text("https://jobs.test")
        assert fetch.call_count == count
