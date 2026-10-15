import json

import pytest

import autopilot as A
import medical_boards as M


def role_fit(job):
    return job["title"] == "Security Engineer"


def test_workday_paginates_and_reads_primary_description(monkeypatch):
    calls, details = [], []
    def post(url, payload):
        calls.append(payload)
        suffix = "R1" if payload["offset"] == 0 else "R2"
        return {"total": 2, "jobPostings": [{"title": "Security Engineer" if suffix == "R1" else "Nurse",
                "externalPath": "/job/Berlin/Role_" + suffix, "locationsText": "Berlin"}]}
    def fetch(url, **kwargs):
        details.append(url)
        return json.dumps({"jobPostingInfo": {"jobDescription": "We offer visa sponsorship and relocation support.",
                           "location": "Berlin, Germany", "additionalLocations": ["Paris, France"]}})
    monkeypatch.setattr(M, "_post_json", post)
    jobs = M.workday_board("https://medical.wd5.myworkdayjobs.com/wday/cxs/medical/Careers/jobs", fetch, role_fit)
    assert len(jobs) == 2
    assert jobs[0]["description"].startswith("We offer visa")
    assert jobs[0]["location"] == "Berlin, Germany; Paris, France"
    assert len(details) == 1
    assert all(x["offset"] in (0, 1) for x in calls)
    assert "/wday/cxs/medical/Careers/job/Berlin/Role_R1" in details[0]


def test_workday_partial_response_is_an_error(monkeypatch):
    monkeypatch.setattr(M, "_post_json", lambda *args: {"total": 5, "jobPostings": []})
    with pytest.raises(ValueError, match="pagination"):
        M.workday_board("https://medical.wd5.myworkdayjobs.com/wday/cxs/medical/Careers/jobs", lambda *args: "", role_fit)


def test_gem_reads_public_descriptions_without_application_fields(monkeypatch):
    queries = []
    def post(url, payload):
        queries.append(payload["query"])
        assert url == "https://jobs.gem.com/api/public/graphql"
        if "JobBoardList" in payload["query"]:
            return {"data": {"jobBoardExternal": {"teamDisplayName": "Medical Inc."},
                "oatsExternalJobPostings": {"jobPostings": [{"extId": "public-posting", "title": "Security Engineer",
                    "locations": [{"name": "Berlin"}]}]}}}
        return {"data": {"oatsExternalJobPosting": {"descriptionHtml": "Visa sponsorship and relocation support available.",
                "jobPostSectionHtml": {"introHtml": "Medical devices", "outroHtml": "Benefits"}}}}
    monkeypatch.setattr(M, "_post_json", post)
    jobs = M.gem_board("https://jobs.gem.com/medical", role_fit)
    assert jobs[0]["url"] == "https://jobs.gem.com/medical/public-posting"
    assert "Medical devices" in jobs[0]["description"]
    assert len(queries) == 2
    assert all("mutation" not in q and "applicationForm" not in q for q in queries)


def test_uhn_uses_its_current_catalog_and_preserves_smartrecruiters_identity():
    def fetch(url, **kwargs):
        if url.endswith("UHNCareers"):
            return '<data-grid data-url="/UHNCareers/Home/GetAll?tcnt=1">'
        return json.dumps({"data": [{"id": "744000123456789", "name": "Nurse", "site": "Toronto"}]})
    jobs = M.uhn_board("https://forms.uhn.ca/UHNCareers", fetch, role_fit)
    assert jobs[0]["url"] == "https://jobs.smartrecruiters.com/UniversityHealthNetwork/744000123456789"
    assert jobs[0]["description"] == ""


def test_uhn_security_detail_failure_is_not_an_empty_or_eligible_posting():
    def fetch(url, **kwargs):
        if url.endswith("UHNCareers"):
            return '<data-grid data-url="/UHNCareers/Home/GetAll?tcnt=1">'
        if "/GetAll" in url:
            return json.dumps({"data": [{"id": "744000123456789", "name": "Security Engineer"}]})
        raise OSError("HTTP 403")
    with pytest.raises(OSError, match="403"):
        M.uhn_board("https://forms.uhn.ca/UHNCareers", fetch, role_fit)


def test_smartrecruiters_uses_public_pagination_and_full_sections():
    urls = []
    def fetch(url, **kwargs):
        urls.append(url)
        assert "destination=PUBLIC" in url
        if "limit=" in url:
            return json.dumps({"totalFound": 1, "content": [{"id": "744000123456789", "name": "Security Engineer"}]})
        return json.dumps({"jobAd": {"sections": {"companyDescription": {"text": "Medical devices"},
            "jobDescription": {"text": "We sponsor visas."}, "additionalInformation": {"text": "Relocation support."}}}})
    jobs = M.smartrecruiters_board("https://api.smartrecruiters.com/v1/companies/MedicalCompany/postings", fetch, role_fit)
    assert len(urls) == 2
    assert "Relocation support" in jobs[0]["description"]
    assert jobs[0]["url"].startswith("https://jobs.smartrecruiters.com/MedicalCompany/")


def test_partial_large_board_keeps_already_fetched_postings(monkeypatch):
    def fetch(url, **kwargs):
        return json.dumps({"totalFound": 5000, "content": [{"id": str(i + 1), "name": "Nurse"} for i in range(1000)]})
    with pytest.raises(M.PartialBoardError) as error:
        M.smartrecruiters_board("https://api.smartrecruiters.com/v1/companies/MedicalCompany/postings", fetch, role_fit)
    assert len(error.value.jobs) == 3000
    def partial(*args):
        raise M.PartialBoardError("bounded scan", [{"title": "Security Engineer", "url": "https://jobs.test/role", "description": "Primary"}])
    monkeypatch.setattr(M, "smartrecruiters_board", partial)
    cfg = {"vendor_boards": {"smartrecruiters": ["https://api.smartrecruiters.com/v1/companies/MedicalCompany/postings"]}}
    assert len(A.vendor_source(cfg, 100)) == 1
    assert A._vendor_board_status[-1]["status"] == "partial" and not A._vendor_sync_safe["v"]


@pytest.mark.parametrize("kind,url", [("gem", "https://evil.test/medical"),
    ("workday", "https://evil.test/wday/cxs/medical/site/jobs"),
    ("uhn", "https://evil.test/UHNCareers")])
def test_source_adapters_reject_foreign_endpoints(kind, url):
    with pytest.raises(ValueError):
        if kind == "gem": M.gem_board(url, role_fit)
        elif kind == "workday": M.workday_board(url, lambda *args: "", role_fit)
        else: M.uhn_board(url, lambda *args: "", role_fit)


def test_workday_and_gem_application_urls_share_posting_identity():
    assert A._ats_job_key("https://medical.wd5.myworkdayjobs.com/en-US/Careers/job/Berlin/Security_R12345-1") == A._ats_job_key(
        "https://medical.wd5.myworkdayjobs.com/Careers/job/Security_R12345")
    assert A._ats_job_key("https://jobs.gem.com/medical/public-posting") == A._ats_job_key(
        "https://jobs.gem.com/medical/public-posting/application?utm_source=mail")
    assert A._ats_job_key("https://evil.test/Careers/job/Security_R12345") is None
