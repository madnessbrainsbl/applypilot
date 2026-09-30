import tempfile, pathlib
from datetime import datetime, timezone, timedelta

import db

tmp = pathlib.Path(tempfile.mkdtemp()) / "jobs.db"
db.DB_PATH = tmp
con = db.connect()

NOW = datetime.now(timezone.utc)

def job(jid, company):
    db.upsert_job(con, {"id": jid, "source": "vendor", "keyword": "", "title": f"Role {jid}",
                        "company": company, "location": "", "url": f"https://x/{jid}",
                        "description": "", "score": 50, "cv": None, "status": "queued",
                        "created_at": NOW.isoformat()})

def app(jid, age_days, submitted=1):
    db.insert_application(con, {"id": jid, "submitted": submitted,
                                 "applied_at": (NOW - timedelta(days=age_days)).isoformat()})

job("a1", "Acme Corp"); job("a2", "Acme Corp"); job("a3", "ACME corp")
job("b1", "Beta"); job("c1", "Gamma")
app("a1", 1); app("a2", 10); app("a3", 29)
app("a1", 2)                     # re-submit of the same job counts once
app("b1", 5, submitted=0)        # failed attempt does not count
app("c1", 31)                    # outside the window

counts = db.recent_company_counts(con, 30)
assert counts == {"acme-corp": 3}, counts
assert db.recent_company_counts(con, 7) == {"acme-corp": 1}, "window cut-off must drop older submissions"
assert db.recent_company_counts(con, 60) == {"acme-corp": 3, "gamma": 1}

print("company window self-check OK")
