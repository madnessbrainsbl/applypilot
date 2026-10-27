#!/usr/bin/env python3
"""Telegram Public Tech & Creative Channels Vacancy & Lead Harvester.

Scrapes public Telegram job channels (via https://t.me/s/{channel}) without requiring
any Telegram bot tokens, API keys, or Telegram accounts.
Extracts:
  - Job Title & Tech/Design Stack
  - Company & Salary
  - Recruiter Telegram handles (@username)
  - HR Contact Emails
  - Direct application links / post URLs

Filters by dual-track personas:
  1. Creative: Design, Photo, Video, Motion, Art Director, Lookbook, Content
  2. Cybersec: AppSec, DevSecOps, Cloud Security, Pentest, SOC, IAM, Infosec

Persists:
  1. Open vacancies into bot_otclick/autopilot/jobs.db (status: queued)
  2. Recruiter & HR leads into poisk_contact/contacts.json
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
POISK_CONTACT_JSON = ROOT.parent / "poisk_contact" / "contacts.json"

try:
    import db
except ImportError:
    try:
        from tools import db
    except ImportError:
        db = None

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
TG_HANDLE_RE = re.compile(r"@([a-zA-Z0-9_]{5,32})")
BAD_HANDLES = {
    "share", "joinchat", "telegram", "channel", "bot", "channel_bot",
    "admin", "support", "media", "info", "news", "official",
}
BAD_EMAIL_PARTS = ("example.", "noreply", "no-reply", "donotreply", ".png", ".jpg", ".webp")

CREATIVE_KEYWORDS = [
    "дизайн", "дизайнер", "designer", "art director", "арт-директор", "creative",
    "видео", "видеограф", "videographer", "video", "фото", "фотограф", "photographer",
    "lookbook", "лукбук", "motion", "моушн", "ui/ux", "ux/ui", "brand", "бренд",
    "контент", "content", "графический", "graphic", "illustrator", "иллюстратор",
    "3d", "cg", "creative lead", "visual"
]

DEFAULT_CHANNELS = [
    # Creative, Design, Media & Content channels
    "designer_jobs",
    "vdhl_good",
    "workinart",
    "norabota",
    "creative_jobs",
    "design_hunters",
    "motionjobs",
    "cgjobs",
    # InfoSec & Cybersecurity channels
    "secjobs",
    "infosec_jobs",
    "cybersec_jobs",
    # Global & Remote Tech channels
    "it_vacancies_world",
    "rust_jobs_feed",
    "remotejobss",
    "relocate_today",
    "remote_abroad",
]


def classify_channel_post(title: str, text: str) -> str:
    combined = f"{title} {text}".lower()
    if any(k in combined for k in CREATIVE_KEYWORDS):
        return "creative"
    return "appsec"


def fetch_channel_messages(channel: str, limit: int = 30) -> list[dict]:
    """Fetch public web feed from https://t.me/s/{channel}."""
    try:
        from curl_cffi import requests
    except ImportError:
        import requests

    channel = channel.lstrip("@").strip()
    url = f"https://t.me/s/{channel}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.9",
    }

    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            print(f"Channel @{channel} returned HTTP {r.status_code}")
            return []
        raw_html = r.text
    except Exception as e:
        print(f"Error fetching channel @{channel}: {e}")
        return []

    post_blocks = re.findall(
        r'<div class="tgme_widget_message\s+js-widget_message"[^>]*data-post="([^"]+)"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        raw_html,
        re.DOTALL,
    )
    if not post_blocks:
        texts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', raw_html, re.DOTALL)
        post_blocks = [(f"{channel}/{i}", t) for i, t in enumerate(texts)]

    results = []
    for post_id, block in reversed(post_blocks):
        if len(results) >= limit:
            break

        text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
        content_html = text_match.group(1) if text_match else block

        clean_text = re.sub(r'<br\s*/?>', '\n', content_html)
        clean_text = re.sub(r'<[^>]+>', ' ', clean_text)
        clean_text = html.unescape(clean_text).strip()

        if len(clean_text) < 40:
            continue

        raw_tgs = TG_HANDLE_RE.findall(clean_text)
        recruiters = [f"@{t}" for t in raw_tgs if t.lower() not in BAD_HANDLES and t.lower() != channel.lower()]

        raw_emails = EMAIL_RE.findall(clean_text)
        emails = [
            e.lower().strip(".-_ \t\r\n")
            for e in raw_emails
            if not any(b in e.lower() for b in BAD_EMAIL_PARTS) and "." in e.split("@")[-1]
        ]

        lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
        first_line = lines[0] if lines else "Вакансия"
        title = first_line[:80].strip(" #*•-")
        if any(h in title.lower() for h in ["вакансия", "vacancy", "ищем", "position"]):
            if len(lines) > 1 and len(lines[1]) > 5:
                title = f"{title}: {lines[1][:60].strip(' #*•-')}"

        salary_match = re.search(r'(\d+[\s\d]*\s*(?:-\s*\d+[\s\d]*)?\s*(?:[$€₽]|usd|eur|rub|руб|тыс))', clean_text, re.IGNORECASE)
        salary = salary_match.group(1).strip() if salary_match else None

        post_url = f"https://t.me/{post_id}" if "/" in post_id else f"https://t.me/{channel}"
        persona = classify_channel_post(title, clean_text)

        results.append({
            "post_id": post_id,
            "channel": channel,
            "post_url": post_url,
            "title": title,
            "text": clean_text,
            "recruiters": list(dict.fromkeys(recruiters)),
            "emails": list(dict.fromkeys(emails)),
            "salary": salary,
            "persona": persona,
        })

    return results


