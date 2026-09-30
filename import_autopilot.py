import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUTOPILOT_DB = ROOT / "autopilot" / "jobs.db"
JOBSYNC_DB = ROOT / "prisma" / "dev.db"

STATUSES = {"applied": "Applied", "draft": "Draft", "interview": "Interview",
            "rejected": "Rejected", "offer-accepted": "Offer Accepted", "offer-declined": "Offer Declined"}


def slug(text):
    return "-".join("".join(c.lower() if c.isalnum() else " " for c in (text or "")).split()) or "unknown"


def iso(value, fallback=None):
    return (value or "").replace("+00:00", "Z") or fallback


def lookup(con, table, label, user_id, cache):
    value = slug(label)
    key = (table, value)
    if key in cache:
        return cache[key]
    row = con.execute(f'SELECT id FROM "{table}" WHERE value=? AND createdBy=?', (value, user_id)).fetchone()
    if row:
        cache[key] = row[0]
        return row[0]
    new_id = str(uuid.uuid4())
    con.execute(f'INSERT INTO "{table}" (id, label, value, createdBy) VALUES (?,?,?,?)',
                (new_id, (label or "Unknown").strip()[:120], value, user_id))
    cache[key] = new_id
    return new_id


def ensure_statuses(con):
    ids = {}
    for value, label in STATUSES.items():
        row = con.execute('SELECT id FROM "JobStatus" WHERE value=?', (value,)).fetchone()
        if row:
            ids[value] = row[0]
            continue
        new_id = str(uuid.uuid4())
        con.execute('INSERT INTO "JobStatus" (id, label, value) VALUES (?,?,?)', (new_id, label, value))
        ids[value] = new_id
    return ids


def main():
    if not AUTOPILOT_DB.exists():
        sys.exit(f"autopilot database not found: {AUTOPILOT_DB}")
    if not JOBSYNC_DB.exists():
        sys.exit(f"jobsync database not found: {JOBSYNC_DB} — run prisma migrate deploy first")

    dst = sqlite3.connect(JOBSYNC_DB)
    dst.row_factory = sqlite3.Row
    users = dst.execute('SELECT id, email FROM "User" ORDER BY rowid LIMIT 1').fetchone()
    if not users:
        sys.exit("no JobSync account yet — open http://localhost:3737, register, then run this again")
    user_id = users["id"]

    src = sqlite3.connect(f"file:{AUTOPILOT_DB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    rows = src.execute("""
        SELECT j.id, j.title, j.company, j.location, j.url, j.description, j.source,
               j.score, j.created_at,
               MAX(a.submitted) AS submitted,
               MAX(CASE WHEN a.submitted=1 THEN a.applied_at END) AS applied_at
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        WHERE j.hidden=0 AND j.dead_listing=0
        GROUP BY j.id
        ORDER BY j.score DESC
    """).fetchall()

    status_ids = ensure_statuses(dst)
    existing = {r[0] for r in dst.execute('SELECT jobUrl FROM "Job" WHERE userId=? AND jobUrl IS NOT NULL',
                                          (user_id,)).fetchall()}
    cache = {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    added = skipped = 0
    for r in rows:
        url = r["url"] or ""
        if not url or url in existing:
            skipped += 1
            continue
        submitted = bool(r["submitted"])
        # Job.createdAt is NOT NULL: one autopilot row with a blank created_at used to
        # raise mid-loop, and since the commit is after the loop the whole import was lost
        created = iso(r["created_at"], now)
        dst.execute(
            'INSERT INTO "Job" (id, userId, jobUrl, description, jobType, createdAt, applied, appliedDate,'
            ' statusId, jobTitleId, companyId, jobSourceId, locationId, matchScore, discoveredAt,'
            ' discoveryStatus) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()), user_id, url, (r["description"] or "")[:20000], "Full-time",
             created, 1 if submitted else 0, iso(r["applied_at"]),
             status_ids["applied" if submitted else "draft"],
             lookup(dst, "JobTitle", r["title"], user_id, cache),
             lookup(dst, "Company", r["company"], user_id, cache),
             lookup(dst, "JobSource", r["source"], user_id, cache),
             lookup(dst, "Location", r["location"], user_id, cache) if (r["location"] or "").strip() else None,
             r["score"], created, "approved" if submitted else "pending"))
        existing.add(url)
        added += 1
    dst.commit()

    total = dst.execute('SELECT COUNT(*) FROM "Job" WHERE userId=?', (user_id,)).fetchone()[0]
    applied = dst.execute('SELECT COUNT(*) FROM "Job" WHERE userId=? AND applied=1', (user_id,)).fetchone()[0]
    print(f"imported for {users['email']}: {added} new, {skipped} already there")
    print(f"jobsync now holds {total} jobs, {applied} of them marked applied")


if __name__ == "__main__":
    main()
