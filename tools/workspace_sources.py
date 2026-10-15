"""Read vacancy leads from the user's FindAll and PoiskContact projects."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import medical_search


def run_json(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=360, check=True)
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Workspace vacancy source returned malformed JSON")
    return data


def discover(kind, cfg, limit, fetch_text, role_fit, archive):
    options = cfg.get("search", {}).get("workspace_sources") or {}
    findall = Path(options.get("findall_root") or "")
    python = findall / ".venv/Scripts/python.exe"
    if not (findall / "jf/careers.py").is_file() or not python.is_file():
        raise FileNotFoundError("Configured FindAll project or its Python runtime is unavailable")
    queries = options.get("queries") or ['healthtech "application security" careers', 'medtech cybersecurity hiring']
    bridge = Path(__file__).with_name("findall_vacancy_bridge.py")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive.mkdir(parents=True, exist_ok=True)
    command = [str(python), "-X", "utf8", str(bridge), "--root", str(findall), "--limit", str(min(limit, 30))]
    if kind == "workspace_poisk":
        executable = Path(options.get("poisk_cli") or "")
        if not executable.is_file():
            raise FileNotFoundError("PoiskContact read-only vacancy binary is unavailable")
        native_command = [str(executable), "--limit", str(min(limit, 30))]
        for query in queries:
            native_command.extend(["--query", query])
        cache = archive / "poisk-vacancy-cache.json"
        cached = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
        used_cache = cached.get("queries") == queries and time.time() - cached.get("captured_at", 0) < 3600
        data = cached["data"] if used_cache else run_json(native_command, executable.parent)
        if not used_cache:
            cache.write_text(json.dumps({"queries": queries, "captured_at": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
        data["native_search_cached"] = used_cache
        native_file = archive / f"poisk-vacancy-leads-{stamp}.json"
        native_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        seeds = archive / f"poisk-vacancy-links-{stamp}.json"
        seeds.write_text(json.dumps(data.get("links") or [], ensure_ascii=False), encoding="utf-8")
        enriched = run_json([*command, "--seeds", str(seeds)], findall)
        data["jobs"].extend(enriched["jobs"])
        data.setdefault("statuses", []).extend(enriched.get("statuses") or [])
    else:
        for query in queries:
            command.extend(["--query", query])
        data = run_json(command, findall)
    evidence = archive / f"{kind}-{stamp}.json"
    data["discovered_via"] = kind
    evidence.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for link in data.get("links") or []:
        host = urlparse(link.get("url") or "").hostname or ""
        if host.endswith(".myworkdayjobs.com") or host == "jobs.gem.com":
            data["jobs"].append({"url": link["url"], "title": "", "company": "", "description": ""})
    jobs, seen, errors, candidates = [], set(), [], []
    for item in data["jobs"]:
        url = item.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        if item.get("title") and not role_fit(item):
            continue
        if item.get("primary_verified") and role_fit(item):
            jobs.append({**item, "source": "vendor", "discovered_via": kind})
            continue
        candidates.append(item)
    def verify(item):
        try:
            primary = medical_search.primary_posting(item, fetch_text)
            if primary and role_fit(primary):
                return {**primary, "discovered_via": kind}
        except Exception as exc:
            errors.append({"source": kind, "url": item["url"], "error": type(exc).__name__})
        return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs.extend(job for job in pool.map(verify, candidates) if job)
    summary = {"raw_leads": len(data["jobs"]), "verified_security_postings": len(jobs),
               "search_links": len(data.get("links") or []), "statuses": data.get("statuses") or [],
               "errors": errors, "evidence": str(evidence), "native_search_cached": data.get("native_search_cached", False)}
    return jobs, summary
