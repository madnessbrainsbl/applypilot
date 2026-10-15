import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

import autopilot as A
def config():
    return {"active_persona": "appsec", "search": {}, "personas": {"appsec": {"search": {
        "required_title_terms": ["security engineer"], "sector_focus": "healthtech_medtech",
        "sector_focus_terms": ["healthtech", "medical devices", "patient data"],
    }}}}


def job(description, **kwargs):
    return {"title": "Security Engineer", "company": "Example", "location": "Berlin, Germany",
            "persona": "appsec", "description": description, **kwargs}


def strict_config():
    cfg = config()
    cfg["personas"]["appsec"]["search"].update({
        "require_explicit_visa_and_relocation": True,
        "keywords": ["healthtech security"],
        "vendor_boards": {"greenhouse": ["https://example.test/board"]},
    })
    return cfg


@pytest.mark.parametrize("description,reason", [
    ("HealthTech. We offer visa sponsorship and a relocation package.", None),
    ("HealthTech. Visa sponsorship and relocation assistance available.", None),
    ("HealthTech. Relocation package available.", "visa_support_not_proven"),
    ("HealthTech. We sponsor visas.", "relocation_not_proven"),
    ("HealthTech. Will you require visa sponsorship? Relocation package.", "visa_support_not_proven"),
    ("HealthTech. We may provide visa sponsorship on a case-by-case basis. Relocation package.", "visa_support_not_proven"),
    ("HealthTech. We offer visa sponsorship and relocation support. We do not sponsor visas.", "support_excluded"),
    ("HealthTech. We sponsor visas. No relocation assistance.", "support_excluded"),
    ("HealthTech. Visa sponsorship is not available. Relocation package.", "support_excluded"),
    ("HealthTech. Immigration sponsorship is not available. Relocation package.", "support_excluded"),
    ("HealthTech. We sponsor visas. Relocation assistance is not provided.", "support_excluded"),
    ("HealthTech. We sponsor visas for existing H-1B holders transferring only. Relocation package.", "support_excluded"),
    ("Medical insurance. We offer visa sponsorship and relocation support.", "sector_not_proven"),
])
def test_requires_two_independent_employer_promises(description, reason):
    assert A._strict_discovery_reason(job(description, source="vendor"), strict_config()) == reason


def test_aggregator_tag_does_not_prove_employer_support():
    listing = job("HealthTech. We sponsor visas. Relocation package.", source="simplify", sponsorship="Offers Sponsorship")
    assert A._strict_discovery_reason(listing, strict_config()) == "primary_description_required"
    assert A._strict_discovery_reason(listing, config()) is None


def test_known_medical_employer_does_not_need_sector_keywords_in_every_role():
    cfg = strict_config()
    cfg["personas"]["appsec"]["search"]["sector_focus_companies"] = ["Cala Health"]
    description = "Secure cloud applications. We sponsor visas and offer a relocation package."
    assert A._strict_discovery_reason(job(description, company="Cala Health", source="vendor"), cfg) is None
    assert A._strict_discovery_reason(job(description + " Medical insurance.", source="vendor"), cfg) == "sector_not_proven"


def test_simplify_does_not_invent_visa_or_relocation(monkeypatch):
    payload = [{"title": "Security Engineer", "company_name": "Example", "url": "https://example.test/job",
                "sponsorship": "Other"}]
    monkeypatch.setattr(A, "_full_list_cache", {})
    monkeypatch.setattr(A.urllib.request, "urlopen", lambda *args, **kwargs: __import__("io").BytesIO(json.dumps(payload).encode()))
    # Avoid the live cache, including writes.
    class Cache:
        def exists(self): return False
        @property
        def parent(self): return self
        def mkdir(self, **kwargs): raise OSError("isolated")
    class Root:
        def __truediv__(self, name): return self if name == "autopilot" else Cache()
    monkeypatch.setattr(A, "ROOT", Root())
    listing = A.simplify_source("", 10)[0]
    assert "relocation" not in listing["description"].lower()
    assert not A.offers_sponsorship(listing)


@pytest.mark.parametrize("needs_manual", [0, 1])
def test_discover_preserves_all_attempts_and_skips_ats_variants(monkeypatch, capsys, tmp_path, needs_manual):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(A.db.SCHEMA)
    cfg = strict_config()
    old = A.normalize("vendor", "", {"title": "Security Engineer", "company": "Example",
        "url": "https://jobs.ashbyhq.com/example/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/application",
        "description": "HealthTech. We sponsor visas. Relocation package."}, cfg, persona="appsec")
    old["status"] = "pending_confirmation" if needs_manual else "queued"
    A.db.upsert_job(con, old)
    A.db.insert_application(con, {"id": old["id"], "applied_at": A.now(), "needs_manual": needs_manual, "apply_url": old["url"]})
    before = dict(con.execute("SELECT * FROM jobs").fetchone())
    listings = [dict(old, url=old["url"].removesuffix("/application") + "?utm_source=test"),
                dict(old, company="Example Inc.", url="https://jobs.gem.com/example/new-identity"),
                dict(old, company="New Medical Company", url="https://jobs.ashbyhq.com/new/ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee")]
    monkeypatch.setattr(A, "read_json", lambda _: copy.deepcopy(cfg))
    monkeypatch.setattr(A.db, "connect", lambda: con)
    monkeypatch.setattr(A, "cli_source", lambda *args: listings)
    monkeypatch.setattr(A, "write_progress", lambda *args: None)
    monkeypatch.setattr(A, "_quit_requested", lambda: False)
    monkeypatch.setattr(A, "_QUIT_RUN", {"v": False})
    monkeypatch.setattr(A, "_vendor_sync_safe", {"v": False})
    monkeypatch.setattr(A, "ROOT", tmp_path)
    A.discover(SimpleNamespace(persona="appsec", source=["vendor"], max_keywords=1, limit=10, min_score=-999))
    result = json.loads(capsys.readouterr().out)
    assert result["added"] == 1
    assert result["excluded"]["already_attempted_or_closed"] == 2
    assert result["marked_dead"] == 0
    assert len(json.loads(__import__("pathlib").Path(result["report"]).read_text(encoding="utf-8"))["screened"]) == 3
    assert dict(con.execute("SELECT * FROM jobs WHERE id=?", (old["id"],)).fetchone()) == before
    assert con.execute("SELECT count(*) FROM applications").fetchone()[0] == 1
    con.close()


def test_subset_board_fetch_does_not_mark_other_companies_dead(monkeypatch):
    cfg = A.get_persona_cfg(strict_config(), "appsec")
    monkeypatch.setattr(A, "fetch_text", lambda *args, **kwargs: '{"jobs": []}')
    monkeypatch.setattr(A, "_vendor_sync_safe", {"v": True})
    assert A.vendor_source(cfg, 10) == []
    assert not A._vendor_sync_safe["v"]
