import json
from datetime import datetime, timedelta, timezone

import pytest

import jobwatch as W
from jobwatch_health import inspect, tor_policy

NOW = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)


def target(**changes):
    return {"status": "accepted", "updated_at": (NOW-timedelta(days=1)).isoformat(), **changes}


@pytest.mark.parametrize("targets", [[], [target(status="disabled")], [target(status="rejected")]])
def test_no_accepted_targets_never_trigger(targets):
    result = tor_policy(targets, now=NOW)
    assert not result["eligible"] and result["reason"] == "no_accepted_targets"
    assert not result["triggered"]


def test_changed_target_still_obeys_48h_cooldown():
    result = tor_policy([target()], (NOW-timedelta(hours=47)).isoformat(), now=NOW)
    assert not result["eligible"] and result["reason"] == "48h_cooldown"


def test_cooldown_applies_to_failed_attempts_too():
    result = tor_policy([target()], (NOW-timedelta(hours=2)).isoformat(), (NOW-timedelta(days=20)).isoformat(), NOW)
    assert not result["eligible"]


def test_unchanged_targets_wait_7_days():
    result = tor_policy([target(updated_at=(NOW-timedelta(days=10)).isoformat())],
                        (NOW-timedelta(days=3)).isoformat(), (NOW-timedelta(days=3)).isoformat(), NOW)
    assert result["reason"] == "targets_unchanged" and not result["eligible"]
    result = tor_policy([target(updated_at=(NOW-timedelta(days=10)).isoformat())],
                        (NOW-timedelta(days=7)).isoformat(), (NOW-timedelta(days=7)).isoformat(), NOW)
    assert result["reason"] == "7d_health_refresh_due" and result["eligible"]
    assert result["triggered"] is False


def test_new_targets_are_eligible_only_as_read_only_advice():
    result = tor_policy([target()], (NOW-timedelta(days=3)).isoformat(), (NOW-timedelta(days=3)).isoformat(), NOW)
    assert result["reason"] == "accepted_targets_changed" and result["eligible"]
    assert not result["triggered"] and result["limits"]["max_pages"] == 8
    assert result["limits"]["bootstrap_percent"] == 100


def test_target_level_cooldown_is_respected():
    result = tor_policy([target(cooldown_until=(NOW+timedelta(days=1)).isoformat())], now=NOW)
    assert not result["eligible"] and result["reason"] == "targets_cooling_down"


def test_missing_target_timing_is_not_guessed():
    assert tor_policy([target(updated_at=None)], now=NOW)["reason"] == "target_change_time_unknown"


def test_only_allowlisted_workers_can_be_repair_candidates():
    good_id = W.WORKERS["watchdog"][0]
    result = inspect({"workers": [{"automation_id": "old-intentionally-disabled", "automation_enabled": False},
        {"id": "known", "automation_id": good_id, "automation_enabled": False, "cadence_minutes": 60}],
        "worker_runs": [{"worker_id": "known", "id": "run1", "status": "running", "finished_at": None,
            "started_at": (NOW-timedelta(hours=4)).isoformat()}]}, NOW)
    candidates = [w for w in result["workers"] if w["repair_candidate"]]
    assert [w["automation_id"] for w in candidates] == [good_id]
    assert "stale_running" in candidates[0]["issues"]
    assert candidates[0]["stale_run_ids"] == ["run1"]
    assert not candidates[0]["repair_performed"] and not result["cloud_state_mutated"]


def test_registry_enabled_does_not_prove_scheduler_health():
    result = inspect({"workers": [{"automation_id": W.WORKERS["security"][0], "registry_enabled": True}]}, NOW)
    assert result["workers"][0]["automation_enabled"] is None
    assert "scheduler_state_unknown" in result["workers"][0]["issues"]


def test_started_pipeline_is_not_success_or_authorization_for_a_duplicate():
    result = inspect({"tor_targets": [target()], "tor_runtime": {"state": "running"}}, NOW)
    assert not result["tor"]["eligible"]
    assert result["tor"]["reason"] == "runtime_completion_unconfirmed"


def test_db_snapshot_survives_unavailable_drive_and_routes_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "ROOT", tmp_path)
    monkeypatch.delenv("JOBWATCH_EXPORT_DIR", raising=False)
    path = W.directory({}) / "database/snapshot.json"; path.parent.mkdir(parents=True)
    snapshot = {"schema_version": "1.0", "origin": "supabase", "generated_at": datetime.now(timezone.utc).isoformat(),
        "jobs": [{"stream": "creative", "global_job_id": "creative1"}], "grants": [], "sources": [{"canonical_key": "source1"}],
        "workers": [], "cloud_write_status": "unavailable"}
    path.write_text(json.dumps(snapshot))
    creative, _ = W.read_stream({}, "creative")
    security, _ = W.read_stream({}, "security")
    assert len(creative["jobs"]) == 1 and not security["jobs"]
    assert W.read_metadata({}, "discovery/source_registry.json")[0]["sources"] == snapshot["sources"]
    health = W.adapter_health({})
    assert "canonical DB snapshot" in health[0]["adapter"]


def test_updated_discovery_contract_accepts_source_registry_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "ROOT", tmp_path)
    monkeypatch.delenv("JOBWATCH_EXPORT_DIR", raising=False)
    path = W.directory({}) / "discovery/source_registry.json"; path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(),
                              "source_registry_snapshot": [{"canonical_key": "source1", "status": "cooldown"}]}))
    data, _ = W.read_metadata({}, "discovery/source_registry.json")
    assert data["sources"][0]["status"] == "cooldown"
