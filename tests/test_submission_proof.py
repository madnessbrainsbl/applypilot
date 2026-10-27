import json
from pathlib import Path

import pytest
import autopilot as A
import db


@pytest.mark.parametrize('body', [
    'We received your application. If you are not selected for this position, check back.',
    'Thank you for your application! If your application is a good fit, we will contact you to schedule a call.',
])
def test_conditional_process_is_only_a_receipt(body):
    assert A.classify_email('Thank you for applying', body) == 'received'


def test_actual_rejection_overrides_acknowledgement():
    assert A.classify_email('Thank you for applying', 'Unfortunately we have decided to move forward with other candidates.') == 'rejected'


def test_persona_full_name_does_not_inherit_other_candidate():
    cfg = {'profile': {'first_name': 'Jane', 'last_name': 'Doe', 'full_name': 'Jane Doe'},
           'personas': {'creative': {'profile': {'first_name': 'Alex', 'last_name': 'Taylor'}}}}
    assert A.get_persona_cfg(cfg, 'creative')['profile']['full_name'] == 'Alex Taylor'
    cfg['personas']['creative']['profile']['full_name'] = 'Alex T.'
    assert A.get_persona_cfg(cfg, 'creative')['profile']['full_name'] == 'Alex T.'


@pytest.mark.parametrize('persona,first,last', [('appsec', 'Jane', 'Doe'), ('creative', 'Mark', 'Taylor')])
def test_persona_replaces_other_candidate_before_submit(page, monkeypatch, persona, first, last):
    cfg = {'profile': {'first_name': first, 'last_name': last, 'full_name': f'{first} {last}',
                       'email': 'shared@example.com'}, 'active_persona': persona}
    page.set_content('''<input autocomplete="given-name" value="Wrong"><input autocomplete="family-name" value="Other">
        <input name="name" value="Wrong Other"><input type="email" value="wrong@example.com">
        <label>Company name<input id="company" value="Preserve company"></label>
        <input name="name" value="Hidden trap" style="display:none">
        <button onclick="window.sent=true">Submit</button>''')
    monkeypatch.setattr('stealth_mouse.human_click', lambda p, el, **kwargs: el.click() or True)
    assert A._submit_if_running(page, page.locator('button'), cfg)
    assert page.locator('[autocomplete="given-name"]').input_value() == first
    assert page.locator('[autocomplete="family-name"]').input_value() == last
    assert page.locator('input[name=name]').first.input_value() == f'{first} {last}'
    assert page.locator('[type=email]').input_value() == 'shared@example.com'
    assert page.locator('#company').input_value() == 'Preserve company'
    assert page.locator('input[name=name]').last.input_value() == 'Hidden trap'


def test_identity_mismatch_blocks_dispatch(page, monkeypatch):
    cfg = {'profile': {'first_name': 'Alex', 'last_name': 'Taylor', 'full_name': 'Alex Taylor',
                       'email': 'shared@example.com'}}
    page.set_content('<input name="name" value="Jane Doe" readonly><button>Submit</button>')
    clicked = []
    monkeypatch.setattr('stealth_mouse.human_click', lambda *args, **kwargs: clicked.append(True))
    monkeypatch.setattr(A._TL, 'identity_error', None, raising=False)
    assert not A._submit_if_running(page, page.locator('button'), cfg)
    assert not clicked
    assert A._TL.identity_error == 'candidate identity check failed; submission blocked'


def test_persona_browser_settings_are_isolated_and_idempotent():
    cfg = {'browser': {'engine': 'camoufox', 'user_data_dir': 'shared', 'storage_state': 'shared.json'},
           'personas': {p: {'browser': {'user_data_dir': p, 'storage_state': p+'.json'}} for p in ['appsec','creative']}}
    creative = A.get_persona_cfg(cfg, 'creative')
    appsec = A.get_persona_cfg(creative, 'appsec')
    assert creative['browser'] == {'engine': 'camoufox', 'user_data_dir': 'creative', 'storage_state': 'creative.json'}
    assert appsec['browser'] == {'engine': 'camoufox', 'user_data_dir': 'appsec', 'storage_state': 'appsec.json'}
    assert A.get_persona_cfg(creative, 'creative')['browser'] == creative['browser']
    assert cfg['browser']['user_data_dir'] == 'shared'


