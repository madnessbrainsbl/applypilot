import threading

import pytest
import autopilot as A
import db


CV = 'Application Security Engineer, 5 years experience. Education: Doctor of Veterinary Medicine, University, 2011-2016.'
CFG = {'profile': {}, 'apply': {}, 'active_persona': 'appsec'}
JOB = {'id': 'fixture', 'company': 'Fixture', 'title': 'Engineer', 'url': 'https://example.invalid/job'}


@pytest.mark.parametrize('requirement', ['TS with SCI eligibility required', 'TS/SCI eligibility required', 'TS/SCI clearance required', 'Active Top Secret Required', 'Active Top-Secret security clearance is required'])
def test_required_clearance_skips_before_browser_without_ats_api(monkeypatch, requirement):
    import ats_schema
    saved = []
    monkeypatch.setattr(ats_schema, 'fetch_schema', lambda *_: None)
    monkeypatch.setattr(db, 'connect', lambda: None)
    monkeypatch.setattr(db, 'insert_application', lambda con, rec: saved.append(rec))
    monkeypatch.setattr(A, '_mark_step', lambda *_: None)
    monkeypatch.setattr(A, 'launch_browser', lambda *_: pytest.fail('Ineligible posting opened a browser'))
    monkeypatch.setattr(A, 'submit_block_reason', lambda *a: None)   # stub cfg has no persona CV
    result = A.apply_one({**JOB, 'title': 'Security Engineer - ' + requirement, 'cv': 'fixture.pdf'},
                         {'profile': {}, 'apply': {}, 'active_persona': 'appsec'}, False, True, False)
    assert not result['submitted'] and result['filled_fields'] == 0
    assert requirement.lower() in result['manual_reason'] and saved == [result]


def test_clearance_preference_is_not_a_required_gate(page):
    page.set_content('<p>TS/SCI eligibility preferred, not required.</p>')
    assert A.hard_disqualifier(page) is None
    page.set_content('<p>TS with SCI eligibility required</p>')
    assert A.hard_disqualifier(page) == 'ts with sci eligibility required'
    assert A._required_clearance('No Top Secret required.') is None
    assert A._required_clearance('Active Top Secret preferred, not required.') is None


def test_lever_wrapped_radio_options_reach_validated_fallback(page, monkeypatch):
    questions = [
        ('What level of DOD clearance do you have?', ['Secret', 'Top Secret SCI', 'N/A'], 'N/A'),
        ('Are you a current employee of Fixture?', ['Yes', 'No'], 'No'),
        ('How did you hear about us?', ['Referral', 'Company Website', 'Job Board', 'Other'], 'Job Board'),
        ('Do you have a higher education degree?', ['Yes', 'No'], 'Yes'),
        ('Do you have any active industry related certifications?', ['Yes', 'No'], 'No'),
        ('Are you a current or former U.S. Government employee or U.S. armed forces?', ['Yes', 'No'], 'No'),
    ]
    page.set_content(''.join(
        '<li class="application-question custom-question"><div><div class="application-label"><div class="text">'+label+'</div></div>'
        + '<div class="application-field"><ul>'+''.join(
            f'<li><label><input type="radio" name="cards[{n}][field0]" value="{option}" required>{option}</label></li>'
            for option in options)+'</ul></div></div></li>' for n, (label, options, _) in enumerate(questions)))
    cfg = {'profile': {}, 'apply': {'dropdown_answers': {label: expected for label, _, expected in questions}}}
    asked = []
    expected = {label: answer for label, _, answer in questions}
    def answer(job, qs, cfg):
        asked.extend(qs)
        for q in qs:
            assert q['options'] == next(opts for label, opts, _ in questions if label == q['label'])
        return {q['label']: expected[q['label']] for q in qs}
    monkeypatch.setattr(A, 'ask_llm_answers', answer)
    A.fill_radio_groups(page, cfg)
    assert page.locator('input[name="cards[0][field0]"]:checked').input_value() == 'N/A'
    A.llm_fill(page, cfg, JOB)
    for n, (_, _, expected_answer) in enumerate(questions):
        assert page.locator(f'input[name="cards[{n}][field0]"]:checked').input_value() == expected_answer
    assert page.locator('input[name="cards[2][field0]"]:checked').input_value() == 'Job Board'
    assert not A.required_field_issues(page)
    assert A.fill_radio_groups(page, cfg) == 0


