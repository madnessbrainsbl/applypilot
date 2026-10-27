"""Consume JobWatch canonical snapshots/exports without sharing cloud credentials."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import db
import employer_url as EU
from remote_visa_boards import _public_apply_url

ROOT = Path(__file__).resolve().parents[1]
STREAMS = {"appsec": "security", "creative": "creative"}
MAX_BYTES = 20 * 1024 * 1024
WORKERS = {"security": ("jobwatch-security", 240),
           "creative": ("jobwatch-creative", 240),
           "discovery": ("jobwatch-discovery", 60),
           "watchdog": ("jobwatch-watchdog", 60)}
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobwatch_records (
  stream TEXT NOT NULL, kind TEXT NOT NULL, global_id TEXT NOT NULL,
  content_hash TEXT NOT NULL, version_at TEXT NOT NULL, local_job_id TEXT,
  disposition TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL,
  policy_key TEXT NOT NULL,
  PRIMARY KEY(stream,kind,global_id)
);
CREATE TABLE IF NOT EXISTS jobwatch_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, stream TEXT NOT NULL, kind TEXT NOT NULL,
  global_id TEXT NOT NULL, content_hash TEXT NOT NULL, event_type TEXT NOT NULL,
  version_at TEXT NOT NULL, disposition TEXT NOT NULL, snapshot TEXT NOT NULL,
  observed_at TEXT NOT NULL
);
"""


def directory(cfg):
    value = os.environ.get("JOBWATCH_EXPORT_DIR") or cfg.get("discovery", {}).get("jobwatch", {}).get("directory")
    path = Path(value) if value else ROOT / "autopilot/jobwatch"
    return path if path.is_absolute() else ROOT / path


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return date.astimezone(timezone.utc)


def read_export(path, stream=None):
    with path.open("rb") as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("export exceeds 20 MiB")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ValueError("expected JobWatch schema_version 1.0")
    if data.get("generated_at") is None:
        raise ValueError("awaiting first completed JobWatch export")
    generated = timestamp(data["generated_at"])
    if generated > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("export timestamp is in the future")
    if stream and data.get("stream") != stream:
        raise ValueError("export stream does not match the requested persona")
    keys = ("jobs", "grants") if stream else ()
    for key in keys:
        if not isinstance(data.get(key), list) or len(data[key]) > 20000:
            raise ValueError(f"invalid {key} array")
    return data, generated


def read_stream(cfg, stream):
    """Use the newest verified file; a DB snapshot also works when Drive is unavailable."""
    candidates, errors = [], []
    snapshot = directory(cfg) / "database/snapshot.json"
    if snapshot.exists():
        try:
            data, generated = read_export(snapshot)
            if data.get("origin") != "supabase" or not isinstance(data.get("jobs"), list) or not isinstance(data.get("grants"), list):
                raise ValueError("invalid canonical DB snapshot")
            data = {**data, "stream": stream, "jobs": [j for j in data["jobs"] if j.get("stream") == stream],
                    "grants": [g for g in data["grants"] if g.get("stream") == stream]}
            candidates.append((data, generated))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            errors.append(str(error))
    try:
        candidates.append(read_export(directory(cfg) / stream / "latest.json", stream))
    except (OSError, ValueError) as error:
        errors.append(str(error))
    if not candidates:
        raise ValueError("; ".join(errors))
    return max(candidates, key=lambda item: item[1])


