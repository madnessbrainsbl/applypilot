import copy
import json

import pytest
import autopilot as A
import db


CV = "Application Security Engineer. Built Python tools for secure code review at Cedar Labs."
QUESTION = {"label": "Describe a project you are proud of", "type": "text"}
JOB = {"id": "fixture", "url": "https://example.invalid/job", "company": "Demo", "persona": "appsec"}
CFG = {"active_persona": "appsec", "profile": {"location": "Russia"}, "apply": {"require_sponsorship": True}}
DRAFT = "I built Python tools for secure code review at Cedar Labs."


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(A, "extract_cv_text", lambda *_: CV)
    monkeypatch.setattr(A, "pick_cv", lambda *_: "appsec.pdf")
    monkeypatch.setattr(A, "_answer_grounding", lambda *_: CV)
    monkeypatch.setattr(A, "_fill_expired", lambda: False)
    monkeypatch.setattr(A._TL, "fill_deadline", 0, raising=False)
    with db.connect() as connection:
        yield connection


def packet(verdict="approve", answer=DRAFT, evidence=None):
    return json.dumps({"reviews": [{"index": 0, "verdict": verdict, "answer": answer,
        "reason": "Опыт подтверждён резюме.",
        "evidence": ["Built Python tools for secure code review at Cedar Labs."] if evidence is None else evidence}]})


def check(con, cfg=None, answers=None):
    return A.check_form_answers(JOB, [QUESTION], {QUESTION["label"]: DRAFT} if answers is None else answers,
                                cfg or CFG, con, {QUESTION["label"]: {"source": "llm"}})


def test_reviewer_gets_cv_question_role_and_draft_and_records_evidence(con, monkeypatch):
    before = copy.deepcopy(CFG)
    def complete(cfg, system, user, **kwargs):
        payload = json.loads(user)
        assert payload["cv_text"] == CV
        assert payload["questions"][0]["draft"] == DRAFT
        assert payload["questions"][0]["question"] == QUESTION["label"]
        assert payload["role"]["company"] == "Demo"
        assert kwargs["budget"] <= 25
        return packet()
    monkeypatch.setattr(A, "llm_complete", complete)
    assert check(con) == {QUESTION["label"]: DRAFT}
    row = db.list_answer_decisions(con)[0]
    assert row["status"] == "approved" and row["method"] == "cv_review"
    assert row["evidence"] == ["Built Python tools for secure code review at Cedar Labs."]
    assert row["persona"] == "appsec" and CFG == before


@pytest.mark.parametrize("response", ["", "invalid", "{}", packet(evidence=[]),
    packet(evidence=["Led a Kubernetes team at Google."]), packet(verdict="manual"),
    '{"reviews":[{"index":0},{"index":0}]}'])
def test_failed_or_unverifiable_review_goes_to_manual(con, monkeypatch, response):
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: response)
    assert check(con) == {}
    row = db.list_answer_decisions(con)[0]
    assert row["status"] == "manual" and row["reason"]
    assert con.execute("SELECT count(*) FROM qa_answers").fetchone()[0] == 0


def test_one_grounded_revision_is_accepted(con, monkeypatch):
    revised = "At Cedar Labs, I built Python tools for secure code review."
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: packet("revise", revised))
    assert check(con) == {QUESTION["label"]: revised}
    row = db.list_answer_decisions(con)[0]
    assert row["status"] == "revised" and row["draft"] == DRAFT and row["answer"] == revised


def test_future_contribution_needs_quotes_for_experience_not_for_aspiration(con, monkeypatch):
    draft = DRAFT + " I would like to help improve your security review workflows."
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: packet(answer=draft))
    assert check(con, answers={QUESTION["label"]: draft}) == {QUESTION["label"]: draft}


def test_unclaimed_job_requirements_do_not_invalidate_a_grounded_answer(con, monkeypatch):
    job = {**JOB, "description": "We require OSCP and Kubernetes operations experience."}
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: packet())
    assert A.check_form_answers(job, [QUESTION], {QUESTION["label"]: DRAFT}, CFG, con, {}) == {QUESTION["label"]: DRAFT}


@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_literal_evidence_for_multiple_claims_has_a_bounded_limit(con, monkeypatch, count):
    quotes = ["Application Security Engineer.", "Built Python tools", "secure code review", "at Cedar Labs.",
              "Python tools for secure code review", "Built Python tools for secure code review at Cedar Labs."][:count]
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: packet(evidence=quotes))
    assert bool(check(con)) == (count <= 5)


def test_evidence_ids_resolve_to_the_original_cv_text(con, monkeypatch):
    def complete(cfg, system, user, **kwargs):
        payload = json.loads(user)
        assert payload["cv_evidence"] == [{"id": 0, "text": CV}]
        return packet(evidence=[0])
    monkeypatch.setattr(A, "llm_complete", complete)
    assert check(con) == {QUESTION["label"]: DRAFT}
    assert db.list_answer_decisions(con)[0]["evidence"] == [CV]


@pytest.mark.parametrize("evidence", [[-1], [1], [True], ["0"]])
def test_unknown_or_noninteger_evidence_ids_cannot_approve(con, monkeypatch, evidence):
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: packet(evidence=evidence))
    assert check(con) == {}