def save_leads_to_poisk_contact(jobs: list[dict]):
    """Save recruiter handles and emails into poisk_contact/contacts.json."""
    existing = []
    if POISK_CONTACT_JSON.is_file():
        try:
            existing = json.loads(POISK_CONTACT_JSON.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    existing_emails = {r.get("email", "").lower() for r in existing if r.get("email")}
    existing_tgs = {r.get("telegram", "").lower() for r in existing if r.get("telegram")}

    added = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for j in jobs:
        emails = j.get("emails", [])
        recruiters = j.get("recruiters", [])
        channel = j.get("channel", "telegram")
        title = j.get("title", "Вакансия")
        post_url = j.get("post_url", "")
        persona = j.get("persona", "appsec")

        primary_email = emails[0] if emails else ""
        primary_tg = recruiters[0] if recruiters else ""

        if not primary_email and not primary_tg:
            continue

        if primary_email and primary_email in existing_emails:
            continue
        if primary_tg and primary_tg.lower() in existing_tgs and not primary_email:
            continue

        if primary_email:
            existing_emails.add(primary_email)
        if primary_tg:
            existing_tgs.add(primary_tg.lower())

        contact_id = f"tg_{channel}_{re.sub(r'[^a-zA-Z0-9]', '', primary_email or primary_tg)}_{int(datetime.now().timestamp() * 1000)}"

        entry = {
            "id": contact_id,
            "selected": True,
            "name": f"Рекрутер ({primary_tg or channel})",
            "email": primary_email,
            "company": f"Telegram (@{channel})",
            "role": f"Рекрутер / HR ({title[:40]})",
            "city": "Удаленно / Remote",
            "stack": f"[{persona.upper()}] {title}",
            "phone": "",
            "telegram": primary_tg,
            "source": f"Telegram (@{channel})",
            "url": post_url,
            "status": "Pending",
            "score": "Hot" if primary_tg else "Warm",
            "mx_valid": True if primary_email else False,
            "mx_record": "",
            "created_at": now_str,
            "is_role_account": False,
            "lead_score": 85 if primary_tg else 70,
        }
        existing.append(entry)
        added += 1

    try:
        POISK_CONTACT_JSON.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Added {added} recruiter leads into {POISK_CONTACT_JSON.name} (Total: {len(existing)})")
    except Exception as e:
        print(f"Error saving to {POISK_CONTACT_JSON}: {e}", file=sys.stderr)


def save_jobs_to_db(jobs: list[dict]):
    """Insert telegram job posts into autopilot/jobs.db."""
    if not db:
        return 0

    con = db.connect()
    added = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for j in jobs:
        post_id = j.get("post_id", "")
        slug = re.sub(r'[^a-zA-Z0-9_]', '_', post_id)
        job_id = f"tg_{slug}"
        if db.job_exists(con, job_id):
            continue

        emails_str = ", ".join(j.get("emails", []))
        tgs_str = ", ".join(j.get("recruiters", []))
        desc = f"{j.get('text', '')}\n\n[Контакты: TG: {tgs_str or 'Не указан'} | Email: {emails_str or 'Не указан'}]"
        persona = j.get("persona", "appsec")
        cv_file = "creative.pdf" if persona == "creative" else "appsec.pdf"
        score = 85 if persona == "creative" else 90

        job_data = {
            "id": job_id,
            "source": "telegram",
            "keyword": j.get("channel", "telegram"),
            "title": j.get("title", "Вакансия"),
            "company": f"Telegram (@{j.get('channel')})",
            "location": "Remote",
            "url": j.get("post_url", ""),
            "description": desc,
            "score": score,
            "cv": cv_file,
            "status": "queued",
            "salary": j.get("salary"),
            "persona": persona,
            "created_at": now_iso,
        }
        try:
            db.upsert_job(con, job_data)
            added += 1
        except Exception:
            pass

    con.close()
    print(f"Saved {added} new vacancies into autopilot/jobs.db!")
    return added


def main():
    parser = argparse.ArgumentParser(description="Telegram Jobs Harvester")
    parser.add_argument("--channels", default=",".join(DEFAULT_CHANNELS), help="Comma-separated list of channel names")
    parser.add_argument("--limit-per-channel", type=int, default=25, help="Max posts to inspect per channel")
    parser.add_argument("--save-db", action="store_true", default=True, help="Save vacancies to jobs.db")
    parser.add_argument("--save-contacts", action="store_true", default=True, help="Save leads to poisk_contact/contacts.json")
    args = parser.parse_args()

    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    print(f"Starting Telegram Harvester across {len(channels)} channels...")

    total_jobs = []
    for ch in channels:
        print(f"Harvesting channel @{ch}...")
        jobs = fetch_channel_messages(ch, limit=args.limit_per_channel)
        creative_count = sum(1 for j in jobs if j.get("persona") == "creative")
        cyber_count = sum(1 for j in jobs if j.get("persona") == "appsec")
        with_recruiter = sum(1 for j in jobs if j.get("recruiters") or j.get("emails"))
        print(f"  Found {len(jobs)} posts (Creative: {creative_count}, Sec: {cyber_count}, With contacts: {with_recruiter})")
        total_jobs.extend(jobs)

    print(f"\nHarvested {len(total_jobs)} total Telegram vacancy posts.")

    if args.save_db:
        save_jobs_to_db(total_jobs)

    if args.save_contacts:
        save_leads_to_poisk_contact(total_jobs)


if __name__ == "__main__":
    main()