def adapter_health(cfg):
    result = []
    for persona, stream in STREAMS.items():
        row = {"adapter": f"JobWatch {stream}", "persona": persona, "raw": 0, "kept": 0,
               "errors": 0, "status": "ok", "t": None, "retry_at": None}
        try:
            data, generated = read_stream(cfg, stream)
            row.update(raw=len(data["jobs"]), t=generated.isoformat())
            if data.get("origin") == "supabase":
                row["adapter"] += ": canonical DB snapshot"
            if data.get("db_write_status") in ("failed", "unavailable", "pending"):
                row.update(status="partial", errors=1)
            if datetime.now(timezone.utc) - generated > timedelta(hours=12):
                row.update(status="partial", errors=1, adapter=f"JobWatch {stream}: export older than 12h")
        except (OSError, ValueError):
            row.update(status="error", errors=1, adapter=f"JobWatch {stream}: awaiting valid export")
        result.append(row)
    for label, path in (("discovery", "discovery/source_registry.json"), ("watchdog", "health/status.json")):
        row = {"adapter": f"JobWatch {label}", "persona": "all", "raw": 0, "kept": 0,
               "errors": 0, "status": "ok", "t": None, "retry_at": None}
        try:
            data, generated = read_metadata(cfg, path)
            row["t"] = generated.isoformat()
            row["raw"] = len(data.get("sources", data.get("workers", [])))
            if datetime.now(timezone.utc) - generated > timedelta(hours=3) or data.get("overall_status", "ok") not in ("ok", "healthy"):
                row.update(status="partial", errors=1)
        except (OSError, ValueError, TypeError):
            row.update(status="error", errors=1, adapter=f"JobWatch {label}: awaiting valid export")
        result.append(row)
    try:
        data, generated = read_metadata(cfg, "health/status.json")
        from jobwatch_health import inspect
        runtime = inspect(data)
        for status in runtime["workers"]:
            if status["issues"]:
                result.append({"adapter": f"JobWatch {status['slug']}: {', '.join(status['issues'])}",
                    "persona": "all", "status": "partial", "raw": 0, "kept": 0,
                    "errors": len(status["issues"]), "t": generated.isoformat(), "retry_at": None})
        result.append({"adapter": "Bitbucket/Tor: " + runtime["tor"]["reason"], "persona": "all",
            "status": "disabled" if not runtime["tor"]["eligible"] else "ok", "raw": runtime["tor"]["accepted_targets"],
            "kept": 0, "errors": 0, "t": generated.isoformat(), "retry_at": runtime["tor"].get("retry_at")})
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result


def _material(row):
    volatile = {"id", "content_hash", "grant_content_hash", "first_seen_at", "last_seen_at",
                "last_changed_at", "created_at", "updated_at", "fit_score", "apply_priority"}
    return {k: v for k, v in row.items() if k not in volatile}