def test_scrolled_identity_fields_are_not_honeypots(page):
    cfg = {'profile': {'first_name': 'Alex', 'last_name': 'Taylor', 'full_name': 'Alex Taylor',
                       'email': 'shared@example.com'}}
    page.set_content('<input name="first_name" value="Jane"><input name="last_name">'
                     '<input type="email"><div style="height:3000px"></div>')
    page.evaluate('window.scrollTo(0,2000)')
    assert page.locator('[name=first_name]').bounding_box()['y'] < -100
    assert not A.is_honeypot_element(page.locator('[name=first_name]'))
    A._fill_identity(page, cfg, strict=True)
    assert page.locator('[name=first_name]').input_value() == 'Alex'
    assert page.locator('[name=last_name]').input_value() == 'Taylor'
    assert page.locator('[type=email]').input_value() == 'shared@example.com'


def test_cv_is_not_uploaded_as_missing_cover(page, tmp_path, monkeypatch):
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    cv = tmp_path / 'own.pdf'
    cv.write_bytes(b'%PDF-1.4 fixture')
    cfg = {'profile': {}, 'apply': {'answers': {}}}
    page.set_content('<input type="file" name="resume"><input type="file" name="cover_letter">')
    A.fill_scope(page, cfg, str(cv), '')
    A.fill_scope(page, cfg, str(cv), '')
    assert page.locator('[name=resume]').evaluate('e=>e.files.length') == 1
    assert page.locator('[name=cover_letter]').evaluate('e=>e.files.length') == 0


def test_hidden_native_consent_updates_application_state(page, monkeypatch):
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    page.set_content('''<div id="application-state" data-consent="false"></div>
        <label>By checking this box, I consent to processing my application responses.
        <input type="checkbox" id="consent" required style="display:none"
            onclick="document.getElementById('application-state').dataset.consent=String(this.checked)"></label>''')
    A.fill_scope(page, {'profile': {}, 'apply': {}}, '', None)
    assert page.locator('#consent').is_checked()
    assert page.locator('#application-state').get_attribute('data-consent') == 'true'


@pytest.mark.parametrize('job', [
    {'location': 'Belgrade, Serbia', 'title': 'Engineer'},
    {'location': 'Remote', 'title': 'NIST security engineer'},
])
def test_job_location_never_overwrites_candidate_residence(page, monkeypatch, job):
    cfg = {'profile': {'location': 'Springfield, Russia', 'location_native': 'Спрингфилд'}, 'apply': {}}
    page.set_content('<label>Current location<input id="residence"></label>')
    assert A.fill_field(page.locator('input'), cfg, job=job)
    assert page.locator('input').input_value() == 'Springfield, Russia'
    calls = []
    monkeypatch.setattr(A, '_fill_autocomplete', lambda *args, **kwargs: calls.append((args, kwargs)) or 1)
    assert A.fill_location(page, cfg, job=job) == 1
    assert calls[0][0][3:5] == ('Springfield', 'Спрингфилд')
    assert calls[0][1]['fallback_value'] == 'Russia'


def test_plain_location_select_keeps_native_city(page, monkeypatch):
    cfg = {'profile': {'location': 'Springfield, Russia', 'location_native': 'Спрингфилд'}, 'apply': {}}
    page.set_content('''<label for="city">Location. *</label><div class="select__control">
        <input id="city" type="text"><div class="options"></div></div>''')
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    monkeypatch.setattr(A._TL, 'select_attempts', None, raising=False)
    monkeypatch.setattr(A, '_pick_option', lambda *args: False)
    queries = []
    def type_query(p, ctrl, text, **kwargs):
        queries.append(text)
    def pick_city(scope, query):
        scope.locator('.options').evaluate("e=>{e.className='select__single-value'; e.textContent='Спрингфилд, Россия'}")
        return True
    monkeypatch.setattr(A, '_type_into_select', type_query)
    monkeypatch.setattr(A, '_pick_city', pick_city)
    assert A.fill_react_selects(page, page, cfg) == 1
    assert queries == ['Спрингфилд']
    assert page.locator('.select__single-value').inner_text() == 'Спрингфилд, Россия'


