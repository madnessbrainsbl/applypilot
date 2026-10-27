import base64
from contextlib import nullcontext
from pathlib import Path

import pytest
import autopilot as A
import ats_schema
import db


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'jobs.db')
    monkeypatch.setattr(A, 'ROOT', tmp_path)
    monkeypatch.setattr(A, 'SHOTS', tmp_path / 'screenshots')
    monkeypatch.setattr(A, 'CONTROL', tmp_path / 'control.json')
    monkeypatch.setattr(A, '_QUIT_RUN', {'v': False})
    monkeypatch.setattr(A, '_STOP_EVENT', None)
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    monkeypatch.setattr(A._TL, 'select_attempts', {}, raising=False)
    with A.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        p = browser.new_page()
        p.set_default_timeout(1000)
        yield p
        browser.close()


def test_dropdown_unmatched_text_retries_are_bounded(page, monkeypatch):
    page.set_content('<label>Country<div class="select__control"><input></div></label>')
    cfg = {'profile': {}, 'apply': {'dropdown_answers': {'country': 'Russia'}}}
    monkeypatch.setattr(A, '_pick_option', lambda *a: False)
    monkeypatch.setattr(A, '_enter_on_highlighted', lambda *a: False)
    monkeypatch.setattr(A, '_type_into_select', lambda p, c, a, **kw: c.locator('input').fill(a))
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *a: None)
    for _ in range(8):
        assert A.fill_react_selects(page, page, cfg) == 0
    assert list(A._TL.select_attempts.values()) == [2]
    assert page.locator('input').input_value() == ''
    page.locator('.select__control').evaluate("e=>e.insertAdjacentHTML('beforeend','<span class=select__single-value>Russia</span>')")
    assert A.fill_react_selects(page, page, cfg) == 0
    assert page.locator('.select__single-value').inner_text() == 'Russia'


def test_bot_wall_records_once_then_returns(page, tmp_path, monkeypatch):
    url = 'https://jobs.lever.co/isolated/00000000-0000-0000-0000-000000000000'
    page.route('https://jobs.lever.co/**', lambda r: r.fulfill(body='<html>access denied</html>', content_type='text/html'))
    ctx = page.context
    monkeypatch.setattr(ctx, 'new_page', lambda: page)
    monkeypatch.setattr(A, 'launch_browser', lambda *a, **kw: ctx.browser)
    monkeypatch.setattr(A, 'submit_block_reason', lambda *a: None)   # stub cfg has no persona CV
    monkeypatch.setattr(A, 'sync_playwright', lambda: nullcontext(None))
    monkeypatch.setattr(A, 'new_browser_context', lambda *a, **kw: ctx)
    monkeypatch.setattr(A, 'close_browser', lambda *a: None)
    monkeypatch.setattr(A, '_upload_copy', lambda *a: '')
    monkeypatch.setattr(ats_schema, 'fetch_schema', lambda *a: None)
    monkeypatch.setattr(ats_schema, 'knockout', lambda *a: None)
    monkeypatch.setattr(A, 'click_apply', lambda *a, **kw: None)
    monkeypatch.setattr(A, 'wait_for_form', lambda *a, **kw: False)
    monkeypatch.setattr(A, 'is_dead_listing', lambda *a: False)
    monkeypatch.setattr(A, '_bounced_off_listing', lambda *a: False)
    monkeypatch.setattr(A, 'is_bot_wall', lambda *a: True)
    monkeypatch.setattr(A, 'fill_form', lambda *a: pytest.fail('processing continued past bot wall'))
    cfg = {'profile': {}, 'apply': {}, 'cv_path': 'appsec.pdf'}
    job = {'id': 'wall', 'url': url, 'company': 'Test', 'title': 'AppSec Engineer', 'cv': 'appsec.pdf', 'persona': 'appsec'}
    with db.connect() as con:
        con.execute("INSERT INTO jobs(id,persona) VALUES('wall','appsec')")
        con.commit()
    result = A.apply_one(job, cfg, False, True, False)
    assert result['needs_manual'] == 1
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM applications').fetchone()[0] == 1


