import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import medical_search as M
import workspace_sources as W
from findall_vacancy_bridge import structured_posting


def test_workspace_adapter_uses_project_runtimes_without_contact_outputs(monkeypatch, tmp_path):
    root = tmp_path / "findall"
    (root / "jf").mkdir(parents=True)
    (root / "jf/careers.py").write_text("", encoding="utf-8")
    (root / ".venv/Scripts").mkdir(parents=True)
    (root / ".venv/Scripts/python.exe").write_bytes(b"")
    exe = tmp_path / "poisk/applypilot_jobs.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    calls = []
    def run(command, cwd):
        calls.append(command)
        return {"jobs": [{"title": "Security Engineer", "company": "Medical", "url": "https://jobs.ashbyhq.com/medical/id", "description": "unverified aggregator claim"}],
                "links": [], "statuses": [{"state": "partial"}]}
    monkeypatch.setattr(W, "run_json", run)
    monkeypatch.setattr(M, "primary_posting", lambda j, fetch: {**j, "source": "vendor", "description": "Current employer primary description"})
    cfg = {"search": {"workspace_sources": {"findall_root": str(root), "poisk_cli": str(exe), "queries": ["medical appsec"]}}}
    jobs, summary = W.discover("workspace_poisk", cfg, 20, None, lambda j: True, tmp_path / "evidence")
    assert len(calls) == 2 and str(exe) == calls[0][0]
    assert calls[1][0] == str(root / ".venv/Scripts/python.exe")
    assert all("contacts.json" not in " ".join(c) and "--send" not in c for c in calls)
    assert len(jobs) == 1 and jobs[0]["description"] == "Current employer primary description"
    assert summary["verified_security_postings"] == 1
    assert Path(summary["evidence"]).is_file()


def test_missing_workspace_is_a_failure_not_empty_vacancy_coverage(tmp_path):
    with pytest.raises(FileNotFoundError):
        W.discover("workspace_findall", {}, 20, None, lambda j: True, tmp_path)


def test_findall_current_structured_posting_full_description_country_and_expiry():
    from html.parser import HTMLParser
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.json_blocks = []
        def feed(self, text):
            self.json_blocks = [text]
    node = {"@type": "JobPosting", "title": "Security Engineer", "hiringOrganization": {"name": "Medical"},
            "description": "A" * 5000 + " We offer visa sponsorship and relocation support.",
            "jobLocation": [{"address": {"addressLocality": "Berlin", "addressCountry": {"name": "Germany"}}}],
            "validThrough": "2999-12-31T23:00:00Z"}
    class Transport:
        def __init__(self, *args): pass
        def fetch(self, url, hosts):
            assert hosts == {"medical.test"}
            return SimpleNamespace(status=200, error="", text=json.dumps(node), url=url)
    careers = SimpleNamespace(_Transport=Transport, _Page=Page)
    posting = structured_posting("https://medical.test/careers/security", "Security Engineer", careers)
    assert len(posting["description"]) > 5000 and posting["location"] == "Berlin, Germany"
    assert posting["primary_verified"]
    node["validThrough"] = "2020-01-01T00:00:00Z"
    assert structured_posting("https://medical.test/careers/security", "Security Engineer", careers) is None