def _save(con, stream, kind, gid, row, version, job_id, disposition, previous, policy=""):
    snapshot = json.dumps(row, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(json.dumps(_material(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    observed = datetime.now(timezone.utc).isoformat()
    con.execute("""INSERT INTO jobwatch_records VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stream,kind,global_id) DO UPDATE SET content_hash=excluded.content_hash,
        version_at=excluded.version_at, local_job_id=excluded.local_job_id,
        disposition=excluded.disposition,payload=excluded.payload,observed_at=excluded.observed_at,
        policy_key=excluded.policy_key""",
        (stream, kind, gid, digest, version.isoformat(), job_id, disposition, snapshot, observed, policy))
    if not previous or digest != previous["content_hash"] or disposition != previous["disposition"]:
        event = "policy_changed" if previous and digest == previous["content_hash"] else (
            "closed" if row.get("state") in ("closed", "expired", "removed", "withdrawn") else (
            "changed" if previous else "new")
        )
        con.execute("INSERT INTO jobwatch_events(stream,kind,global_id,content_hash,event_type,version_at,disposition,snapshot,observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (stream, kind, gid, digest, event, version.isoformat(), disposition, snapshot, observed))


def _job(row, persona, cfg, engine):
    title, company = row.get("title"), row.get("company")
    if not isinstance(title, str) or not isinstance(company, str) or not title.strip() or not company.strip():
        raise ValueError("missing title/company")
    url = row.get("application_url") or row.get("canonical_url") or ""
    if (not isinstance(url, str) or not _public_apply_url(url) or (urlparse(url).hostname or "").endswith(".onion")):
        raise ValueError("original public employer vacancy URL required")
    raw = row.get("raw") or {}
    description = row.get("description") or (raw.get("description") if isinstance(raw, dict) else "") or ""
    if not isinstance(description, str):
        raise ValueError("description must be text")
    location = row.get("location") or ", ".join(str(row[k]) for k in ("city", "region", "country") if row.get(k))
    job = engine.normalize("jobwatch", "", {"title": title, "company": company, "location": location,
                           "url": url, "description": description}, cfg, persona=persona)
    if EU.classify_url(url) == "aggregator" or not EU.is_job_url(url):
        return job, "primary_employer_url_unproven"
    if not engine._strip_html(description).strip():
        return job, "primary_description_unproven"
    # Never use first-seen/import time to rejuvenate an old vacancy.
    published = row.get("published_at")
    if not published:
        return job, "publication_date_unproven"
    age = datetime.now(timezone.utc) - timestamp(published)
    if age < timedelta(minutes=-5) or age > timedelta(days=14):
        return job, "posting_age"
    job["created_at"] = timestamp(published).isoformat()
    if row.get("expires_at") and timestamp(row["expires_at"]) <= datetime.now(timezone.utc):
        return job, "expired"
    if engine.classify_persona(title, cfg) != persona or not engine.title_fit(job, cfg):
        return job, "role_mismatch"
    import sponsors
    if persona == "creative" and not (sponsors.europe_location(job) or engine.needs_no_visa(job) or engine.visa_free_location(job)):
        return job, "geography"
    if not sponsors.visa_path(job, persona, engine.offers_sponsorship, engine.needs_no_visa, engine.visa_free_location, cfg=cfg):
        return job, "visa_path"
    if cfg.get("search", {}).get("require_explicit_visa_and_relocation"):
        # Existing strict policy still needs a primary description, not upstream fit scores.
        reason = engine._strict_discovery_reason(job, cfg)
        if reason:
            return job, reason
    return job, None


def sync_metadata(con, cfg):
    """Preserve discovery/learning/health data for review; never execute source URLs."""
    errors = 0
    for relative, stream, groups in (
        ("discovery/source_registry.json", "discovery", (("sources", "source"), ("strategies", "strategy"), ("social_leads", "social_lead"))),
        ("health/status.json", "health", (("workers", "worker_health"),)),
    ):
        path = directory(cfg) / relative
        if not path.exists() and not (directory(cfg) / "database/snapshot.json").exists():
            continue
        try:
            data, version = read_metadata(cfg, relative)
            for key, kind in groups:
                rows = data.get(key, [])
                if not isinstance(rows, list) or len(rows) > 20000:
                    raise ValueError("invalid metadata array")
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError("invalid metadata object")
                    gid = row.get("canonical_key") or row.get("slug") or row.get("id") or row.get("source_url")
                    if not isinstance(gid, str) or not gid:
                        raise ValueError("metadata id required")
                    old = con.execute("SELECT * FROM jobwatch_records WHERE stream=? AND kind=? AND global_id=?", (stream, kind, gid)).fetchone()
                    if not old or version >= timestamp(old["version_at"]):
                        _save(con, stream, kind, gid, row, version, None, "review_only", old)
        except (OSError, ValueError, TypeError):
            errors += 1
    return errors


def read_metadata(cfg, relative):
    candidates = []
    snapshot = directory(cfg) / "database/snapshot.json"
    if snapshot.exists():
        try:
            data, version = read_export(snapshot)
            if data.get("origin") != "supabase":
                raise ValueError("unidentified snapshot origin")
            candidates.append((data, version))
        except (OSError, ValueError):
            pass
    try:
        candidates.append(read_export(directory(cfg) / relative))
    except (OSError, ValueError):
        if not candidates:
            raise
    data, version = max(candidates, key=lambda item: item[1])
    if "source_registry_snapshot" in data:
        sources = data["source_registry_snapshot"]
        data = {**data, "sources": sources.get("sources", []) if isinstance(sources, dict) else sources}
    return data, version


def sync(con, cfg, persona, min_score, engine):
    """Atomic per-stream import; absent records never mean closed in a delta feed."""
    stream = STREAMS.get(persona)
    if not stream:
        raise ValueError("JobWatch has no export for this persona")
    data, generated = read_stream(cfg, stream)
    con.executescript(SCHEMA)
    policy = hashlib.sha256(json.dumps({"search": cfg.get("search"), "apply": cfg.get("apply"),
        "personas": {p: c.get("search") for p, c in cfg.get("personas", {}).items()}, "version": 2}, sort_keys=True).encode()).hexdigest()
    stats = {"found": len(data["jobs"]), "added": 0, "updated": 0, "closed": 0,
             "unchanged": 0, "stale": 0, "errors": 0, "excluded": {}}
    local = {}
    for existing in con.execute("SELECT id,url,persona FROM jobs"):
        local.setdefault(engine.canonical_url(existing["url"]), existing)
        key = engine._ats_job_key(existing["url"])
        if key:
            local.setdefault(key, existing)
    with con:
        stats["metadata_errors"] = sync_metadata(con, cfg)
        for kind, rows in (("job", data["jobs"]), ("grant", data["grants"])):
            for row in rows:
                try:
                    if not isinstance(row, dict):
                        raise ValueError("record must be an object")
                    gid = row.get("global_job_id" if kind == "job" else "global_grant_id")
                    if not isinstance(gid, str) or not 1 <= len(gid) <= 500:
                        raise ValueError("stable global id required")
                    if row.get("stream", stream) != stream:
                        raise ValueError("cross-persona stream mismatch")
                    version = timestamp(row.get("last_changed_at") or data["generated_at"])
                    if version > generated:
                        raise ValueError("record newer than export")
                    old = con.execute("SELECT * FROM jobwatch_records WHERE stream=? AND kind=? AND global_id=?", (stream, kind, gid)).fetchone()
                    digest = hashlib.sha256(json.dumps(_material(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    if old and version < timestamp(old["version_at"]):
                        stats["stale"] += 1
                        continue
                    time_check = old and old["disposition"] == "accepted" and (
                        (row.get("expires_at") and timestamp(row["expires_at"]) <= datetime.now(timezone.utc)) or
                        (row.get("published_at") and datetime.now(timezone.utc) - timestamp(row["published_at"]) > timedelta(days=14)))
                    if old and digest == old["content_hash"] and old["policy_key"] == policy and not time_check:
                        con.execute("UPDATE jobwatch_records SET version_at=? WHERE stream=? AND kind=? AND global_id=?", (version.isoformat(), stream, kind, gid))
                        stats["unchanged"] += 1
                        continue
                    if old and version == timestamp(old["version_at"]) and digest != old["content_hash"]:
                        raise ValueError("conflicting content at the same version")
                    job_id, disposition = old["local_job_id"] if old else None, "review_only"
                    if kind == "job":
                        state = row.get("state", "active")
                        if state in ("closed", "expired", "removed", "withdrawn"):
                            if not job_id:
                                url = row.get("application_url") or row.get("canonical_url")
                                existing = local.get(engine.canonical_url(url or "")) or local.get(engine._ats_job_key(url or ""))
                                if existing and existing["persona"] == persona:
                                    job_id = existing["id"]
                            if job_id:
                                con.execute("UPDATE jobs SET dead_listing=1 WHERE id=?", (job_id,))
                                stats["closed"] += 1
                            disposition = "closed"
                        elif state not in ("active", "new", "changed", "open"):
                            raise ValueError("unknown job state")
                        else:
                            job, reason = _job(row, persona, cfg, engine)
                            if not reason and job["score"] < min_score:
                                reason = "score"
                            existing = con.execute("SELECT persona FROM jobs WHERE id=?", (job_id,)).fetchone() if job_id else None
                            if existing and existing["persona"] != persona:
                                reason = "existing_persona_conflict"
                            if not job_id:
                                existing = local.get(engine.canonical_url(job["url"])) or local.get(engine._ats_job_key(job["url"]))
                                if existing and existing["persona"] != persona:
                                    reason = "existing_persona_conflict"
                                job_id = existing["id"] if existing and existing["persona"] == persona else job["id"]
                            if reason:
                                disposition = reason
                                stats["excluded"][reason] = stats["excluded"].get(reason, 0) + 1
                                if old and old["local_job_id"]:
                                    con.execute("UPDATE jobs SET score=-999 WHERE id=? AND status='queued' AND source='jobwatch'", (old["local_job_id"],))
                                else:
                                    job_id = None
                            else:
                                job["id"] = job_id
                                exists = db.job_exists(con, job_id)
                                db.upsert_job(con, job, commit=False)
                                # Preserve original posting age; the shared upsert uses its time only for updated_at on conflict.
                                con.execute("UPDATE jobs SET created_at=? WHERE id=? AND source='jobwatch'", (job["created_at"], job_id))
                                con.execute("UPDATE jobs SET url=? WHERE id=? AND source='jobwatch' AND status='queued' AND NOT EXISTS(SELECT 1 FROM applications WHERE job_id=?)", (job["url"], job_id, job_id))
                                stats["updated" if exists else "added"] += 1
                                local[engine.canonical_url(job["url"])] = {"id": job_id, "persona": persona}
                                disposition = "accepted"
                    _save(con, stream, kind, gid, row, version, job_id, disposition, old, policy)
                except (ValueError, TypeError, KeyError):
                    stats["errors"] += 1
    stats["errors"] += stats["metadata_errors"]
    return stats


def report(con, cfg, kind, limit):
    if kind == "runtime":
        data, version = read_metadata(cfg, "health/status.json")
        from jobwatch_health import inspect
        return {"generated_at": version.isoformat(), **inspect(data)}
    present = con.execute("SELECT 1 FROM sqlite_master WHERE name='jobwatch_records'").fetchone()
    rows = con.execute("SELECT stream,kind,global_id,disposition,version_at,payload FROM jobwatch_records WHERE kind=? ORDER BY version_at DESC LIMIT ?",
                       (kind, max(1, min(limit, 100)))).fetchall() if present else []
    return {"health": adapter_health(cfg), "records": [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]}
