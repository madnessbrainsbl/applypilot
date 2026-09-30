import tempfile, pathlib
import db
import autopilot as A

tmp = pathlib.Path(tempfile.mkdtemp()) / "jobs.db"
db.DB_PATH = tmp
con = db.connect()

CFG = {"profile": {"school": "Example State University",
                   "degree": "Doctor of Veterinary Medicine (DVM)"}}

def add(key, label, answer, hits=1):
    db.save_qa_answer(con, key, label, answer)
    con.execute("UPDATE qa_answers SET hits=? WHERE question_key=?", (hits, key))
    con.commit()

add("relocate", "Are you willing to relocate?", "I am willing to relocate to this job's location.", 20)
add("inperson", "Are you open to working in-person in one of our offices 25% of the time?", "No", 19)
add("school", "School", "Aberystwyth University", 7)
add("institution", "Institution", "Example State University", 5)
add("confirm", "culture is at the heart of it. please confirm you acknowledge this policy.", "I confirm/", 11)
add("visa", "Will you now or will you in the future require employment visa sponsorship to work in the country?", "Yes", 19)
add("citizen", "Due to government requirements, you must be a citizen of the country you are applying in.", "No", 3)
add("enrolled", "If you are currently enrolled in a university, which one?", "N/A", 1)

flags = {f["key"]: f for f in A.qa_audit(con, CFG)}

assert "inperson" in flags, "a NO to office attendance must be flagged while relocation says yes"
assert any("contradiction" in p for p in flags["inperson"]["problems"])

assert "school" in flags, "a school that contradicts the profile must be flagged"
assert "Aberystwyth" in flags["school"]["problems"][0]

assert "confirm" in flags and any("malformed" in p for p in flags["confirm"]["problems"])

assert "institution" not in flags, "an institution matching the profile is fine"
assert "visa" not in flags, "an honest Yes to a visa question is not a defect"
assert "citizen" not in flags, "an honest No to a citizenship question is not a defect"
assert "enrolled" not in flags, "N/A to a not-applicable question is not a defect"
assert "relocate" not in flags

db.update_qa_answer(con, "inperson", "Yes")
flags2 = {f["key"]: f for f in A.qa_audit(con, CFG)}
assert "inperson" not in flags2, "fixing the answer must clear the flag"

con.execute("DELETE FROM qa_answers WHERE question_key='relocate'")
con.commit()
flags3 = {f["key"]: f for f in A.qa_audit(con, CFG)}
assert "citizen" not in flags3

print("qa audit self-check OK")
