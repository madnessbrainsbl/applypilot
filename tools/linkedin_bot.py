"""LinkedIn job discovery (search only).

Inspired by top open-source LinkedIn automation tools (EasyApplyBot,
LinkedIn-Easy-Apply-Bot, and Voyager API clients). Fully integrated with
the bot_otclick autopilot ecosystem (jobs.db, Playwright stealth session,
candidate profile, and QA answer matching).
"""
import html
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urljoin, parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
LI_STATE = ROOT / "autopilot/linkedin-state.json"
BROWSER_STATE = ROOT / "autopilot/browser-state.json"
LI_CACHE = ROOT / "autopilot/linkedin-apply.json"
SHOTS = ROOT / "autopilot/screenshots"

try:
    import db
except ImportError:
    try:
        from tools import db
    except ImportError:
        db = None


def _qa_key(label, options=(), company="", persona="appsec"):
    label = re.sub(r"\([^)]*\)", "", label or "")
    key = re.sub(r"\s+", " ", label.lower().replace("*", "").strip())
    if persona and persona != "appsec":
        key = f"{persona}@" + key
    normalized_options = sorted(
        re.sub(r"\s+", " ", str(option).lower().strip()) for option in (options or ()) if str(option).strip()
    )
    return key if not normalized_options else key + " | options: " + " | ".join(normalized_options)


def get_linkedin_cookies():
    """Retrieve LinkedIn session cookies from LI_STATE or BROWSER_STATE."""
    for state_file in [LI_STATE, BROWSER_STATE]:
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                cookies = data.get("cookies", [])
                li_cookies = [c for c in cookies if "linkedin.com" in c.get("domain", "")]
                if li_cookies:
                    cookie_dict = {c["name"]: c["value"] for c in li_cookies}
                    return cookie_dict, li_cookies
            except Exception:
                pass
    return {}, []


def is_authenticated():
    """Check if we have an active LinkedIn session with li_at cookie."""
    cookies, _ = get_linkedin_cookies()
    return "li_at" in cookies


def extract_job_id(url_or_str):
    """Extract numeric LinkedIn job ID from URL or string."""
    m = re.search(r"(?:/jobs/view/(?:[^/?]*-)?|currentJobId=)(\d{6,})", str(url_or_str or ""))
    if m:
        return m.group(1)
    m2 = re.search(r"\b(\d{8,12})\b", str(url_or_str or ""))
    return m2.group(1) if m2 else ""


# ---------------------------------------------------------------------------
# LinkedIn Job Discovery (Voyager API + Scraper)
# ---------------------------------------------------------------------------

def linkedin_source(keyword, limit=25, cfg=None):
    """Discovery source for autopilot: finds LinkedIn Easy Apply & standard jobs."""
    cfg = cfg or {}
    cookie_dict, _ = get_linkedin_cookies()
    jobs = []

    js_cfg = cfg.get("jobspy", {})
    locations = js_cfg.get("locations") or ["Remote", "London, UK", "Berlin, Germany"]
    if isinstance(locations, str):
        locations = [locations]

    easy_apply_only = False
    remote_only = js_cfg.get("is_remote")

    for loc in locations[:3]:
        found = search_linkedin_voyager(
            keyword=keyword,
            location=loc,
            limit=limit,
            easy_apply_only=easy_apply_only,
            remote=remote_only,
            cookie_dict=cookie_dict,
        )
        if not found:
            # Fallback to HTML guest search if Voyager is unavailable or unauthed
            found = search_linkedin_guest(keyword=keyword, location=loc, limit=limit, easy_apply_only=easy_apply_only)
        jobs.extend(found)
        if len(jobs) >= limit:
            break

    return jobs[:limit]


