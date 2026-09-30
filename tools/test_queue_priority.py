import tempfile, pathlib
from datetime import datetime, timezone, timedelta

import db

tmp = pathlib.Path(tempfile.mkdtemp()) / "jobs.db"
db.DB_PATH = tmp
con = db.connect()

NOW = datetime.now(timezone.utc)

def add(jid, score, age_days, company="Acme", title=None):
    con.execute(
        """INSERT INTO jobs (id, source, keyword, title, company, location, url, description,
                             score, cv, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (jid, "vendor", "", title or f"Role {jid}", company, "", f"https://x/{jid}", "",
         score, None, "queued",
         (NOW - timedelta(days=age_days)).isoformat(), NOW.isoformat()),
    )
    con.commit()

add("fresh_mid", 45, 1)
add("old_high", 60, 20)
add("ancient_top", 90, 60)
add("fresh_low", 10, 0)

order = [j["id"] for j in db.eligible_jobs(con, 0)]

assert order.index("fresh_mid") < order.index("old_high"), \
    "a fresh decent role must outrank a two-week-old slightly better one"
assert order.index("ancient_top") < order.index("fresh_low"), \
    "freshness must not override a far stronger match"
assert order[0] == "ancient_top" or order[0] == "fresh_mid"

prio = {j["id"]: j["priority"] for j in db.eligible_jobs(con, 0)}
assert prio["fresh_low"] == 10, "a same-day posting takes no staleness penalty"
assert prio["old_high"] == 60 - 30, "a 20-day-old posting takes the 30-point penalty"
assert prio["ancient_top"] == 90 - 50, "a 60-day-old posting takes the 50-point penalty"
assert prio["fresh_mid"] == 45 - 0

con.execute("UPDATE jobs SET dead_listing=1 WHERE id='fresh_mid'")
con.commit()
assert "fresh_mid" not in [j["id"] for j in db.eligible_jobs(con, 0)], \
    "a delisted job must stay out regardless of freshness"

con.execute("UPDATE jobs SET hidden=1 WHERE id='fresh_low'")
con.commit()
assert "fresh_low" not in [j["id"] for j in db.eligible_jobs(con, 0)], \
    "a hidden job must stay out regardless of freshness"

assert all(j["score"] >= 60 for j in db.eligible_jobs(con, 60)), "min_score filter still applies"

print("queue priority self-check OK")
