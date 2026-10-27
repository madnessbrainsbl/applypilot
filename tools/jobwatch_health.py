"""Read-only JobWatch supervision. Eligibility is advice, never a pipeline trigger."""
from datetime import datetime, timedelta, timezone

from jobwatch import WORKERS, timestamp


def tor_policy(targets, last_attempt=None, last_success=None, now=None):
    now = now or datetime.now(timezone.utc)
    accepted = [t for t in targets if t.get("status") == "accepted"]
    result = {"eligible": False, "accepted_targets": len(accepted), "reason": "no_accepted_targets",
              "runtime": "bitbucket", "pipeline": "tor-discovery", "triggered": False,
              "limits": {"runner": "1x", "max_pages": 8, "request_timeout_seconds": 20,
                         "max_depth": 2, "max_pipeline_minutes": 10, "bootstrap_percent": 100}}
    if not accepted:
        return result
    if last_attempt:
        attempt = timestamp(last_attempt)
        if now - attempt < timedelta(hours=48):
            return {**result, "reason": "48h_cooldown", "retry_at": (attempt + timedelta(hours=48)).isoformat()}
    due = [t for t in accepted if not t.get("cooldown_until") or timestamp(t["cooldown_until"]) <= now]
    if not due:
        return {**result, "reason": "targets_cooling_down"}
    if any(not t.get("updated_at") for t in due):
        return {**result, "reason": "target_change_time_unknown"}
    if not last_success or any(timestamp(t["updated_at"]) > timestamp(last_success) for t in due):
        return {**result, "eligible": True, "reason": "accepted_targets_changed"}
    success = timestamp(last_success)
    if now - success >= timedelta(days=7):
        return {**result, "eligible": True, "reason": "7d_health_refresh_due"}
    return {**result, "reason": "targets_unchanged", "retry_at": (success + timedelta(days=7)).isoformat()}


def inspect(data, now=None):
    now = now or datetime.now(timezone.utc)
    workers, all_runs = data.get("workers", []), data.get("worker_runs", [])
    if not isinstance(workers, list) or not isinstance(all_runs, list):
        raise ValueError("workers and worker_runs must be arrays")
    statuses = []
    for slug, (automation_id, cadence) in WORKERS.items():
        worker = next((w for w in workers if w.get("automation_id") == automation_id), {})
        issues = []
        if not worker:
            issues.append("worker_not_observed")
        if worker.get("cadence_minutes", cadence) != cadence:
            issues.append("schedule_mismatch")
        # Registry enabled is not the scheduler's is_enabled runtime signal.
        enabled = worker.get("automation_enabled")
        if type(enabled) is not bool:
            enabled = None
            issues.append("scheduler_state_unknown")
        elif not enabled:
            issues.append("disabled")
        runs = [r for r in all_runs if worker.get("id") and r.get("worker_id") == worker["id"]]
        stale = []
        for run in runs:
            if run.get("status") == "running" and not run.get("finished_at"):
                if now - timestamp(run.get("started_at")) > timedelta(minutes=2 * cadence):
                    stale.append(run.get("id"))
        if stale:
            issues.append("stale_running")
        if not runs:
            issues.append("never_ran")
        latest = max(runs, key=lambda r: timestamp(r["started_at"])) if runs else {}
        if latest.get("cloud_write_status") in ("unavailable", "failed", "pending"):
            issues.append("drive_persistence_unconfirmed")
        statuses.append({"slug": slug, "automation_id": automation_id, "registry_enabled": worker.get("registry_enabled"),
            "automation_enabled": enabled, "issues": issues, "stale_run_ids": stale,
            "last_run_status": latest.get("status"), "drive_write_status": latest.get("cloud_write_status"),
            "repair_candidate": enabled is False, "repair_performed": False})
    runtime = data.get("tor_runtime") or {}
    tor = tor_policy(data.get("tor_targets", []), runtime.get("last_attempt_at"), runtime.get("last_success_at"), now)
    if runtime.get("state") in ("triggered", "pending", "running") and tor["accepted_targets"]:
        tor.update(eligible=False, reason="runtime_completion_unconfirmed")
    return {"workers": statuses, "tor": tor, "ingest_counts": data.get("ingest_counts", {}),
            "automatic_repairs_performed": False, "cloud_state_mutated": False}