@pytest.mark.parametrize('submitted', [False, True])
def test_watchdog_preserves_existing_durable_outcome(tmp_path, monkeypatch, submitted):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'jobs.db')
    con = db.connect()
    con.execute("INSERT INTO jobs(id,persona) VALUES ('durable-test','appsec')")
    con.commit()
    db.insert_application(con, {'id': 'durable-test', 'applied_at': A.now(),
        'submitted': submitted, 'needs_manual': not submitted,
        'manual_reason': None if submitted else 'existing manual',
        'confirmation': {'kind': 'ats_acknowledgement'} if submitted else None})
    before = [tuple(row) for row in con.execute('SELECT * FROM applications')]
    A._record_stall('durable-test', 'browser cleanup stalled')
    assert [tuple(row) for row in con.execute('SELECT * FROM applications')] == before
    con.close()


def test_watchdog_records_one_unconfirmed_result(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'jobs.db')
    con = db.connect()
    con.execute("INSERT INTO jobs(id,persona) VALUES ('stalled-test','appsec')")
    con.commit()
    A._record_stall('stalled-test', 'first stall')
    A._record_stall('stalled-test', 'second stall')
    rows = con.execute('SELECT needs_manual,manual_reason FROM applications').fetchall()
    assert [tuple(row) for row in rows] == [(1, 'first stall')]
    con.close()


def test_watchdog_preserves_old_dry_run_and_records_current_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'jobs.db')
    con = db.connect()
    con.execute("INSERT INTO jobs(id,persona) VALUES ('prior-dry-test','appsec')")
    con.commit()
    db.insert_application(con, {'id': 'prior-dry-test', 'applied_at': A.now(), 'dry_run': True})
    baseline = con.execute('SELECT max(attempt_id) FROM applications').fetchone()[0]
    assert A._record_stall('prior-dry-test', 'current stall', baseline=baseline) is True
    assert A._record_stall('prior-dry-test', 'duplicate stall', baseline=baseline) is False
    assert [tuple(r) for r in con.execute('SELECT dry_run,needs_manual,manual_reason FROM applications')] == [
        (1, 0, None), (0, 1, 'current stall')]
    con.close()


def test_join_date_uses_profile_notice_and_actual_range_options():
    cfg = {'profile': {'notice_period': '1 month'}, 'apply': {}}
    options = ['Immediately', '1 to 2 weeks', '3 to 4 weeks', '1 to 2 months']
    assert A.offline_answer('How soon can you join us ?', options, cfg) == '1 to 2 months'
    assert A.offline_answer('Notice period', ['Immediately', '1 to 2 weeks'], cfg) is None
    assert A.offline_answer('How soon can you join us ?', options, {'profile': {}, 'apply': {}}) is None


def test_company_research_does_not_invent_sources():
    label = 'How well do you know us? Please select all sources/tools you have used to research our company.'
    cfg = {'profile': {}, 'apply': {}}
    assert A.offline_answer(label, ['Indeed', 'Careers Blog', 'Other'], cfg) == 'Other'
    assert A.offline_answer(label, ['Indeed', 'Careers Blog'], cfg) is None


def test_native_company_research_checks_only_other(page, monkeypatch):
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    page.set_content('''<fieldset><legend>How well do you know us? Sources/tools used to research our company.</legend>
        <label>Indeed<input type="checkbox" name="sources[]" value="indeed"></label>
        <label>Careers Blog<input type="checkbox" name="sources[]" value="blog"></label>
        <label>Other<input type="checkbox" name="sources[]" value="other"></label></fieldset>''')
    A.fill_scope(page, {'profile': {}, 'apply': {}}, '', None)
    assert page.locator('[value=other]').is_checked()
    assert not page.locator('[value=indeed]').is_checked()
    assert not page.locator('[value=blog]').is_checked()