def test_revision_cannot_override_existing_fact_guards(con, monkeypatch):
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: packet("revise", "I hold an OSCP certification."))
    assert check(con) == {}
    assert "OSCP" in db.list_answer_decisions(con)[0]["reason"]


def test_missing_cv_never_calls_reviewer_or_accepts_complex_answer(con, monkeypatch):
    monkeypatch.setattr(A, "extract_cv_text", lambda *_: "")
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: pytest.fail("missing CV"))
    assert check(con) == {}


def test_review_cache_is_bound_to_cv_and_draft(con, monkeypatch):
    calls = []
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: calls.append(True) or packet())
    assert check(con)
    assert check(con)
    assert len(calls) == 1
    monkeypatch.setattr(A, "extract_cv_text", lambda *_: "Different candidate CV.")
    assert check(con) == {}
    assert len(calls) == 2


def test_tightened_review_policy_rechecks_previous_approval(con, monkeypatch):
    calls = []
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: calls.append(True) or packet())
    assert check(con) and check(con) and len(calls) == 1
    monkeypatch.setattr(A, "_ANSWER_REVIEW_SYSTEM", A._ANSWER_REVIEW_SYSTEM + " Verify responsibility scope.")
    assert check(con) and len(calls) == 2


def test_profession_geography_and_visa_answers_do_not_use_llm_review(con, monkeypatch):
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: pytest.fail("deterministic eligibility"))
    q = {"label": "Do you require visa sponsorship?", "type": "select", "options": ["Yes", "No"]}
    assert A.check_form_answers(JOB, [q], {q["label"]: "Yes"}, CFG, con, {}) == {q["label"]: "Yes"}
    assert db.list_answer_decisions(con)[0]["method"] == "rules"


def test_rejected_cached_answer_remains_pending_until_user_updates_it(con):
    key = A._qa_key(QUESTION["label"], (), "Demo", "appsec")
    db.save_qa_answer(con, key, QUESTION["label"], "Unverified old answer")
    db.record_answer_decision(con, {"question_key": key, "job_url": JOB["url"], "status": "manual",
                                   "reason": "Не подтверждено резюме"})
    db.record_pending_question(con, key, QUESTION["label"], [], "Demo", JOB["url"])
    db.clear_answered_pending(con)
    rows = db.list_pending_questions(con)
    assert rows[0]["reason"] == "Не подтверждено резюме"
    db.save_qa_answer(con, key, QUESTION["label"], DRAFT)
    assert db.list_pending_questions(con) == []


def test_revision_cannot_add_or_change_visa_or_geography(con, monkeypatch):
    answer = DRAFT + " I do not require visa sponsorship."
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: packet("revise", answer))
    assert check(con) == {}
    assert "визовые" in db.list_answer_decisions(con)[0]["reason"]


def test_pending_reason_tracks_the_latest_job_for_a_shared_question(con):
    key = A._qa_key(QUESTION["label"], (), "", "appsec")
    db.record_pending_question(con, key, QUESTION["label"], [], "Old", "https://example.invalid/old")
    db.save_qa_answer(con, key, QUESTION["label"], DRAFT)
    db.record_answer_decision(con, {"question_key": key, "job_url": JOB["url"], "status": "manual",
                                   "reason": "Нужна проверка для новой вакансии"})
    db.record_pending_question(con, key, QUESTION["label"], [], "Demo", JOB["url"])
    db.clear_answered_pending(con)
    row = db.list_pending_questions(con)[0]
    assert row["job_url"] == JOB["url"] and row["reason"] == "Нужна проверка для новой вакансии"


def test_reviewer_uses_the_jobs_persona_cv_and_cache_key(con, monkeypatch):
    cv = "Photographer. Created studio portraits using professional lighting."
    cfg = {**CFG, "personas": {"creative": {"cv": "creative.pdf", "profile": {"first_name": "Creative"}}}}
    job = {**JOB, "persona": "creative"}
    monkeypatch.setattr(A, "pick_cv", lambda j, c: c["personas"][j["persona"]]["cv"])
    monkeypatch.setattr(A, "extract_cv_text", lambda path: cv if path == "creative.pdf" else CV)
    monkeypatch.setattr(A, "_answer_grounding", lambda *_: cv)
    draft = "I created studio portraits using professional lighting."
    def complete(c, system, user, **kwargs):
        assert json.loads(user)["cv_text"] == cv
        return packet(answer=draft, evidence=["Created studio portraits using professional lighting."])
    monkeypatch.setattr(A, "llm_complete", complete)
    assert A.check_form_answers(job, [QUESTION], {QUESTION["label"]: draft}, cfg, con, {}) == {QUESTION["label"]: draft}
    row = db.list_answer_decisions(con)[0]
    assert row["persona"] == "creative" and row["cv"] == "creative.pdf"
    assert row["question_key"].startswith("creative@")


def test_unavailable_reviewer_and_expired_budget_never_approve(con, monkeypatch):
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")))
    assert check(con) == {}
    monkeypatch.setattr(A, "_fill_expired", lambda: True)
    monkeypatch.setattr(A, "llm_complete", lambda *a, **k: pytest.fail("expired form budget"))
    assert check(con) == {}
