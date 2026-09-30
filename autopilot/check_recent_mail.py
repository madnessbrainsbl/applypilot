import sys
sys.path.insert(0, 'tools')
import autopilot
import imaplib, email as email_mod
from email.header import decode_header
from datetime import datetime, timezone, timedelta

cfg = autopilot.read_json(autopilot.CFG)
em = cfg.get("email", {})
M = imaplib.IMAP4_SSL(em.get("imap_host", "imap.gmail.com"), timeout=15)
try:
    M.login(em["address"], em["app_password"].replace(" ", ""))
    M.select("INBOX")
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d-%b-%Y")
    typ, data = M.search(None, f'(SINCE "{since}")')
    ids = data[0].split()
    print(f"Total emails in last 7 days: {len(ids)}")
    # Check last 35 emails fast by fetching only SUBJECT and FROM
    for mid in list(reversed(ids))[:35]:
        typ, msg_data = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
        if not msg_data or not msg_data[0]: continue
        msg = email_mod.message_from_bytes(msg_data[0][1])
        raw_s, enc_s = decode_header(msg.get("Subject", ""))[0]
        subj = raw_s.decode(enc_s or "utf-8", "ignore") if isinstance(raw_s, bytes) else (raw_s or "")
        from_hdr = msg.get("From", "")
        date_hdr = msg.get("Date", "")
        # Fetch body only if relevant
        is_job = any(w in (subj + from_hdr).lower() for w in [
            "application", "greenhouse", "lever", "ashby", "workable", "smartrecruiters", 
            "careers", "interview", "update", "candidate", "role", "regret", "status"
        ])
        if is_job:
            typ, bdata = M.fetch(mid, "(RFC822)")
            bmsg = email_mod.message_from_bytes(bdata[0][1])
            body = ""
            for part in (bmsg.walk() if bmsg.is_multipart() else [bmsg]):
                if part.get_content_type() in ("text/plain", "text/html"):
                    try: body += part.get_payload(decode=True).decode("utf-8", "ignore")
                    except Exception: pass
            status = autopilot.classify_email(subj, body)
            print(f"[{date_hdr[:16]}] {from_hdr[:35]} | {subj[:50]} -> STATUS: {status}")
            if status == "rejected":
                # print snippet of body
                snippet = " ".join(body.split())[:200]
                print(f"   REJECTION TEXT: {snippet}")
finally:
    try: M.logout()
    except Exception: pass
