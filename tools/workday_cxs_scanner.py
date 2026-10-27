#!/usr/bin/env python3
"""High-Concurrency Keyless Workday CXS REST Directory Scanner.

Scans enterprise Workday career portals directly via their public Careers Experience
Service (CXS) JSON API (POST /wday/cxs/<tenant>/<site>/jobs) in ~300ms without browser overhead.
Applies zero-token suitability scoring before enqueuing to jobs.db.

Strictly zero emojis in logs and messages.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

socket.setdefaulttimeout(10.0)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]

try:
    import db
except ImportError:
    try:
        from tools import db
    except ImportError:
        db = None

try:
    from suitability_scorer import score_vacancy, persona_title_fit
except ImportError:
    from tools.suitability_scorer import score_vacancy, persona_title_fit

# Curated registry of enterprise Workday portals with targeted persona searches
WORKDAY_DIRECTORY: list[dict[str, Any]] = [
    {
        "company": "CrowdStrike",
        "tenant": "crowdstrike",
        "instance": "wd5",
        "site": "crowdstrikecareers",
        "primary_persona": "appsec",
        "search_queries": ["security", "cloud", "engineer"],
    },
    {
        "company": "Adobe",
        "tenant": "adobe",
        "instance": "wd5",
        "site": "external_experienced",
        "primary_persona": "creative",
        "search_queries": ["designer", "creative", "visual"],
    },
    {
        "company": "Salesforce",
        "tenant": "salesforce",
        "instance": "wd12",
        "site": "External_Career_Site",
        "primary_persona": "appsec",
        "search_queries": ["security", "appsec"],
    },
    {
        "company": "Nvidia",
        "tenant": "nvidia",
        "instance": "wd5",
        "site": "NVIDIAExternalCareerSite",
        "primary_persona": "appsec",
        "search_queries": ["security", "infrastructure"],
    },
    {
        "company": "Autodesk",
        "tenant": "autodesk",
        "instance": "wd1",
        "site": "Ext",
        "primary_persona": "creative",
        "search_queries": ["designer", "ux", "visual"],
    },
    {
        "company": "Snowflake",
        "tenant": "snowflake",
        "instance": "wd1",
        "site": "Snowflake_Careers",
        "primary_persona": "appsec",
        "search_queries": ["security", "infrastructure"],
    },
    {
        "company": "Datadog",
        "tenant": "datadog",
        "instance": "wd5",
        "site": "Datadog_Careers",
        "primary_persona": "appsec",
        "search_queries": ["security", "designer"],
    },
    {
        "company": "Target",
        "tenant": "target",
        "instance": "wd5",
        "site": "targetcareers",
        "primary_persona": "creative",
        "search_queries": ["designer", "security"],
    },
    {
        "company": "Walmart Tech",
        "tenant": "walmart",
        "instance": "wd5",
        "site": "WalmartExternal",
        "primary_persona": "appsec",
        "search_queries": ["security", "cyber"],
    },
    {
        "company": "Cisco",
        "tenant": "cisco",
        "instance": "wd5",
        "site": "Cisco_Careers",
        "primary_persona": "appsec",
        "search_queries": ["security", "infosec"],
    },
    {
        "company": "Electronic Arts",
        "tenant": "ea",
        "instance": "wd5",
        "site": "EA_Careers",
        "primary_persona": "creative",
        "search_queries": ["designer", "artist", "ui"],
    },
    {
        "company": "Workday",
        "tenant": "workday",
        "instance": "wd5",
        "site": "Workday",
        "primary_persona": "appsec",
        "search_queries": ["security", "designer"],
    },
    {
        "company": "PayPal",
        "tenant": "paypal",
        "instance": "wd1",
        "site": "PayPal_Careers",
        "primary_persona": "appsec",
        "search_queries": ["security", "designer"],
    },
    {
        "company": "Intuit",
        "tenant": "intuit",
        "instance": "wd5",
        "site": "Intuit_Careers",
        "primary_persona": "appsec",
        "search_queries": ["security", "designer"],
    },
    {
        "company": "HP",
        "tenant": "hp",
        "instance": "wd5",
        "site": "ExternalCareerSite",
        "primary_persona": "creative",
        "search_queries": ["designer", "security"],
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}


def fetch_workday_cxs_jobs(tenant: str, instance: str, site: str, limit: int = 20, offset: int = 0, search_text: str = "") -> list[dict[str, Any]]:
    """Query Workday CXS JSON API."""
    url = f"https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    payload = json.dumps({
        "appliedFacets": {},
        "limit": limit,
        "offset": offset,
        "searchText": search_text,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            **HEADERS,
            "Origin": f"https://{tenant}.{instance}.myworkdayjobs.com",
            "Referer": f"https://{tenant}.{instance}.myworkdayjobs.com/{site}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return data.get("jobPostings", [])
    except Exception as e:
        return []


def scan_workday_portal(portal: dict[str, Any], limit_per_tenant: int = 20) -> list[dict[str, Any]]:
    """Scan and parse job postings for a given Workday portal using targeted queries."""
    comp = portal["company"]
    tenant = portal["tenant"]
    instance = portal["instance"]
    site = portal["site"]
    primary_persona = portal["primary_persona"]
    queries = portal.get("search_queries") or [""]

    seen_ids = set()
    parsed = []

    for q in queries:
        raw_jobs = fetch_workday_cxs_jobs(tenant, instance, site, limit=limit_per_tenant, search_text=q)
        for item in raw_jobs:
            title = item.get("title", "").strip()
            external_path = item.get("externalPath", "")
            bullet = item.get("bulletFields", [""])[0] if item.get("bulletFields") else ""
            raw_id = bullet if bullet else external_path.strip("/").split("/")[-1]
            clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", raw_id)
            job_id = f"workday_{tenant}_{clean_id}"
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            url = f"https://{tenant}.{instance}.myworkdayjobs.com/en-US/{site}{external_path}"
            locations_text = item.get("locationsText", "")
            posted_on = item.get("postedOn", "")

            parsed.append({
                "id": job_id,
                "source": "workday_cxs",
                "company": comp,
                "title": title,
                "location": locations_text,
                "url": url,
                "description": f"{title} at {comp}. Location: {locations_text}. Posted: {posted_on}.",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "primary_persona": primary_persona,
            })

    return parsed


def run_workday_scanner(limit_per_tenant: int = 20, dry_run: bool = True) -> dict[str, Any]:
    """Execute full directory scan across Workday tenants with suitability scoring."""
    t0 = time.time()
    all_found: list[dict[str, Any]] = []
    qualified_jobs: list[dict[str, Any]] = []

    print("==================================================")
    print(f"  WORKDAY CXS REST SCANNER: {'DRY-RUN' if dry_run else 'LIVE INGESTION'}")
    print(f"  Portals configured: {len(WORKDAY_DIRECTORY)}")
    print("==================================================")

    for portal in WORKDAY_DIRECTORY:
        comp = portal["company"]
        jobs = scan_workday_portal(portal, limit_per_tenant=limit_per_tenant)
        all_found.extend(jobs)
        print(f"  [{comp}] Scanned: {len(jobs)} postings")

    # Score and filter
    for j in all_found:
        # primary_persona is only the search hint; the title decides (autopilot.title_fit gate)
        persona = persona_title_fit(j["title"])
        if not persona:
            continue
        verdict = score_vacancy(j["title"], j["description"], persona=persona)
        if verdict["eligible"] and verdict["score"] >= 55:
            j["score"] = verdict["score"]
            j["persona"] = persona
            j["status"] = "queued"
            j["keyword"] = "workday_cxs"
            j["cv"] = f"autopilot/resumes/{persona}.pdf"
            qualified_jobs.append(j)

    elapsed = time.time() - t0
    print("\n--------------------------------------------------")
    print(f"  Total raw vacancies scanned: {len(all_found)}")
    print(f"  Qualified (Score >= 55, zero clearances): {len(qualified_jobs)}")
    print(f"  Time elapsed: {elapsed:.2f}s")
    print("--------------------------------------------------")

    if not dry_run and db:
        con = db.connect()
        inserted_count = 0
        for qj in qualified_jobs:
            if not db.job_exists(con, qj["id"]):
                db.upsert_job(con, qj, commit=False)
                inserted_count += 1
        con.commit()
        con.close()
        print(f"  Successfully inserted {inserted_count} new qualified jobs into jobs.db")

    return {
        "scanned_total": len(all_found),
        "qualified_total": len(qualified_jobs),
        "elapsed_seconds": round(elapsed, 2),
        "dry_run": dry_run,
    }


def main():
    parser = argparse.ArgumentParser(description="Workday CXS REST Directory Scanner")
    parser.add_argument("--limit", type=int, default=20, help="Max postings per portal (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without saving to jobs.db")
    parser.add_argument("--save", action="store_true", help="Explicit save flag (default behavior)")
    args = parser.parse_args()

    run_workday_scanner(limit_per_tenant=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