@pytest.mark.parametrize('supporting_first', [True, False])
def test_cv_is_never_uploaded_to_certificates_or_portfolio(page, tmp_path, monkeypatch, supporting_first):
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    cv = tmp_path / 'own.pdf'
    cv.write_bytes(b'%PDF-1.4 fixture')
    resume = '<label>Resume/CV<input type="file" name="resume"></label>'
    supporting = ('<label>Upload your certificates<input type="file" name="certificate_upload"></label>'
                  '<label>Portfolio<input type="file" name="portfolio"></label>')
    page.set_content(supporting + resume if supporting_first else resume + supporting)
    A.fill_scope(page, {'profile': {}, 'apply': {'answers': {}}}, str(cv), None)
    assert page.locator('[name=resume]').evaluate('e=>e.files.length') == 1
    assert page.locator('[name=certificate_upload]').evaluate('e=>e.files.length') == 0
    assert page.locator('[name=portfolio]').evaluate('e=>e.files.length') == 0


def test_non_referral_followup_is_not_filled_as_source(page):
    cfg = {'profile': {}, 'apply': {'answers': {}}}
    label = 'If "Employee Referral" was selected for "How did you hear about this job?" please type the full name of the referring employee. If not, please type in "N/A".'
    page.set_content(f'<label for="referrer">{label}</label><input id="referrer">')
    assert A.fill_field(page.locator('#referrer'), cfg)
    assert page.locator('#referrer').input_value() == 'N/A'


def test_ats_slug_matches_split_company_name():
    assert A._email_matches_job('Thank you for applying to Scale AI', 'no-reply@greenhouse.io', {'company': 'Scaleai'})
    assert A._company_tokens('2k') == ['2k']
    assert not A._email_matches_job('Thank you for applying to Scaleway', 'no-reply@greenhouse.io', {'company': 'Scaleai'})


def test_receipt_prefers_complete_brand_and_handles_careers_slug():
    jobs = [{'id': 'wpp', 'company': 'Wpp', 'title': 'Creative Designer'},
            {'id': 'media', 'company': 'Wppmedia', 'title': 'Multimedia Designer'},
            {'id': 'skild', 'company': 'Skildai-careers', 'title': 'Content Creator'}]
    assert A.match_mail_to_job({'subject': 'Thank you for applying to WPP Media!', 'from': 'no-reply@wppmedia.com'}, jobs)['id'] == 'media'
    assert A.match_mail_to_job({'subject': 'Thank you for applying to Skild AI', 'from': 'no-reply@greenhouse.io'}, jobs)['id'] == 'skild'
    assert A.match_mail_to_job({'subject': 'WPP Application Update', 'from': 'no-reply@greenhouse.io'}, jobs)['id'] == 'wpp'


def test_open_failed_challenge_is_solved_only_once(page, monkeypatch):
    calls = []
    monkeypatch.setattr(A, '_hcaptcha_verified', lambda p: False)
    monkeypatch.setattr(A, '_captcha_challenge_open', lambda p: True)
    monkeypatch.setattr(A, '_hcaptcha_challenge_frame', lambda p: object())
    monkeypatch.setattr(A, '_solve_hcaptcha_local', lambda p: calls.append(True) or False)
    assert not A.try_solve_turnstile_or_captcha(page)
    assert calls == [True]


def test_vision_result_after_stop_cannot_drive_captcha(page, monkeypatch):
    monkeypatch.setattr(A, 'llm_providers', lambda c: [{'name': 'test', 'kind': 'api'}])
    monkeypatch.setattr(A, '_vision_model_ids', lambda p: ['fixture'])
    def reply(*args, **kwargs):
        A._QUIT_RUN['v'] = True
        return '{"coordinates":[{"box_2d":[0,0]}]}'
    monkeypatch.setattr(A, '_call_openai_llm', reply)
    assert A.llm_vision({}, '', '', []) == ''
    assert A._hcaptcha_click(page, page.locator('body')) is False


