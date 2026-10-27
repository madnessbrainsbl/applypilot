import pytest

import autopilot as A
import db


CV = "Application Security Engineer, 5 years of experience. Python, secure code review."
CFG = {"profile": {"location": "Springfield, Canada"}, "apply": {"require_sponsorship": True, "answers": {"open_to_relocation": "Yes"}}, "active_persona": "appsec"}
JOB = {"id": "fixture", "title": "Engineer", "company": "Fixture", "url": "https://example.invalid/job"}


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        instance = pw.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(A, "_answer_grounding", lambda *_: CV)
    monkeypatch.setattr(A, "extract_cv_text", lambda *_: CV)
    monkeypatch.setattr(A, "_fill_expired", lambda: False)
    monkeypatch.setattr(A, "llm_providers", lambda _: ["fixture"])
    monkeypatch.setattr(A, "ask_llm_answers", lambda *_: {})
    context = browser.new_context()
    instance = context.new_page()
    yield instance
    context.close()


@pytest.mark.parametrize("label,options", [
    ("Do you hold an astrophysics qualification?", ["Yes", "No"]),
    ("Highest unknown professional qualification", ["CISSP"]),
    ("List your completed certifications", []),
    ("Do you meet all requirements?", ["Yes", "No"]),
    ("How many years of experience in aerospace do you have?", ["0-2", "5+"]),
    ("Are you applying via an automated script?", ["Yes", "No"]),
])
def test_unknown_facts_have_no_offline_answer(label, options):
    assert A.offline_answer(label, options, CFG) is None


@pytest.mark.parametrize("source", ["cache", "offline", "llm"])
def test_unsupported_certification_is_never_filled_or_cached(page, monkeypatch, source):
    label = "Please list your professional certifications"
    page.set_content(f'<label for="cert">{label}</label><textarea id="cert" required></textarea>')
    key = A._qa_key(label, (), JOB["company"], "appsec")
    with db.connect() as con:
        if source == "cache":
            db.save_qa_answer(con, key, label, "OSCP", JOB["company"])
    if source == "offline":
        monkeypatch.setattr(A, "offline_answer", lambda *_: "OSCP")
    if source == "llm":
        monkeypatch.setattr(A, "ask_llm_answers", lambda *_: {label: "OSCP"})
    assert A.llm_fill(page, CFG.copy(), JOB) == 0
    assert page.locator("#cert").input_value() == ""
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM pending_questions").fetchone()[0] == 1
        # Existing private QA data is retained; new unsupported answers are not saved.
        assert con.execute("SELECT count(*) FROM qa_answers").fetchone()[0] == (source == "cache")


def test_unknown_native_select_and_ashby_buttons_stay_unanswered(page):
    page.set_content('''
        <label for="qual">Do you hold an astrophysics qualification?</label>
        <select id="qual" required><option value="">Select...</option><option>Yes</option><option>No</option></select>
        <div class="ashby-application-form-field-entry">
          <label>Do you hold an astrophysics qualification?</label>
          <div class="_yesno_fixture"><button onclick="this.setAttribute('aria-pressed','true')">Yes</button><button>No</button></div>
        </div>''')
    assert A.fill_yesno_buttons(page, CFG) == 0
    assert A.llm_fill(page, CFG.copy(), JOB) == 0
    assert page.locator("#qual").input_value() == ""
    assert page.locator('[aria-pressed="true"]').count() == 0
    assert A.required_field_issues(page), "unknown required answers must reach manual review"


def test_unknown_required_checkboxes_are_not_guessed(page):
    page.set_content('''<fieldset><legend>Qualifications *</legend>
        <label><input type="checkbox" required>CISSP</label>
        <label><input type="checkbox">OSCP</label></fieldset>
        <label><input type="checkbox" required>PhD in astrophysics</label>''')
    A.fill_scope(page, CFG, None, None, job=JOB)
    assert page.locator("input:checked").count() == 0


