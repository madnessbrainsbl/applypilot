"""Recruiter Outreach Helper (LinkedIn Sidecar).

Generates targeted LinkedIn search URLs and a short outreach message for the
recruiter and hiring manager after a job submission. The message only uses what
the profile in config.json says about the candidate (name, email, LinkedIn, bio).
"""
from urllib.parse import quote
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
OUTREACH_DIR = ROOT / "autopilot" / "outreach"

def generate_outreach(job, profile=None, bio=""):
    profile = profile or {}
    name = profile.get("full_name") or "the candidate"
    first = profile.get("first_name") or name.split()[0]
    contact = " | ".join(x for x in (profile.get("email"), profile.get("linkedin")) if x)
    company = (job.get("company") or "").strip()
    title = (job.get("title") or "").strip()
    url = job.get("url") or job.get("apply_url") or ""

    # Extract clean company name from ATS URL if missing or defaulted to host
    if not company or any(x in company.lower() for x in ["greenhouse", "lever", "workable", "ashby", "smartrecruiters"]):
        m = re.search(r"(?:greenhouse\.io|lever\.co|workable\.com|ashbyhq\.com)/([^/]+)", url)
        if m:
            company = m.group(1).capitalize()
    if not company:
        company = "the company"
    if not title or title.lower() == "the role":
        title = "the open role"

    background = f"{bio.strip()}\n\n" if bio.strip() else ""
    short_note = (f"Hi! I just applied for the {title} role at {company} and would love to connect. "
                  f"Best, {first}")
    full_message = (
        f"Subject: Application for {title} — {name}\n\n"
        f"Hi [Name],\n\n"
        f"I recently submitted my application for the {title} position at {company} "
        f"and wanted to reach out directly.\n\n"
        f"{background}"
        f"I would welcome the opportunity to discuss how I can contribute to {company}. "
        f"Thank you for your time and consideration!\n\n"
        f"Best regards,\n{name}\n{contact}"
    ).rstrip()

    if len(short_note) > 295:
        short_note = short_note[:292] + "..."

    recruiter_url = f"https://www.linkedin.com/search/results/people/?keywords={quote(company + ' recruiter')}"
    manager_url = f"https://www.linkedin.com/search/results/people/?keywords={quote(company + ' hiring manager')}"

    company_clean = re.sub(r"[^a-z0-9]", "", company.lower()) if company else "company"
    recruiting_email = f"recruiting@{company_clean}.com"
    email_subject = f"Application for {title} — {name}"
    mailto_url = f"mailto:{recruiting_email}?subject={quote(email_subject)}&body={quote(full_message)}"

    data = {
        "job_id": job.get("id"),
        "company": company,
        "title": title,
        "recruiter_search_url": recruiter_url,
        "manager_search_url": manager_url,
        "predicted_email": recruiting_email,
        "mailto_url": mailto_url,
        "connection_invite_note": short_note,
        "inmail_template": full_message
    }

    OUTREACH_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTREACH_DIR / f"{job.get('id', 'temp')}.json"
    out_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data

if __name__ == "__main__":
    res = generate_outreach({"id": "sample123", "company": "Example Co", "title": "Software Engineer"},
                            {"full_name": "Jane Doe", "email": "jane@example.com"})
    print("Recruiter search:", res["recruiter_search_url"])
    print("Invite note:", res["connection_invite_note"])
