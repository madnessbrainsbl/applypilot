import json

import autopilot as A
import db


def demo():
    con = db.connect()
    key = "__selftest__ would you relocate to mars?"
    con.execute("DELETE FROM pending_questions WHERE question_key=?", (key,))
    con.execute("DELETE FROM qa_answers WHERE question_key=?", (key,))
    con.commit()

    db.record_pending_question(con, key, "Would you relocate to Mars?", ["Yes", "No"], "SpaceX", "http://x")
    db.record_pending_question(con, key, "Would you relocate to Mars?", ["Yes", "No"], "SpaceX", "http://x")
    row = next(r for r in db.list_pending_questions(con, 200) if r["question_key"] == key)
    assert row["seen"] == 2, "a repeat question must count up, not duplicate"
    assert json.loads(row["options"]) == ["Yes", "No"]

    db.save_qa_answer(con, key, "Would you relocate to Mars?", "Yes", "SpaceX")
    assert all(r["question_key"] != key for r in db.list_pending_questions(con, 200)), \
        "once answered it must leave the queue"

    con.execute("DELETE FROM qa_answers WHERE question_key=?", (key,))
    con.execute("DELETE FROM pending_questions WHERE question_key=?", (key,))
    con.commit()
    print("pending questions self-check OK")


if __name__ == "__main__":
    demo()