def test_known_permit_and_grounded_answer_still_work(page, monkeypatch):
    assert A.offline_answer("Do you require visa sponsorship?", ["Yes", "No"], CFG) == "Yes"
    assert A.offline_answer("Are you open to relocation?", ["Yes", "No"], CFG) == "Yes"
    label = "Describe your professional experience"
    page.set_content(f'<label for="experience">{label}</label><textarea id="experience"></textarea>')
    answer = "I have 5 years of experience in application security and secure code review."
    monkeypatch.setattr(A, "ask_llm_answers", lambda *_: {label: answer})
    assert A.llm_fill(page, CFG.copy(), JOB) == 1
    assert page.locator("#experience").input_value() == answer


def test_grounded_checkboxes_and_consent_still_work(page):
    page.set_content('''<fieldset><legend>Which skills have you used? *</legend>
        <label><input id="python" type="checkbox">Python</label>
        <label><input id="oscp" type="checkbox">OSCP</label>
        <label><input id="none" type="checkbox">None of the above</label></fieldset>
        <label><input id="privacy" type="checkbox" required>I agree to the privacy policy</label>''')
    A.fill_scope(page, CFG, "fixture.pdf", None, job=JOB)
    assert page.locator("#python").is_checked()
    assert page.locator("#privacy").is_checked()
    assert not page.locator("#oscp").is_checked()
    assert not page.locator("#none").is_checked()


def test_unknown_single_react_option_is_not_selected(page):
    page.set_content('''<div><label>Highest unknown qualification</label>
        <div class="select__control" onclick="document.querySelector('.select__option').hidden=false">Select...</div>
        <div class="select__option" hidden onclick="this.dataset.picked='yes'">CISSP</div></div>''')
    assert A.fill_react_selects(page, page, CFG) == 0
    assert page.locator(".select__option").get_attribute("data-picked") is None


def test_existing_guard_selfcheck_with_isolated_database(page):
    from tests.test_llm_answer_guard import demo

    demo()


def test_claims_are_checked_without_cv_text_and_regardless_of_case():
    assert A._unsupported_claim({"label": "Qualifications"}, "oscp", CV)
    assert A._unsupported_claim({"label": "Experience"}, "5 years of experience", '{"location": "Canada"}')


def test_location_matches_whole_words():
    assert A._job_country_option("Paris, France", ["Germany", "France"]) == "France"
    assert A._job_country_option("Parisian systems", ["Germany", "France"]) == "Germany"
    assert A.offline_answer("Do you currently live in Springfield?", ["Yes", "No"], CFG) == "Yes"
    assert A.offline_answer("Do you currently live in NewSpringfield?", ["Yes", "No"], CFG) == "No"


EXPORT_LABEL = "Please list any countries of which you are a citizen or legal permanent resident, and when you obtained such status."
CITIZENSHIP = "Canadian citizenship from birth."


@pytest.mark.parametrize("source", ["cache", "offline", "llm"])
def test_unrelated_visa_answer_is_rejected_for_export_status(page, monkeypatch, source):
    page.set_content(f'<label for="status">{EXPORT_LABEL}</label><textarea id="status" required></textarea>')
    key = A._qa_key(EXPORT_LABEL, (), JOB["company"], "appsec")
    bad = "Student visa expires 18"
    with db.connect() as con:
        if source == "cache":
            db.save_qa_answer(con, key, EXPORT_LABEL, bad, JOB["company"])
    if source == "offline":
        monkeypatch.setattr(A, "offline_answer", lambda *_: bad)
    if source == "llm":
        monkeypatch.setattr(A, "ask_llm_answers", lambda *_: {EXPORT_LABEL: bad})
    assert A.llm_fill(page, CFG.copy(), JOB) == 0
    assert page.locator("#status").input_value() == ""
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM pending_questions").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM qa_answers").fetchone()[0] == (source == "cache")