@pytest.mark.parametrize('acknowledged', [True, False])
def test_captcha_callback_never_causes_a_second_application_send(page, monkeypatch, acknowledged):
    from types import SimpleNamespace
    import sys
    url = 'https://jobs.lever.co/isolated/00000000-0000-0000-0000-000000000001/apply'
    html = '<html><body><input><button onclick="document.body.dataset.sent=(Number(document.body.dataset.sent)||0)+1">Submit application</button></body></html>'
    page.route('https://jobs.lever.co/**', lambda r: r.fulfill(body=html, content_type='text/html'))
    ctx = page.context
    monkeypatch.setattr(ctx, 'new_page', lambda: page)
    monkeypatch.setattr(A, 'launch_browser', lambda *a, **kw: ctx.browser)
    monkeypatch.setattr(A, 'submit_block_reason', lambda *a: None)   # stub cfg has no persona CV
    monkeypatch.setattr(A, 'sync_playwright', lambda: nullcontext(None))
    monkeypatch.setattr(A, 'new_browser_context', lambda *a, **kw: ctx)
    monkeypatch.setattr(A, 'close_browser', lambda *a: None)
    monkeypatch.setattr(A, '_upload_copy', lambda *a: '')
    monkeypatch.setattr(ats_schema, 'fetch_schema', lambda *a: None)
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *a: None)
    monkeypatch.setattr(A.time, 'sleep', lambda *a: None)
    for name in ('is_dead_listing', '_bounced_off_listing', 'is_bot_wall', 'is_spam_blocked', 'has_captcha', '_has_code_input'):
        monkeypatch.setattr(A, name, lambda *a: False)
    for name in ('fill_form', 'fill_location', 'fill_institution', 'fill_all_react_selects', 'llm_fill'):
        monkeypatch.setattr(A, name, lambda *a, **kw: 5)
    monkeypatch.setattr(A, '_fill_identity', lambda *a, **kw: 0)
    monkeypatch.setattr(A, 'required_field_issues', lambda *a: [])
    monkeypatch.setattr(A, '_submit_if_running', lambda p, button, cfg=None: button.click() or True)
    def solve(p):
        if p.locator('body').get_attribute('data-sent') != '1':
            return False
        if acknowledged:
            p.locator('body').evaluate("e=>e.insertAdjacentHTML('beforeend','<p>Your application has been received.</p>')")
        return True
    monkeypatch.setattr(A, 'try_solve_turnstile_or_captcha', solve)
    monkeypatch.setitem(sys.modules, 'recruiter_outreach', SimpleNamespace(generate_outreach=lambda *a, **kw: None))
    monkeypatch.setenv('AUTOPILOT_FORCE_IDS', 'fixture')
    cfg = {'profile': {'first_name': 'Test', 'last_name': 'Candidate', 'full_name': 'Test Candidate'},
           'apply': {}, 'cv_path': 'fixture.pdf'}
    job = {'id': 'fixture', 'url': url, 'company': 'Fixture', 'title': 'AppSec Engineer', 'cv': 'fixture.pdf', 'persona': 'appsec'}
    with db.connect() as con:
        con.execute("INSERT INTO jobs(id,persona) VALUES('fixture','appsec')")
        con.commit()
    result = A.apply_one(job, cfg, False, True, False)
    assert page.locator('body').get_attribute('data-sent') == '1'
    assert result['submitted'] == acknowledged
    assert bool(result['confirmation']) == acknowledged
    assert result['needs_manual'] == int(not acknowledged)


@pytest.mark.parametrize('hidden', ['opacity:0', 'visibility:hidden', 'display:none'])
def test_ready_image_ignores_hidden_spinner_and_hidden_empty_img(page, hidden):
    png = (Path(__file__).parent / 'fixtures/captcha-animals.png').read_bytes()
    image = base64.b64encode(png).decode()
    page.set_content(f'<div class="challenge-view" style="width:380px;height:500px;background-image:url(data:image/png;base64,{image})"></div>'
                     f'<div style="{hidden}"><div class="spinner" style="width:50px;height:50px"></div><div class="task-image"><img src="missing.png" width=50 height=50></div></div>')
    assert A._hcaptcha_wait_ready(page, timeout=.7)


def test_visible_spinner_and_blank_challenge_remain_unready(page):
    page.set_content('<div class="challenge-view" style="width:380px;height:500px;background:#eee"></div><div class="spinner" style="width:50px;height:50px"></div>')
    assert not A._hcaptcha_wait_ready(page, timeout=.6)
    page.locator('.spinner').evaluate('e=>e.remove()')
    assert not A._hcaptcha_wait_ready(page, timeout=.6)


def test_readiness_error_is_not_reported_as_loading(page, monkeypatch, capsys):
    def fail(*a):
        raise RuntimeError('isolated DOM failure')
    monkeypatch.setattr(page, 'evaluate', fail)
    assert not A._hcaptcha_wait_ready(page, timeout=.1)
    assert 'readiness check failed: RuntimeError: isolated DOM failure' in capsys.readouterr().err
    assert not A._hcaptcha_image_ready(b'')
