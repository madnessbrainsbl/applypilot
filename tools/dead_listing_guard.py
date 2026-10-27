"""High-Speed Dead Listing Pruning & Expiry Guard.
Probes queued job URLs in jobs.db using concurrent HTTP HEAD/GET requests
to detect expired, filled, or deleted job postings (HTTP 404, 410, redirect to root).
Marks dead listings in jobs.db with dead_listing = 1 and status = 'dead_listing'
to prevent Playwright workers from wasting browser time on defunct vacancies.
Zero external API tokens, $0 budget.
"""
import concurrent.futures
import re
import sys
import time
import urllib.request
import urllib.error
from typing import Dict, Any, List, Tuple
from pathlib import Path

import db

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

_DEAD_PATTERNS = [
    re.compile(r"this\s+(?:job|posting|position|role)\s+is\s+no\s+longer\s+available", re.I),
    re.compile(r"this\s+job\s+has\s+expired", re.I),
    re.compile(r"no\s+longer\s+accepting\s+applications", re.I),
    re.compile(r"the\s+job\s+you\s+are\s+looking\s+for\s+has\s+been\s+filled", re.I),
    re.compile(r"page\s+not\s+found", re.I),
    re.compile(r"job\s+not\s+found", re.I),
    re.compile(r"404\s+not\s+found", re.I),
]


def check_url_liveness(job_id: str, url: str, timeout: int = 4) -> Tuple[str, bool, str]:
    """Check if job listing URL is still active. Returns (job_id, is_dead, reason)."""
    clean_url = (url or "").strip()
    if not clean_url or not clean_url.startswith("http"):
        return (job_id, True, "Invalid URL schema")

    req = urllib.request.Request(
        clean_url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Range": "bytes=0-4096"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            status = resp.getcode()

            # Check redirect to home or careers root
            if final_url.rstrip("/") in [
                "https://boards.greenhouse.io",
                "https://jobs.lever.co",
                "https://jobs.ashbyhq.com",
            ] or (len(final_url.split("/")) <= 4 and len(clean_url.split("/")) > 4):
                return (job_id, True, f"Redirected to careers root: {final_url}")

            # Read snippet of body to check dead markers
            snippet = resp.read(4096).decode("utf-8", "ignore")
            for pat in _DEAD_PATTERNS:
                if pat.search(snippet):
                    return (job_id, True, f"Dead listing marker in body: '{pat.pattern}'")

            return (job_id, False, f"Active (HTTP {status})")

    except urllib.error.HTTPError as e:
        if e.code in [404, 410]:
            return (job_id, True, f"HTTP Error {e.code}: Gone/Not Found")
        # 403/429/500 might be anti-bot or temporary server glitch - do not mark dead
        return (job_id, False, f"Temporary HTTP {e.code} (Kept)")
    except urllib.error.URLError as e:
        if "getaddrinfo failed" in str(e) or "Name or service not known" in str(e):
            return (job_id, True, f"Domain resolution failed: {e.reason}")
        return (job_id, False, f"Network timeout/glitch: {e.reason}")
    except Exception as e:
        return (job_id, False, f"Unknown error: {str(e)[:50]}")


def prune_dead_listings(db_path: str = None, batch_size: int = 150, max_workers: int = 25) -> Dict[str, int]:
    """Audit queued jobs in jobs.db and prune closed/defunct listings."""
    con_path = db_path or (Path(__file__).resolve().parents[1] / "autopilot" / "jobs.db")
    con = db.connect() if db_path is None else db.sqlite3.connect(con_path, timeout=30)  # WAL + busy_timeout
    cur = con.cursor()

    cur.execute(
        "SELECT id, url, company, title FROM jobs WHERE status = 'queued' AND dead_listing = 0 ORDER BY id ASC LIMIT ?",
        (batch_size,)
    )
    rows = cur.fetchall()

    if not rows:
        print("No active queued jobs to check for expiry.")
        con.close()
        return {"checked": 0, "pruned": 0}

    print(f"Checking liveness of {len(rows)} queued listings via {max_workers} concurrent threads...", flush=True)

    dead_ids = []
    active_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(check_url_liveness, r[0], r[1]): r for r in rows
        }
        for fut in concurrent.futures.as_completed(futures):
            job_info = futures[fut]
            try:
                jid, is_dead, reason = fut.result()
                if is_dead:
                    dead_ids.append((jid, reason))
                    print(f"  [DEAD LISTING] {job_info[2]} - {job_info[3][:35]} -> {reason}")
                else:
                    active_count += 1
            except Exception:
                pass

    if dead_ids:
        cur.executemany(
            "UPDATE jobs SET dead_listing = 1, status = 'dead_listing' WHERE id = ?",
            [(d[0],) for d in dead_ids]
        )
        con.commit()

    con.close()

    print("============================================================")
    print("  Dead Listing Pruning Summary:")
    print(f"  Total Checked:    {len(rows)}")
    print(f"  Active Verified:  {active_count}")
    print(f"  Pruned as Dead:   {len(dead_ids)}")
    print("============================================================")
    return {"checked": len(rows), "active": active_count, "pruned": len(dead_ids)}


if __name__ == "__main__":
    b_size = 150
    for arg in sys.argv:
        if arg.startswith("--batch="):
            b_size = int(arg.split("=")[1])
    prune_dead_listings(batch_size=b_size)