def test_explicit_citizenship_replaces_bad_cache_without_inferring_from_residence(page, monkeypatch):
    import json

    assert A._unsupported_claim({"label": EXPORT_LABEL}, CITIZENSHIP, json.dumps(CFG["profile"]))
    unknown_date = json.dumps({"citizenship_statement": "Canada — Canadan citizenship."})
    assert A._unsupported_claim({"label": EXPORT_LABEL}, "Canada — Canadan citizenship.", unknown_date)
    profile = {**CFG["profile"], "citizenship_statement": CITIZENSHIP}
    cfg = {**CFG, "profile": profile}
    assert A.offline_answer("List your permanent residency countries", (), cfg) is None
    assert A._unsupported_claim({"label": "List your permanent residency countries"}, CITIZENSHIP, json.dumps(profile))
    monkeypatch.setattr(A, "_answer_grounding", lambda *_: json.dumps(profile))
    page.set_content(f'<label for="status">{EXPORT_LABEL}</label><textarea id="status" required></textarea>')
    key = A._qa_key(EXPORT_LABEL, (), JOB["company"], "appsec")
    with db.connect() as con:
        db.save_qa_answer(con, key, EXPORT_LABEL, "Student visa expires 18", JOB["company"])
    assert A.offline_answer(EXPORT_LABEL, (), cfg) == CITIZENSHIP
    assert A.llm_fill(page, cfg, JOB) == 1
    assert page.locator("#status").input_value() == CITIZENSHIP


@pytest.mark.parametrize("source", ["cache", "llm", "offline"])
@pytest.mark.parametrize("approve", [True, False])
def test_complex_answers_are_reviewed_before_fill_or_cache(page, monkeypatch, source, approve):
    import json
    cv = "Built Python tools for secure code review at Cedar Labs."
    label = "Describe a project you are proud of"
    draft = "I built Python tools for secure code review at Cedar Labs."
    monkeypatch.setattr(A, "extract_cv_text", lambda *_: cv)
    monkeypatch.setattr(A, "_answer_grounding", lambda *_: cv)
    monkeypatch.setattr(A, "ask_llm_answers", lambda *_: {label: draft} if source == "llm" else {})
    monkeypatch.setattr(A, "offline_answer", lambda *_: draft if source == "offline" else None)
    def review(*args, **kwargs):
        return json.dumps({"reviews": [{"index": 0, "verdict": "approve" if approve else "manual",
            "answer": draft if approve else "", "reason": "Подтверждено" if approve else "Не подтверждено",
            "evidence": [cv] if approve else []}]})
    monkeypatch.setattr(A, "llm_complete", review)
    page.set_content(f'<label for="project">{label}</label><textarea id="project" required></textarea>')
    key = A._qa_key(label, (), JOB["company"], "appsec")
    if source == "cache":
        with db.connect() as con:
            db.save_qa_answer(con, key, label, draft)
    assert A.llm_fill(page, CFG.copy(), JOB) == int(approve)
    assert page.locator("#project").input_value() == (draft if approve else "")
    with db.connect() as con:
        row = db.list_answer_decisions(con)[0]
        assert row["source"] == {"cache": "cache", "llm": "llm", "offline": "rules"}[source]
        assert row["status"] == ("approved" if approve else "manual")
        assert bool(db.list_pending_questions(con)) == (not approve)
        assert con.execute("SELECT count(*) FROM qa_answers").fetchone()[0] == int(approve or source == "cache")


def test_complex_stock_value_is_deferred_to_shared_review(page, monkeypatch):
    label = "Describe a project you are proud of"
    page.set_content(f'<label for="project">{label}</label><textarea id="project"></textarea>')
    monkeypatch.setattr(A, "_match_dropdown_answer", lambda *_: "I built Python tools for secure code review at Cedar Labs.")
    assert not A.fill_field(page.locator("#project"), CFG)
    assert page.locator("#project").input_value() == ""
