"""Read current public medical career boards; never submit candidate data."""
import json
import re
import urllib.request
from urllib.parse import urlparse


class PartialBoardError(ValueError):
    def __init__(self, message, jobs):
        super().__init__(message)
        self.jobs = jobs


def _post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.loads(response.read())
    if data.get("errors"):
        raise ValueError("Public career API returned errors: " + str(data["errors"])[:200])
    return data


def smartrecruiters_board(url, fetch_text, role_fit):
    parsed = urlparse(url)
    if parsed.netloc != "api.smartrecruiters.com" or not re.fullmatch(r"/v1/companies/[\w-]+/postings", parsed.path):
        raise ValueError("Expected a public SmartRecruiters company endpoint")
    base = "https://" + parsed.netloc + parsed.path
    company = parsed.path.split("/")[3]
    jobs, offset = [], 0
    while True:
        data = json.loads(fetch_text(f"{base}?destination=PUBLIC&limit=100&offset={offset}", timeout=20))
        items = data.get("content")
        if not isinstance(items, list) or "totalFound" not in data:
            raise ValueError("Malformed public SmartRecruiters list response")
        if not items:
            if offset < int(data["totalFound"]):
                raise ValueError("Incomplete SmartRecruiters pagination")
            break
        for item in items:
            posting_id = str(item.get("id") or "")
            if not posting_id.isdigit():
                raise ValueError("Invalid SmartRecruiters posting identity")
            location = item.get("location") or {}
            job = {"title": item.get("name") or "", "company": (item.get("company") or {}).get("name") or company,
                   "location": ", ".join(filter(None, [location.get("city"), location.get("country")])),
                   "url": f"https://jobs.smartrecruiters.com/{company}/{posting_id}", "description": ""}
            if role_fit(job):
                detail = json.loads(fetch_text(f"{base}/{posting_id}?destination=PUBLIC", timeout=20))
                sections = (detail.get("jobAd") or {}).get("sections") or {}
                job["description"] = "\n".join(v.get("text") or "" for v in sections.values() if isinstance(v, dict))
                if not job["description"]:
                    raise ValueError("SmartRecruiters security posting has no primary description")
            jobs.append(job)
        offset += len(items)
        if offset >= int(data["totalFound"]):
            break
        if offset > 2000:
            raise PartialBoardError("SmartRecruiters board exceeded the bounded 2000-posting scan", jobs)
    return jobs


def workday_board(url, fetch_text, role_fit):
    parsed = urlparse(url)
    match = re.fullmatch(r"/wday/cxs/([\w-]+)/([\w-]+)/jobs", parsed.path)
    if not match or not re.fullmatch(r"[\w-]+\.wd\d+\.myworkdayjobs\.com", parsed.netloc):
        raise ValueError("Expected a public Workday CXS jobs endpoint")
    tenant, site = match.groups()
    origin = "https://" + parsed.netloc
    seen, jobs = set(), []
    for query in ("security", "cyber", "devsecops", "identity"):
        offset = 0
        while True:
            data = _post_json(url, {"limit": 20, "offset": offset, "searchText": query, "appliedFacets": {}})
            postings = data.get("jobPostings")
            if not isinstance(postings, list) or "total" not in data:
                raise ValueError("Malformed Workday listing response")
            if not postings:
                if offset < int(data["total"]):
                    raise ValueError("Workday pagination ended before its reported total")
                break
            for item in postings:
                path = item.get("externalPath") or ""
                if not path.startswith("/job/") or ".." in path:
                    raise ValueError("Invalid public Workday posting path")
                if path in seen:
                    continue
                seen.add(path)
                job = {"title": item.get("title") or "", "company": tenant,
                       "location": item.get("locationsText") or "",
                       "url": f"{origin}/en-US/{site}{path}", "description": ""}
                if role_fit(job):
                    detail = json.loads(fetch_text(f"{origin}/wday/cxs/{tenant}/{site}{path}", timeout=20))
                    info = detail.get("jobPostingInfo") or {}
                    if not info.get("jobDescription"):
                        raise ValueError("Workday security posting has no primary description")
                    job["description"] = info["jobDescription"]
                    if isinstance(info.get("location"), str) and info["location"]:
                        job["location"] = "; ".join([info["location"], *(x for x in info.get("additionalLocations") or [] if isinstance(x, str))])
                jobs.append(job)
            offset += len(postings)
            if offset >= int(data["total"]):
                break
            if offset > 2000:
                raise ValueError("Workday query exceeded the bounded 2000-posting scan")
    return jobs