def test_drag_cancellation_releases_mouse(page, monkeypatch):
    actions = []
    class Mouse:
        def move(self, *args):
            actions.append('move')
            if actions.count('move') == 2:
                A._QUIT_RUN['v'] = True
        def down(self):
            actions.append('down')
        def up(self):
            actions.append('up')
    from types import SimpleNamespace
    monkeypatch.setattr(A.time, 'sleep', lambda *args: None)
    assert A._hcaptcha_perform_drags(SimpleNamespace(mouse=Mouse()), [(10, 10, 100, 100)]) == 0
    assert actions[-1] == 'up'


def test_spatial_solver_sees_reference_picture_without_changing_click_box(page, monkeypatch, tmp_path):
    import cv2
    import numpy as np
    page.set_content('<body style="margin:0;width:300px"><header style="height:100px;background:red">Reference picture</header>'
                     '<canvas class="challenge-view" width="300" height="200" style="display:block;background:blue"></canvas></body>')
    monkeypatch.setattr(A, '_HCAPTCHA_CACHE', tmp_path)
    def vision(cfg, system, user, images, **kwargs):
        assert len(images) == 3 and 'reference picture' in user
        raw, _, context = [cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR) for png in images]
        assert raw.shape[:2] == (200, 300)
        assert tuple(context[50, 200]) == (0, 0, 255)  # prompt image outside the canvas
        return '{"points":[{"x":150,"y":180}]}'
    monkeypatch.setattr(A, 'llm_vision', vision)
    monkeypatch.setattr(A.time, 'sleep', lambda *args: None)
    page.evaluate("() => { document.querySelector('canvas').onclick=e=>document.body.dataset.clicked=e.clientX+','+e.clientY; }")
    assert A._hcaptcha_click_spatial(page, page.main_frame, 'Tap the object in this place', 1) == 1
    assert page.locator('body').get_attribute('data-clicked') == '150,180'


def test_empty_vision_selection_uses_next_provider(page, monkeypatch, tmp_path):
    monkeypatch.setattr(A, 'read_json', lambda path: {})
    monkeypatch.setattr(A, '_HCAPTCHA_CACHE', tmp_path)
    monkeypatch.setattr(A, 'llm_providers', lambda cfg: [{'name': 'first'}, {'name': 'second'}])
    monkeypatch.setattr(A, '_vision_model_ids', lambda provider: ['fixture'])
    called = []
    def answer(provider, *args, **kwargs):
        called.append(provider['name'])
        return '{"points":[]}' if provider['name'] == 'first' else '{"points":[{"x":40,"y":80}]}'
    monkeypatch.setattr(A, '_call_openai_llm', answer)
    png = (Path(__file__).parent / 'fixtures/captcha-animals.png').read_bytes()
    assert A._hcaptcha_spatial_points(png, {'x':0,'y':0,'width':300,'height':300}, 'Tap the item', 1) == [(40, 80)]
    assert called == ['first', 'second']


def test_russian_challenge_is_not_an_empty_prompt():
    assert A._hcaptcha_prompt_from_text('Выберите животных, отвернувшихся от вас Попробуйте еще раз. Пропустить RU') == 'Выберите животных, отвернувшихся от вас'
    assert A._hcaptcha_is_drag('Переместите правильный сосуд в пустую ячейку')
    assert not A._hcaptcha_prompt_from_text('Пропустить\nПроверить\nRU')


@pytest.mark.parametrize('prompt', ['Wählen Sie alle Tiere aus', 'Sélectionnez les animaux'])
def test_scoped_prompt_does_not_require_english_verbs(prompt):
    assert A._hcaptcha_prompt_from_text(prompt, trusted=True) == prompt


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'jobs.db')
    monkeypatch.setattr(A, 'CONTROL', tmp_path / 'control.json')
    monkeypatch.setattr(A, '_QUIT_RUN', {'v': False})
    monkeypatch.setattr(A, '_STOP_EVENT', None)
    monkeypatch.setattr(A._TL, 'confirmation', None, raising=False)
    with A.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        instance = browser.new_page()
        yield instance
        browser.close()