@pytest.mark.parametrize('previous,expected', [('No', 'N/A'), ('Yes', 'I worked on the fixture project.')])
def test_required_followup_distinguishes_condition_from_na_instruction(page, monkeypatch, previous, expected):
    label = 'If yes, please briefly describe. If no, please respond with "N/A".'
    page.set_content('<li class="application-question"><div class="application-label">Worked on the fixture?</div>'
                     '<label><input type="radio" name="trigger" value="Yes">Yes</label>'
                     '<label><input type="radio" name="trigger" value="No">No</label></li>'
                     f'<li class="application-question"><label for="details">{label}</label><textarea id="details" required></textarea></li>')
    page.locator(f'input[value="{previous}"]').check()
    asked = []
    def answer(job, qs, cfg):
        asked.extend(qs)
        return {q['label']: expected for q in qs}
    monkeypatch.setattr(A, 'ask_llm_answers', answer)
    A.llm_fill(page, CFG.copy(), JOB)
    assert page.locator('#details').input_value() == expected
    assert bool(asked) == (previous == 'Yes')
    if previous == 'Yes':
        assert 'previous question: "Worked on the fixture?"' in asked[0]['label']
    assert not A.required_field_issues(page)
    assert A._followup_triggered('If you selected "Other" or "Referral", describe.', 'Other')
    assert not A._followup_triggered('If you selected "Other" or "Referral", describe.', 'LinkedIn')


@pytest.mark.parametrize('previous,expected,asks_llm', [
    ('No ongoing outside employment.', 'N/A', False),
    ('I work on personal projects outside working hours.', 'Personal projects outside working hours.', True),
])
def test_required_followup_handles_narrative_previous_answer(page, monkeypatch, previous, expected, asks_llm):
    label = 'If yes, please briefly describe. If no, please respond with "N/A".'
    page.set_content('<li class="application-question"><div class="application-label">Outside employment?</div>'
                     f'<textarea>{previous}</textarea></li><li class="application-question"><label for="details">{label}</label>'
                     '<textarea id="details" required></textarea></li>')
    asked = []
    def answer(job, qs, cfg):
        asked.extend(qs)
        assert previous in qs[0]['label']
        return {q['label']: expected for q in qs}
    monkeypatch.setattr(A, 'ask_llm_answers', answer)
    A.llm_fill(page, CFG.copy(), JOB)
    assert page.locator('#details').input_value() == expected
    assert bool(asked) == asks_llm
    assert not A.required_field_issues(page)


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'jobs.db')
    monkeypatch.setattr(A, 'CONTROL', tmp_path / 'control.json')
    monkeypatch.setattr(A, '_QUIT_RUN', {'v': False})
    monkeypatch.setattr(A, '_STOP_EVENT', None)
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    monkeypatch.setattr(A._TL, 'llm_attempts', {}, raising=False)
    monkeypatch.setattr(A, '_answer_grounding', lambda *_: CV)
    monkeypatch.setattr(A, 'check_form_answers', lambda job, qs, answers, *args: answers)
    monkeypatch.setattr(A, 'llm_providers', lambda _: ['fixture'])
    with A.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        instance = browser.new_page()
        instance.set_default_timeout(1000)
        yield instance
        browser.close()


def test_optional_year_error_is_detected_and_not_filled_as_availability(page):
    page.set_content('<label for="year">Start date year</label><input id="year" type="number" min="1900" max="2100" value="11">')
    assert A.required_field_issues(page) == ['Start date year']
    assert not A.fill_field(page.locator('#year'), CFG)
    assert page.locator('#year').input_value() == '11'


def test_education_year_uses_cv_and_separate_qa_context(page, monkeypatch):
    page.set_content('<fieldset><legend>Education</legend><label for="year">Start date year</label><input id="year" type="number" min="1900" max="2100" value="11"></fieldset>')
    with db.connect() as con:
        db.save_qa_answer(con, A._qa_key('Start date year'), 'Start date year', '2025', 'Earlier employer')
    asked = []
    def answer(job, questions, cfg):
        asked.extend(q['label'] for q in questions)
        return {q['label']: '2011' for q in questions}
    monkeypatch.setattr(A, 'ask_llm_answers', answer)
    assert A.llm_fill(page, CFG.copy(), JOB) == 1
    assert asked == ['Education: Start date year']
    assert page.locator('#year').input_value() == '2011'
    assert A.required_field_issues(page) == []


