import sys
sys.path.insert(0, 'tools')
import autopilot, db
import imaplib, email as email_mod
from email.header import decode_header
from datetime import datetime, timezone, timedelta
import sqlite3

con = db.connect()

# 1. Database Stats
c = con.cursor()
c.execute("SELECT COUNT(*) FROM applications WHERE submitted = 1")
submitted_cnt = c.fetchone()[0]

c.execute("SELECT COUNT(*) FROM applications WHERE needs_manual = 1")
manual_cnt = c.fetchone()[0]

c.execute("SELECT COUNT(*) FROM applications WHERE dead_listing = 1")
dead_cnt = c.fetchone()[0]

c.execute("SELECT COUNT(*) FROM jobs")
total_jobs = c.fetchone()[0]

print(f"=== DB STATS ===")
print(f"Total jobs: {total_jobs}")
print(f"Submitted applications: {submitted_cnt}")
print(f"Needs manual review: {manual_cnt}")
print(f"Dead listings: {dead_cnt}")

# 2. Check submitted companies
c.execute("""
    SELECT j.company, j.title, j.location, a.applied_at
    FROM applications a
    JOIN jobs j ON a.job_id = j.id
    WHERE a.submitted = 1
    ORDER BY a.applied_at DESC
    LIMIT 15
""")
print(f"\n=== RECENT 15 SUBMITTED APPLICATIONS ===")
for row in c.fetchall():
    print(f"- {row[0]} | {row[1]} | {row[2]} ({row[3][:16]})")

# 3. IMAP Mail Outcomes Analysis
cfg = autopilot.read_json(autopilot.CFG)
em = cfg.get("email", {})
M = imaplib.IMAP4_SSL(em.get("imap_host", "imap.gmail.com"), timeout=20)
outcomes = {"rejected": [], "applied": [], "interview": [], "other": []}

try:
    M.login(em["address"], em["app_password"].replace(" ", ""))
    M.select("INBOX")
    since = (datetime.now(timezone.utc) - timedelta(days=21)).strftime("%d-%b-%Y")
    typ, data = M.search(None, f'(SINCE "{since}")')
    ids = data[0].split()
    print(f"\n=== IMAP SCAN (Last 14 days: {len(ids)} emails) ===", flush=True)
    
    for mid in list(reversed(ids))[:60]:
        typ, msg_data = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
        if not msg_data or not msg_data[0]: continue
        msg = email_mod.message_from_bytes(msg_data[0][1])
        raw_s, enc_s = decode_header(msg.get("Subject", ""))[0]
        subj = raw_s.decode(enc_s or "utf-8", "ignore") if isinstance(raw_s, bytes) else (raw_s or "")
        from_hdr = msg.get("From", "")
        date_hdr = msg.get("Date", "")
        
        is_job = any(w in (subj + from_hdr).lower() for w in [
            "application", "greenhouse", "lever", "ashby", "workable", "smartrecruiters", 
            "careers", "interview", "update", "candidate", "role", "regret", "status",
            "cloudflare", "canonical", "monzo", "semgrep", "datadog", "anthropic", "thank"
        ])
        if not is_job:
            continue
            
        typ, bdata = M.fetch(mid, "(RFC822)")
        bmsg = email_mod.message_from_bytes(bdata[0][1])
        body = ""
        for part in (bmsg.walk() if bmsg.is_multipart() else [bmsg]):
            if part.get_content_type() in ("text/plain", "text/html"):
                try: body += part.get_payload(decode=True).decode("utf-8", "ignore")
                except Exception: pass
                
        status = autopilot.classify_email(subj, body)
        clean_body = " ".join(body.split())[:300]
        item = {
            "from": from_hdr,
            "subject": subj,
            "date": date_hdr,
            "status": status,
            "snippet": clean_body
        }
        if status in outcomes:
            outcomes[status].append(item)
        else:
            outcomes["other"].append(item)
        print(f"[{status}] {subj[:50]} | {from_hdr[:35]}", flush=True)
            
    print(f"\n=== OUTCOMES SUMMARY ===")
    print(f"Rejections: {len(outcomes['rejected'])}")
    print(f"Confirmations / In Progress: {len(outcomes['applied'])}")
    print(f"Interviews / Assessments: {len(outcomes['interview'])}")
    
    print(f"\n--- ALL REJECTIONS ({len(outcomes['rejected'])}) ---")
    for r in outcomes['rejected']:
        print(f"• Date: {r['date']}")
        print(f"  From: {r['from']}")
        print(f"  Subject: {r['subject']}")
        print(f"  Snippet: {r['snippet'][:160]}...\n")

    print(f"\n--- CONFIRMATIONS (Sample 5) ---")
    for a in outcomes['applied'][:5]:
        print(f"• [{a['date'][:16]}] {a['from'][:40]} | {a['subject'][:60]}")

finally:
    try: M.logout()
    except Exception: pass
