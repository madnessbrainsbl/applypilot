"""Use FindAll's public collectors without touching its databases or contact outputs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlparse


def structured_posting(url, title, careers):
    host = urlparse(url).hostname
    transport = careers._Transport(time.monotonic() + 15, 1_000_000, None)
    response = transport.fetch(url, {host})
    if response.status != 200 or response.error:
        return None
    parser = careers._Page()
    parser.feed(response.text)
    for block in parser.json_blocks:
        try:
            stack = [json.loads(block)]
        except ValueError:
            continue
        visited = 0
        while stack and visited < 300:
            node = stack.pop()
            visited += 1
            if isinstance(node, list):
                stack.extend(node[:100])
            elif isinstance(node, dict):
                if (node.get("@type") == "JobPosting" or "JobPosting" in (node.get("@type") or [])) and node.get("title") == title:
                    expiry = node.get("validThrough")
                    if expiry:
                        try:
                            until = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                            until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until.astimezone(timezone.utc)
                            if until < datetime.now(timezone.utc):
                                continue
                        except (ValueError, TypeError):
                            continue
                    locations = node.get("jobLocation") or []
                    locations = locations if isinstance(locations, list) else [locations]
                    names = []
                    for loc in locations:
                        address = loc.get("address") or {} if isinstance(loc, dict) else {}
                        if isinstance(address, dict):
                            country = address.get("addressCountry") or ""
                            if isinstance(country, dict):
                                country = country.get("name") or ""
                            names.append(", ".join(str(x) for x in [address.get("addressLocality"), address.get("addressRegion"), country] if x))
                    organisation = node.get("hiringOrganization") or {}
                    organisation = organisation if isinstance(organisation, dict) else {}
                    if not isinstance(node.get("description"), str):
                        continue
                    return {"title": title, "company": organisation.get("name") or host,
                            "url": response.url, "description": str(node.get("description") or ""),
                            "location": "; ".join(names).strip(" ,;"), "primary_verified": True,
                            "source": "FindAll company JobPosting", "observed_at": datetime.now(timezone.utc).isoformat()}
                stack.extend(x for x in node.values() if isinstance(x, (dict, list)))
    return None


def collect(root, queries, seeds, limit):
    sys.path.insert(0, str(root))
    from jf import careers, osint
    statuses, jobs, links = [], [], list(seeds)
    for query in queries:
        rows, errors, source_status = osint.search(query, ["bing", "brave", "yahoo"], limit=min(limit, 15))
        links.extend(rows)
        statuses.extend(source_status)
        statuses.extend({"source": "FindAll search", "state": "error", "error": str(error)[:200]} for error in errors)
    database = root.parent / "jobfind.db"
    if database.is_file() and queries:
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            jobs.extend({**dict(row), "source": "FindAll saved vacancy"} for row in con.execute(
                "SELECT title,company,location,url,description FROM jobs WHERE lower(title) LIKE '%security%' OR lower(title) LIKE '%appsec%' OR lower(title) LIKE '%cyber%'"))
    seen, crawled = set(), 0
    for lead in links:
        url = lead.get("url") or ""
        host = urlparse(url).hostname or ""
        if not url or url in seen:
            continue
        seen.add(url)
        identity = careers.ats_identity(url)
        if identity:
            parts = [x for x in urlparse(url).path.split("/") if x]
            is_posting = (identity[0] == "greenhouse" and "/jobs/" in url or
                          identity[0] in ("ashby", "lever", "smartrecruiters") and len(parts) >= 2 or
                          identity[0] == "workable" and "/j/" in url)
            if is_posting:
                jobs.append({"url": url, "title": lead.get("title") or "", "company": "", "description": lead.get("snippet") or "", "source": "FindAll ATS search"})
            else:
                try:
                    jobs.extend(careers.sources.SOURCE_ATS[identity[0]](identity[1]))
                except (ValueError, OSError) as exc:
                    statuses.append({"source": "FindAll ATS board", "state": "error", "host": host, "error": type(exc).__name__})
            continue
        # Public social posts are evidence leads, never a substitute for an application URL.
        if host.endswith(("linkedin.com", "facebook.com", "twitter.com", "x.com", "t.me")) or crawled >= 8:
            continue
        try:
            result = careers.collect(url, max_pages=4, max_scripts=1, max_sitemaps=1,
                                     max_boards=2, max_jobs=100, time_limit=20)
            crawled += 1
            statuses.extend(result.get("statuses") or [])
            for job in result.get("jobs") or []:
                if job.get("source") == "Company JobPosting":
                    primary = structured_posting(job["url"], job["title"], careers)
                    if primary:
                        jobs.append(primary)
                else:
                    jobs.append(job)
        except (ValueError, OSError) as exc:
            statuses.append({"source": "FindAll careers", "state": "error", "host": host, "error": type(exc).__name__})
    return {"jobs": jobs, "links": links, "statuses": statuses,
            "observed_at": datetime.now(timezone.utc).isoformat(), "company_pages_crawled": crawled}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--seeds", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or len(args.query) > 8 or not (args.root / "jf/careers.py").is_file():
        raise SystemExit("Invalid FindAll bridge arguments")
    seeds = json.loads(args.seeds.read_text(encoding="utf-8")) if args.seeds else []
    result = collect(args.root, args.query, seeds, args.limit)
    print(json.dumps(result, ensure_ascii=False))
