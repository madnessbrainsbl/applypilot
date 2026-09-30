import argparse, collections, shutil, sys
import db
import autopilot as A


def survivor(rows, con):
    def rank(r):
        has_app = con.execute("SELECT COUNT(*) FROM applications WHERE job_id=?", (r["id"],)).fetchone()[0]
        submitted = con.execute("SELECT COUNT(*) FROM applications WHERE job_id=? AND submitted=1",
                                (r["id"],)).fetchone()[0]
        return (submitted, has_app, -(r["hidden"] or 0), r["created_at"] or "")
    return sorted(rows, key=rank, reverse=True)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = db.connect()
    groups = collections.defaultdict(list)
    for r in con.execute("SELECT * FROM jobs"):
        groups[A.sha(A.canonical_url(r["url"]) or (r["source"] or "") + (r["title"] or "") + (r["company"] or ""))].append(dict(r))

    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    merged = sum(len(v) - 1 for v in dupes.values())
    print(f"job rows: {sum(len(v) for v in groups.values())}")
    print(f"distinct jobs after canonicalisation: {len(groups)}")
    print(f"redundant rows to merge: {merged}")

    if not args.apply:
        for k, v in list(dupes.items())[:5]:
            keep = survivor(v, con)
            print(f"  {len(v)}x {(keep['company'] or '?')[:24]:24} {(keep['title'] or '')[:44]}")
        print("\ndry run — re-run with --apply to merge")
        return

    shutil.copy(db.DB_PATH, str(db.DB_PATH) + ".bak")
    print(f"backup: {db.DB_PATH}.bak")

    moved_apps = moved_out = deleted = 0
    for canon_id, rows in dupes.items():
        keep = survivor(rows, con)
        for r in rows:
            if r["id"] == keep["id"]:
                continue
            moved_apps += con.execute("UPDATE applications SET job_id=? WHERE job_id=?",
                                      (keep["id"], r["id"])).rowcount
            moved_out += con.execute("UPDATE outcomes SET job_id=? WHERE job_id=?",
                                     (keep["id"], r["id"])).rowcount
            if r["hidden"]:
                con.execute("UPDATE jobs SET hidden=1 WHERE id=?", (keep["id"],))
            if r["dead_listing"]:
                con.execute("UPDATE jobs SET dead_listing=1 WHERE id=?", (keep["id"],))
            deleted += con.execute("DELETE FROM jobs WHERE id=?", (r["id"],)).rowcount

    rekeyed = 0
    for canon_id, rows in groups.items():
        keep = survivor(rows, con) if len(rows) > 1 else rows[0]
        if keep["id"] == canon_id or con.execute("SELECT 1 FROM jobs WHERE id=?", (canon_id,)).fetchone():
            continue
        con.execute("UPDATE jobs SET id=? WHERE id=?", (canon_id, keep["id"]))
        con.execute("UPDATE applications SET job_id=? WHERE job_id=?", (canon_id, keep["id"]))
        con.execute("UPDATE outcomes SET job_id=? WHERE job_id=?", (canon_id, keep["id"]))
        rekeyed += 1
    con.commit()
    print(f"merged: {deleted} rows deleted, {moved_apps} applications and {moved_out} outcomes repointed")
    print(f"re-keyed to canonical ids: {rekeyed}")
    print(f"jobs now: {db.counts(con)[0]}")


if __name__ == "__main__":
    main()