def search_linkedin_voyager(keyword, location="Europe", limit=25, easy_apply_only=True, remote=None, cookie_dict=None):
    """Query LinkedIn Voyager API for job postings using authenticated cookies."""
    if not cookie_dict:
        cookie_dict, _ = get_linkedin_cookies()
    if not cookie_dict.get("li_at"):
        return []

    try:
        from curl_cffi import requests as creq
    except ImportError:
        return []

    csrf = cookie_dict.get("JSESSIONID", "").strip('"')
    headers = {
        "csrf-token": csrf,
        "x-restli-protocol-version": "2.0.0",
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }

    url = "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards"
    params = {
        "decorationId": "com.linkedin.voyager.dash.deco.jobs.search.JobSearchCardsCollection-220",
        "count": str(min(limit, 25)),
        "q": "jobSearch",
        "query": f"(origin:JOB_SEARCH_PAGE_SEARCH_BUTTON,keywords:{keyword},locationUnion:(normUnion:(text:{location})))",
    }

    try:
        r = creq.get(
            url,
            params=params,
            headers=headers,
            cookies=cookie_dict,
            impersonate="chrome",
            timeout=15,
        )
        if r.status_code != 200:
            return []

        data = r.json()
        included = data.get("included", [])
        jobs = []

        for item in included:
            entity_urn = item.get("entityUrn", "")
            if "jobPosting" not in entity_urn and "fsd_jobPosting" not in entity_urn:
                continue
            jid = extract_job_id(entity_urn)
            if not jid:
                continue

            title = item.get("title") or ""
            company = ""
            comp_obj = item.get("primaryDescription", {})
            if isinstance(comp_obj, dict):
                company = comp_obj.get("text") or ""
            elif isinstance(comp_obj, str):
                company = comp_obj

            loc_str = ""
            loc_obj = item.get("secondaryDescription", {})
            if isinstance(loc_obj, dict):
                loc_str = loc_obj.get("text") or location
            elif isinstance(loc_obj, str):
                loc_str = loc_obj

            # Check Easy Apply
            easy_apply = bool(item.get("applyMethod", {}).get("easyApplyUrl") or
                              "ComplexOnsiteApply" in str(item.get("applyMethod", {})))

            if easy_apply_only and not easy_apply:
                continue

            jobs.append({
                "id": jid,
                "title": title,
                "company": company.strip(),
                "location": loc_str.strip(),
                "url": f"https://www.linkedin.com/jobs/view/{jid}/",
                "easy_apply": easy_apply,
                "description": title + " at " + company,
                "source": "linkedin",
            })
        return jobs
    except Exception as e:
        print(f"linkedin voyager search error: {e}", file=sys.stderr)
        return []


def search_linkedin_guest(keyword, location="Europe", limit=25, easy_apply_only=True):
    """Guest/public HTML scraping fallback for LinkedIn job listings."""
    import urllib.request
    jobs = []
    kw_q = quote(keyword)
    loc_q = quote(location)
    f_al = "&f_AL=true" if easy_apply_only else ""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={kw_q}&location={loc_q}{f_al}&start=0"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read().decode("utf-8", "ignore")

        # Extract job cards
        for card_match in re.finditer(r'<li[^>]*>(.*?)</li>', content, re.DOTALL):
            card = card_match.group(1)
            link_m = re.search(r'href="(https://[a-z]+\.linkedin\.com/jobs/view/[^"]+)"', card)
            if not link_m:
                continue
            job_url = link_m.group(1).split("?")[0]
            jid = extract_job_id(job_url)

            title_m = re.search(r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>\s*(.*?)\s*</h3>', card, re.DOTALL)
            title = html.unescape(title_m.group(1)).strip() if title_m else ""

            comp_m = re.search(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*<a[^>]*>\s*(.*?)\s*</a>\s*</h4>', card, re.DOTALL)
            if not comp_m:
                comp_m = re.search(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*(.*?)\s*</h4>', card, re.DOTALL)
            company = html.unescape(comp_m.group(1)).strip() if comp_m else ""

            loc_m = re.search(r'<span[^>]*class="[^"]*job-search-card__location[^"]*"[^>]*>\s*(.*?)\s*</span>', card, re.DOTALL)
            loc = html.unescape(loc_m.group(1)).strip() if loc_m else location

            if jid and title:
                jobs.append({
                    "id": jid,
                    "title": title,
                    "company": company,
                    "location": loc,
                    "url": job_url,
                    "easy_apply": bool(easy_apply_only),
                    "description": f"{title} at {company}",
                    "source": "linkedin",
                })
            if len(jobs) >= limit:
                break
    except Exception as e:
        print(f"linkedin guest search: {e}", file=sys.stderr)

    return jobs


# ---------------------------------------------------------------------------
# LinkedIn Easy Apply Automation
# ---------------------------------------------------------------------------