def test_unsupported_answer_has_only_two_llm_attempts(page, monkeypatch):
    page.set_content('<label for="degree">Degree</label><select id="degree" required><option value="">Select</option><option>Doctorate Degree</option></select>')
    calls = []
    def answer(*args):
        calls.append(True)
        return {'Degree': 'Doctorate Degree'}
    monkeypatch.setattr(A, 'ask_llm_answers', answer)
    for _ in range(6):
        assert A.llm_fill(page, CFG.copy(), JOB) == 0
    assert len(calls) == 2
    assert page.locator('#degree').input_value() == ''
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM qa_answers').fetchone()[0] == 0
    # Changed options permit a fresh answer; a failed field must not poison the new field.
    page.locator('#degree').evaluate("e=>e.insertAdjacentHTML('beforeend','<option>Other</option>')")
    A.llm_fill(page, CFG.copy(), JOB)
    assert len(calls) == 3


def test_known_md_uses_other_without_claiming_a_doctorate(page, monkeypatch):
    cfg = {'profile': {'degree': 'Doctor of Veterinary Medicine (DVM)'}, 'apply': {}}
    assert A.offline_answer('Degree', ['Doctorate Degree', 'Other'], cfg) == 'Other'


def test_education_month_is_not_invented(page, monkeypatch):
    page.set_content('<fieldset><legend>Education</legend><label for="month">Start date month</label><select id="month"><option value="">Select</option><option>November</option></select></fieldset>')
    monkeypatch.setattr(A, 'ask_llm_answers', lambda job, qs, cfg: {q['label']: 'November' for q in qs})
    assert A.llm_fill(page, CFG.copy(), JOB) == 0
    assert page.locator('#month').input_value() == ''


def test_historical_dates_reject_availability_year_and_allow_cv_year():
    question = {'label': 'Education: Start date year'}
    assert A._unsupported_claim(question, '2026', CV) == 'historical year not in CV'
    assert A._unsupported_claim(question, '2011', CV) is None
    assert A._unsupported_claim(question, '2016', CV) == 'historical year does not match CV date range'
    assert A._unsupported_claim({'label': 'Education: End date year'}, '2016', CV) is None
    assert A._unsupported_claim(question, '2011-2016', CV) == 'historical year not in CV'


def test_quit_during_mail_lookup_does_not_enter_code(page, monkeypatch):
    page.set_content('<p>Enter the security code</p><input id="security-input" name="code">')
    stop = threading.Event()
    monkeypatch.setattr(A, '_STOP_EVENT', stop)
    monkeypatch.setattr(A, '_has_code_input', lambda _: True)
    def fetch(*args, **kwargs):
        stop.set()
        return '123456'
    monkeypatch.setattr(A, '_imap_fetch_code', fetch)
    assert not A.try_email_code(page, {'email': {'app_password': 'fixture'}}, JOB)
    assert page.locator('input').input_value() == ''


def test_quit_blocks_submit_and_fallback_click(page, monkeypatch):
    page.set_content('<button onclick="this.dataset.sent=(Number(this.dataset.sent)||0)+1">Submit</button>')
    stop = threading.Event()
    monkeypatch.setattr(A, '_STOP_EVENT', stop)
    stop.set()
    assert not A._submit_if_running(page, page.locator('button'))
    assert page.locator('button').get_attribute('data-sent') is None
    stop.clear()
    import stealth_mouse
    def interrupted(*args, **kwargs):
        stop.set()
        raise RuntimeError('interrupted')
    monkeypatch.setattr(stealth_mouse, 'human_click', interrupted)
    assert not A._submit_if_running(page, page.locator('button'))
    assert page.locator('button').get_attribute('data-sent') is None


def test_quit_while_moving_to_submit_does_not_click(page, monkeypatch):
    page.set_content('<button onclick="this.dataset.sent=1">Submit</button>')
    stop = threading.Event()
    monkeypatch.setattr(A, '_STOP_EVENT', stop)
    import stealth_mouse
    monkeypatch.setattr(stealth_mouse, 'human_move', lambda *args: stop.set())
    assert not A._submit_if_running(page, page.locator('button'))
    assert page.locator('button').get_attribute('data-sent') is None


def test_submit_click_error_is_not_retried(page, monkeypatch):
    page.set_content('<button onclick="this.dataset.sent=(Number(this.dataset.sent)||0)+1">Submit</button>')
    import stealth_mouse
    def dispatched_then_failed(page, button, **kwargs):
        button.click()
        raise RuntimeError('response unavailable')
    monkeypatch.setattr(stealth_mouse, 'human_click', dispatched_then_failed)
    assert not A._submit_if_running(page, page.locator('button'))
    assert page.locator('button').get_attribute('data-sent') == '1'


def test_running_submit_still_clicks_once(page):
    page.set_content('<button onclick="this.dataset.sent=(Number(this.dataset.sent)||0)+1">Submit</button>')
    assert A._submit_if_running(page, page.locator('button'))
    assert page.locator('button').get_attribute('data-sent') == '1'