def gem_board(url, role_fit):
    parsed = urlparse(url)
    if parsed.netloc != "jobs.gem.com" or not re.fullmatch(r"/[\w-]+/?", parsed.path):
        raise ValueError("Expected a public Gem job board")
    board = parsed.path.strip("/")
    endpoint = "https://jobs.gem.com/api/public/graphql"
    query = """query JobBoardList($boardId:String!) {
      oatsExternalJobPostings(boardId:$boardId) { jobPostings {
        extId title locations { name city isoCountry isRemote }
      }} jobBoardExternal(vanityUrlPath:$boardId) { teamDisplayName }
    }"""
    data = _post_json(endpoint, {"query": query, "variables": {"boardId": board}}).get("data") or {}
    postings = (data.get("oatsExternalJobPostings") or {}).get("jobPostings")
    if not isinstance(postings, list):
        raise ValueError("Malformed public Gem board response")
    company = (data.get("jobBoardExternal") or {}).get("teamDisplayName") or board
    jobs = []
    for item in postings:
        ext_id = item.get("extId") or ""
        if not re.fullmatch(r"[\w-]+", ext_id):
            raise ValueError("Invalid public Gem posting identity")
        job = {"title": item.get("title") or "", "company": company,
               "location": "; ".join(x.get("name") or x.get("city") or "" for x in item.get("locations") or []),
               "url": f"https://jobs.gem.com/{board}/{ext_id}", "description": ""}
        if role_fit(job):
            query = """query ExternalJobPosting($boardId:String!,$extId:String!) {
              oatsExternalJobPosting(boardId:$boardId,extId:$extId) {
                descriptionHtml jobPostSectionHtml { introHtml outroHtml }
              }
            }"""
            detail = _post_json(endpoint, {"query": query, "variables": {"boardId": board, "extId": ext_id}})
            info = (detail.get("data") or {}).get("oatsExternalJobPosting") or {}
            sections = info.get("jobPostSectionHtml") or {}
            job["description"] = "\n".join(filter(None, [sections.get("introHtml"), info.get("descriptionHtml"), sections.get("outroHtml")]))
            if not job["description"]:
                raise ValueError("Gem security posting has no primary description")
        jobs.append(job)
    return jobs


def uhn_board(url, fetch_text, role_fit):
    if url != "https://forms.uhn.ca/UHNCareers":
        raise ValueError("Expected UHN's official careers catalog")
    page = fetch_text(url, timeout=20)
    match = re.search(r'data-url="(/UHNCareers/Home/GetAll[^"<>]*)"', page)
    if not match:
        raise ValueError("UHN catalog no longer exposes its public listing endpoint")
    data = json.loads(fetch_text("https://forms.uhn.ca" + match.group(1), timeout=20))
    if not isinstance(data.get("data"), list):
        raise ValueError("Malformed UHN careers catalog")
    jobs = []
    for item in data["data"]:
        posting_id = str(item.get("id") or "")
        if not posting_id.isdigit():
            raise ValueError("Invalid UHN posting identity")
        job = {"title": item.get("name") or "", "company": "University Health Network",
               "location": item.get("site") or "Toronto, Canada",
               "url": f"https://jobs.smartrecruiters.com/UniversityHealthNetwork/{posting_id}", "description": ""}
        if role_fit(job):
            # The official detail route redirects to SmartRecruiters; access errors must remain errors.
            detail = fetch_text(f"https://forms.uhn.ca/UHNCareers/Home/Posting/{posting_id}", timeout=20)
            matches = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', detail, re.S | re.I)
            for value in matches:
                info = json.loads(value)
                for record in info if isinstance(info, list) else [info]:
                    if record.get("@type") == "JobPosting":
                        job["description"] = record.get("description") or ""
            if not job["description"]:
                raise ValueError("UHN security posting primary description unavailable")
        jobs.append(job)
    return jobs