@pytest.mark.parametrize('text,url', [
    ('', 'https://jobs.example.com/confirmation'),
    ('', 'https://jobs.example.com/apply?redirect=/thank-you'),
    ('Thank you for your interest!', 'https://jobs.example.com/thank-you'),
    ('Thank you for applying. Enter the verification code to complete your application.', ''),
    ('Your application has not been submitted.', ''),
    ('Your response has been recorded. Newsletter subscription successfully submitted.', ''),
])
def test_incomplete_application_is_not_success(text, url):
    assert not A._is_success(text, url)


def test_acknowledgement_is_saved_with_application(page):
    assert A._is_success('Your application was successfully submitted.', 'https://ats.example.com/thank-you')
    proof = dict(A._TL.confirmation)
    with db.connect() as con:
        con.execute("INSERT INTO jobs(id,title) VALUES('fixture','Engineer')")
        db.insert_application(con, {'id': 'fixture', 'applied_at': A.now(), 'submitted': True, 'confirmation': proof})
        row = con.execute('SELECT confirmation, submitted FROM applications').fetchone()
        assert json.loads(row['confirmation']) == proof
        assert row['submitted'] == 1
        con.execute('ALTER TABLE applications DROP COLUMN confirmation')
        con.commit()
    with db.connect() as con:
        row = con.execute('SELECT confirmation, submitted FROM applications').fetchone()
        assert row['confirmation'] is None
        assert row['submitted'] == 1  # migration preserves legacy history without inventing proof


def test_optional_year_without_native_constraints_is_invalid(page):
    page.set_content('<label for="year">Start date year</label><input id="year" type="number" value="11">')
    assert page.locator('input').evaluate('e=>e.checkValidity()')
    assert A.required_field_issues(page) == ['Start date year']


@pytest.mark.parametrize('kind,name', [('hcaptcha','h-captcha-response'), ('turnstile','cf-turnstile-response'), ('recaptcha','g-recaptcha-response')])
def test_each_captcha_requires_response_token(page, kind, name):
    page.set_content(f'<textarea name="{name}"></textarea>')
    assert not A._captcha_verified(page, kind)
    page.locator('textarea').fill('fixture-real-response-token-length')
    assert A._captcha_verified(page, kind)


def test_enterprise_open_challenge_is_not_solved_by_token(page):
    page.route('https://www.recaptcha.net/**', lambda r: r.fulfill(body='<p>Challenge</p>', content_type='text/html'))
    page.set_content('<textarea name="g-recaptcha-response">fixture-real-response-token-length</textarea>'
                     '<iframe src="https://www.recaptcha.net/recaptcha/enterprise/bframe"></iframe>')
    page.wait_for_timeout(100)
    assert A._captcha_challenge_open(page)
    assert not A._captcha_verified(page, 'recaptcha')
    page.locator('iframe').evaluate("e=>e.style.display='none'")
    assert A._captcha_verified(page, 'recaptcha')


def test_detection_does_not_click_and_turnstile_click_is_not_success(page, monkeypatch):
    page.route('https://challenges.cloudflare.com/**', lambda r: r.fulfill(body='<input type="checkbox">', content_type='text/html'))
    page.set_content('<input type="hidden" name="cf-turnstile-response"><iframe '
                     'src="https://challenges.cloudflare.com/turnstile/test"></iframe>')
    page.frame_locator('iframe').locator('input').wait_for(state='visible', timeout=3000)
    import stealth_mouse
    clicked = []
    monkeypatch.setattr(stealth_mouse, 'human_click_box', lambda *a: clicked.append(True))
    monkeypatch.setattr(A.time, 'sleep', lambda *a: None)
    assert A.has_captcha(page)
    assert not clicked
    assert not A.try_solve_turnstile_or_captcha(page)
    assert clicked
    assert not A.try_solve_turnstile(page, timeout_s=0.01)
