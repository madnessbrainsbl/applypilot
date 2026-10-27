import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import autopilot as A
import db
import jobwatch as W
import sponsors


@pytest.mark.parametrize("command", ["discover", "run"])
def test_cli_accepts_jobwatch_source(command):
    assert A.build_parser().parse_args([command, "--source", "jobwatch"]).source == ["jobwatch"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(W, "ROOT", tmp_path)
    monkeypatch.delenv("JOBWATCH_EXPORT_DIR", raising=False)
    monkeypatch.setattr(sponsors, "is_known_sponsor", lambda *a: False)
    monkeypatch.setattr(A, "pick_cv", lambda job, cfg: job["persona"] + ".pdf")
    cfg = {"active_persona": "appsec", "profile": {}, "search": {}, "apply": {"require_sponsorship": True, "sponsorship_waived": {"creative": "europe"}},
        "personas": {"appsec": {"search": {"keywords": ["security"], "required_title_terms": ["security engineer"], "excluded_title_terms": ["senior"]}},
                     "creative": {"search": {"keywords": ["photographer"], "required_title_terms": ["photographer"], "excluded_title_terms": ["senior"]}}}}
    with db.connect() as con:
        yield con, cfg


def row(**changes):
    return {"global_job_id": "global-security-1", "stream": "security", "state": "active",
        "title": "Security Engineer", "company": "Example", "country": "Germany", "city": "Berlin",
        "canonical_url": "https://jobs.lever.co/example/123456",
        "description": "Application security and code review. We offer visa sponsorship and relocation assistance.",
        "published_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), **changes}


def export(cfg, rows, persona="appsec", generated=None, grants=None, **changes):
    stream = W.STREAMS[persona]
    data = {"schema_version": "1.0", "stream": stream, "generated_at": generated or datetime.now(timezone.utc).isoformat(),
            "jobs": rows, "grants": grants or [], **changes}
    path = W.directory(cfg) / stream / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def sync(env, persona="appsec"):
    con, cfg = env
    return W.sync(con, A.get_persona_cfg(cfg, persona), persona, 3, A)


def test_new_unchanged_changed_closed_and_stale(env):
    con, cfg = env
    j = row()
    export(cfg, [j])
    assert sync(env)["added"] == 1
    original = dict(con.execute("SELECT * FROM jobs").fetchone())
    export(cfg, [j])
    assert sync(env)["unchanged"] == 1
    assert con.execute("SELECT count(*) FROM jobwatch_events").fetchone()[0] == 1
    j["description"] += " Build secure Python tooling."
    export(cfg, [j])
    assert sync(env)["updated"] == 1
    assert con.execute("SELECT created_at FROM jobs").fetchone()[0] == original["created_at"]
    closed = {**j, "state": "closed"}
    export(cfg, [closed])
    assert sync(env)["closed"] == 1
    export(cfg, [j], generated=original["created_at"])
    assert sync(env)["stale"] == 1
    assert con.execute("SELECT dead_listing FROM jobs").fetchone()[0] == 1
    assert [r[0] for r in con.execute("SELECT event_type FROM jobwatch_events")] == ["new", "changed", "closed"]


def test_absence_from_incremental_feed_does_not_close(env):
    con, cfg = env
    export(cfg, [row()]); sync(env)
    export(cfg, []); sync(env)
    assert con.execute("SELECT dead_listing FROM jobs").fetchone()[0] == 0


def test_first_closure_can_match_a_job_already_found_by_another_source(env):
    con, cfg = env
    j = row()
    saved = A.normalize("vendor", "", {**j, "url": j["canonical_url"]}, cfg, persona="appsec")
    db.upsert_job(con, saved)
    export(cfg, [{**j, "state": "closed"}])
    assert sync(env)["closed"] == 1
    assert con.execute("SELECT dead_listing FROM jobs").fetchone()[0] == 1


def test_rejected_cross_persona_record_cannot_close_another_personas_job(env):
    con, cfg = env
    j = row()
    saved = A.normalize("vendor", "", {**j, "url": j["canonical_url"]}, cfg, persona="creative")
    db.upsert_job(con, saved)
    export(cfg, [j]); assert sync(env)["excluded"] == {"existing_persona_conflict": 1}
    export(cfg, [{**j, "state": "closed"}]); assert sync(env)["closed"] == 0
    assert con.execute("SELECT persona,dead_listing FROM jobs").fetchone()["dead_listing"] == 0


@pytest.mark.parametrize("changes,reason", [
    ({"title": "Senior Security Engineer"}, "role_mismatch"),
    ({"title": "Photographer"}, "role_mismatch"),
    ({"published_at": (datetime.now(timezone.utc)-timedelta(days=20)).isoformat()}, "posting_age"),
    ({"published_at": None}, "publication_date_unproven"),
    ({"country": "United States", "city": "Boston", "description": "US residents only. No visa sponsorship."}, "visa_path"),
    ({"country": "United Kingdom", "city": "London", "description": "Must already have the right to work in the UK. No visa sponsorship."}, "visa_path"),
])
def test_external_scores_never_override_persona_age_and_visa_gates(env, changes, reason):
    con, cfg = env
    export(cfg, [row(fit_score=100, apply_priority="urgent", **changes)])
    assert sync(env)["excluded"] == {reason: 1}
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_creative_uses_own_cv_and_keeps_europe_waiver(env):
    con, cfg = env
    export(cfg, [row(stream="creative", title="Photographer", description="Produce portraits and videos.")], persona="creative")
    assert sync(env, "creative")["added"] == 1
    saved = con.execute("SELECT persona,cv FROM jobs").fetchone()
    assert tuple(saved) == ("creative", "creative.pdf")


@pytest.mark.parametrize("url", ["http://127.0.0.1/jobs/1", "https://linkedin.com/jobs/view/123456", "https://example.onion/opportunities/apply/123", "https://jobs.lever.co/example"])
def test_leads_and_non_employer_addresses_do_not_enter_apply_queue(env, url):
    con, cfg = env
    export(cfg, [row(canonical_url=url)])
    result = sync(env)
    if "linkedin.com" in url or url == "https://jobs.lever.co/example":
        assert result["excluded"] == {"primary_employer_url_unproven": 1}
        assert con.execute("SELECT disposition FROM jobwatch_records").fetchone()[0] == "primary_employer_url_unproven"
    else:
        assert result["errors"] == 1
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_mobility_boolean_is_not_fabricated_visa_evidence(env):
    con, cfg = env
    export(cfg, [row(city="London", country="United Kingdom", description="Secure our Python services.",
                     mobility={"visa_sponsorship": True, "relocation": True})])
    assert sync(env)["excluded"] == {"visa_path": 1}


def test_cross_source_url_dedup_preserves_application_history_and_cv(env):
    con, cfg = env
    j = row()
    previous = A.normalize("vendor", "", {**j, "url": j["canonical_url"], "location": "Berlin, Germany"}, cfg, persona="appsec")
    db.upsert_job(con, previous)
    db.insert_application(con, {"id": previous["id"], "submitted": True, "applied_at": A.now(), "confirmation": {"proof": "fixture"}})
    export(cfg, [j]); assert sync(env)["updated"] == 1
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert con.execute("SELECT status FROM jobs").fetchone()[0] == "applied"
    assert con.execute("SELECT count(*) FROM applications").fetchone()[0] == 1


def test_global_id_links_changed_urls_to_same_local_record(env):
    con, cfg = env
    j = row(); export(cfg, [j]); sync(env)
    j["canonical_url"] = "https://jobs.lever.co/example/654321"
    export(cfg, [j]); assert sync(env)["updated"] == 1
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert con.execute("SELECT url FROM jobs").fetchone()[0] == j["canonical_url"]


def test_pending_confirmation_and_hidden_flags_are_never_reset(env):
    con, cfg = env
    j = row(); export(cfg, [j]); sync(env)
    con.execute("UPDATE jobs SET status='pending_confirmation',hidden=1,claim_token='worker-owned'")
    con.commit()
    j["description"] += " Python tooling."
    export(cfg, [j]); sync(env)
    saved = con.execute("SELECT status,hidden,claim_token FROM jobs").fetchone()
    assert tuple(saved) == ("pending_confirmation", 1, "worker-owned")


def test_grants_and_discovery_metadata_are_audited_separately(env):
    con, cfg = env
    stamp = datetime.now(timezone.utc).isoformat()
    directory = W.directory(cfg) / "discovery"; directory.mkdir(parents=True)
    (directory / "source_registry.json").write_text(json.dumps({"schema_version": "1.0", "generated_at": stamp,
        "sources": [{"canonical_key": "public-board", "status": "cooldown", "failure_count": 3}],
        "strategies": [{"id": "search1", "weight": .7, "accepted_yield": 12}],
        "social_leads": [{"id": "lead1", "source_url": "https://example.org/community", "state": "unverified"}]}))
    export(cfg, [], grants=[{"global_grant_id": "grant1", "state": "active", "programme": "Photography fellowship"}])
    assert sync(env)["errors"] == 0
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert {r[0] for r in con.execute("SELECT kind FROM jobwatch_records")} == {"grant", "source", "strategy", "social_lead"}
    assert con.execute("SELECT count(*) FROM jobwatch_events").fetchone()[0] == 4
    sync(env)
    assert con.execute("SELECT count(*) FROM jobwatch_events").fetchone()[0] == 4


def test_placeholder_malformed_and_wrong_stream_exports_do_not_touch_jobs(env):
    con, cfg = env
    path = export(cfg, [], generated_at=None)
    with pytest.raises(ValueError, match="first completed"):
        sync(env)
    assert W.adapter_health(cfg)[0]["status"] == "error"
    path.write_text('{"schema_version":"1.0",')
    with pytest.raises(ValueError): sync(env)
    export(cfg, [], stream="creative")
    with pytest.raises(ValueError, match="stream"): sync(env)
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_policy_changes_recheck_rejected_records_without_duplicate_events(env):
    con, cfg = env
    export(cfg, [row(title="Senior Security Engineer")]); sync(env)
    cfg["personas"]["appsec"]["search"]["excluded_title_terms"] = []
    assert sync(env)["added"] == 1
    assert [r[0] for r in con.execute("SELECT event_type FROM jobwatch_events")] == ["new", "policy_changed"]


def test_expiry_is_rechecked_even_when_the_export_content_is_unchanged(env, monkeypatch):
    con, cfg = env
    now = datetime.now(timezone.utc)
    j = row(expires_at=(now + timedelta(minutes=1)).isoformat())
    export(cfg, [j]); assert sync(env)["added"] == 1
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return now + timedelta(minutes=2)
    monkeypatch.setattr(W, "datetime", Later)
    assert sync(env)["excluded"] == {"expired": 1}
    assert con.execute("SELECT score FROM jobs").fetchone()[0] == -999


def test_changed_visa_denial_removes_previous_jobwatch_job_from_ready_queue(env):
    con, cfg = env
    j = row(city="London", country="United Kingdom"); export(cfg, [j]); sync(env)
    j["description"] = "No visa sponsorship. Must already have right to work in the UK."
    export(cfg, [j])
    assert sync(env)["excluded"] == {"visa_path": 1}
    assert con.execute("SELECT score FROM jobs").fetchone()[0] == -999


def test_only_last_seen_changes_do_not_create_material_events(env):
    con, cfg = env
    j = row(); export(cfg, [j]); sync(env)
    export(cfg, [{**j, "last_seen_at": datetime.now(timezone.utc).isoformat(), "fit_score": 999}])
    assert sync(env)["unchanged"] == 1
    assert con.execute("SELECT count(*) FROM jobwatch_events").fetchone()[0] == 1


def test_replayed_same_version_with_different_contents_is_an_error(env):
    con, cfg = env
    j = row(); stamp = datetime.now(timezone.utc).isoformat()
    export(cfg, [j], generated=stamp); sync(env)
    export(cfg, [{**j, "company": "Conflicting company"}], generated=stamp)
    assert sync(env)["errors"] == 1
    assert con.execute("SELECT company FROM jobs").fetchone()[0] == "Example"


def test_strict_medical_visa_and_relocation_policy_is_preserved(env):
    con, cfg = env
    search = cfg["personas"]["appsec"]["search"]
    search.update(require_explicit_visa_and_relocation=True, sector_focus="healthtech_medtech", sector_focus_terms=["medical"], exclude_russia=True)
    j = row(description="Secure medical devices. Visa sponsorship and relocation assistance available.")
    export(cfg, [j]); assert sync(env)["added"] == 1
    j["description"] = "Secure medical devices. Visa sponsorship available."
    export(cfg, [j])
    assert sync(env)["added"] == 0
    saved = con.execute("SELECT disposition FROM jobwatch_records WHERE kind='job'").fetchone()[0]
    assert saved != "accepted"


def test_missing_description_is_not_replaced_by_requirements_or_mobility_guess(env):
    con, cfg = env
    export(cfg, [row(description="", mobility={"visa": True}, requirements={"skill": "Python"})])
    assert sync(env)["excluded"] == {"primary_description_unproven": 1}
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_report_exposes_grants_without_enqueuing_them(env):
    con, cfg = env
    assert W.report(con, cfg, "grant", 10)["records"] == []
    export(cfg, [], grants=[{"global_grant_id": "grant1", "programme": "Science photography"}]); sync(env)
    report = W.report(con, cfg, "grant", 10)
    assert report["records"][0]["payload"]["programme"] == "Science photography"


def test_discover_source_is_wired_for_both_profiles_and_reports_errors(env, monkeypatch, capsys):
    con, cfg = env
    export(cfg, [row()])
    export(cfg, [row(stream="creative", title="Photographer", description="Shoot portraits.", canonical_url="https://jobs.lever.co/photo/987654")], persona="creative")
    monkeypatch.setattr(A, "read_json", lambda *_: copy.deepcopy(cfg))
    monkeypatch.setattr(A, "write_progress", lambda *a: None)
    monkeypatch.setattr(A, "_quit_requested", lambda: False)
    monkeypatch.setattr(A, "_QUIT_RUN", {"v": False})
    A.discover(SimpleNamespace(persona="all", source=["jobwatch"], max_keywords=1, limit=10, min_score=3))
    report = json.loads(capsys.readouterr().out)
    assert report["added"] == 2 and set(report["jobwatch"]) == {"appsec", "creative"}
    assert not report["source_errors"]
    export(cfg, [], generated_at=None)
    A.discover(SimpleNamespace(persona="appsec", source=["jobwatch"], max_keywords=1, limit=10, min_score=3))
    report = json.loads(capsys.readouterr().out)
    assert report["source_errors"][0]["source"] == "jobwatch"
    source = next(r for r in A.sources_overview(con, cfg) if r["source"] == "jobwatch")
    assert source["known"] and source["last_errors"] == 1 and source["adapters"][0]["status"] == "error"
