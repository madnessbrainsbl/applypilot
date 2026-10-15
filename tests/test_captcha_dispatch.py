"""Vendor routing and success evidence in isolated real browser DOMs."""
import pytest
import autopilot as A
from captcha_probe import captcha_request


@pytest.fixture(scope='module')
def browser():
    with A.sync_playwright() as pw:
        instance = pw.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser, monkeypatch, tmp_path):
    monkeypatch.setattr(A, 'CONTROL', tmp_path / 'control.json')
    monkeypatch.setattr(A, '_QUIT_RUN', {'v': False})
    monkeypatch.setattr(A, '_STOP_EVENT', None)
    context = browser.new_context()
    instance = context.new_page()
    yield instance
    context.close()


@pytest.mark.parametrize('kind,name', [('hcaptcha','h-captcha-response'), ('turnstile','cf-turnstile-response'), ('recaptcha','g-recaptcha-response')])
def test_automatic_token_without_iframe_is_recognized(page, monkeypatch, kind, name):
    page.set_content(f'<textarea name="{name}">isolated-response-token-long-enough</textarea>')
    monkeypatch.setattr(A, '_solve_hcaptcha_local', lambda p: pytest.fail('No challenge requires solving'))
    monkeypatch.setattr(A, 'try_solve_recaptcha_audio', lambda p: pytest.fail('No challenge requires audio'))
    assert A.try_solve_turnstile_or_captcha(page)
    page.locator('textarea').fill('')
    assert not A._captcha_verified(page, kind)


@pytest.mark.parametrize('variant', ['api2', 'enterprise'])
@pytest.mark.parametrize('token', [False, True])
def test_open_recaptcha_is_routed_to_audio_and_requires_token(page, monkeypatch, variant, token):
    page.route('https://www.recaptcha.net/**', lambda r: r.fulfill(body='<p>Challenge</p>', content_type='text/html'))
    page.set_content(f'<textarea name="g-recaptcha-response"></textarea><iframe src="https://www.recaptcha.net/recaptcha/{variant}/bframe"></iframe>')
    page.wait_for_timeout(100)
    assert A._captcha_challenge_open(page)
    calls = []
    def audio(p):
        calls.append('audio')
        p.locator('iframe').evaluate("e=>e.style.display='none'")
        if token:
            p.locator('textarea').fill('isolated-response-token-long-enough')
        return True
    monkeypatch.setattr(A, 'try_solve_recaptcha_audio', audio)
    monkeypatch.setattr(A, '_solve_hcaptcha_local', lambda p: pytest.fail('Wrong CAPTCHA vendor'))
    assert A.try_solve_turnstile_or_captcha(page) is token
    assert calls == ['audio']


def test_open_hcaptcha_is_routed_once_to_hcaptcha(page, monkeypatch):
    page.route('https://newassets.hcaptcha.com/**', lambda r: r.fulfill(body='<p>Challenge</p>', content_type='text/html'))
    page.set_content('<iframe src="https://newassets.hcaptcha.com/test#frame=challenge"></iframe>')
    page.wait_for_timeout(100)
    calls = []
    monkeypatch.setattr(A, '_solve_hcaptcha_local', lambda p: calls.append('hcaptcha') or False)
    monkeypatch.setattr(A, 'try_solve_recaptcha_audio', lambda p: pytest.fail('Wrong CAPTCHA vendor'))
    assert not A.try_solve_turnstile_or_captcha(page)
    assert calls == ['hcaptcha']


def test_stop_prevents_audio_and_automatic_token_acceptance(page, monkeypatch):
    page.set_content('<textarea name="g-recaptcha-response">isolated-response-token-long-enough</textarea>')
    A._QUIT_RUN['v'] = True
    assert not A.try_solve_recaptcha_audio(page)
    assert not A.try_solve_turnstile_or_captcha(page)


def test_turnstile_dummy_token_is_not_real_solve_proof(page):
    page.set_content('<input name="cf-turnstile-response" value="XXXX.DUMMY.TOKEN.XXXX">')
    assert not A._captcha_verified(page, 'turnstile')
    page.locator('input').fill('isolated-response-token-long-enough')
    assert A._captcha_verified(page, 'turnstile')


@pytest.mark.parametrize('url,allowed', [
    ('https://api.hcaptcha.com/getcaptcha', True),
    ('https://www.google.com/recaptcha/api2/reload', True),
    ('https://www.recaptcha.net/recaptcha/enterprise/reload', True),
    ('https://challenges.cloudflare.com/turnstile/v0/api.js', True),
    ('https://jobs.lever.co/zoox/apply', False),
    ('https://www.google.com/account', False),
    ('https://www.google.com/recaptcha/api2/demo', False),
    ('https://api.hcaptcha.com.evil.example/getcaptcha', False),
    ('http://api.hcaptcha.com/getcaptcha', False),
])
def test_probe_allows_only_captcha_vendor_writes(url, allowed):
    assert captcha_request(url) is allowed


def test_standard_turnstile_closed_shadow_checkbox(page, monkeypatch):
    html = '''<div id="host"></div><script>const s=document.querySelector('#host').attachShadow({mode:'closed'});
    s.innerHTML='<span role="checkbox" style="position:absolute;left:15px;top:15px;width:30px;height:30px"></span>';
    s.querySelector('span').onclick=()=>parent.postMessage('solved','*');</script>'''
    page.route('https://challenges.cloudflare.com/**', lambda r: r.fulfill(body=html, content_type='text/html'))
    page.set_content('''<input name="cf-turnstile-response"><iframe style="width:300px;height:65px;border:0" src="https://challenges.cloudflare.com/turnstile/test"></iframe>
    <script>addEventListener('message',e=>{if(e.data==='solved')document.querySelector('input').value='isolated-response-token-long-enough'})</script>''')
    page.wait_for_timeout(100)
    assert page.frames[-1].locator('input[type="checkbox"]').count() == 0
    import stealth_mouse
    monkeypatch.setattr(stealth_mouse, 'human_click_box', lambda p,b: p.mouse.click(b['x']+b['width']/2,b['y']+b['height']/2))
    assert A.try_solve_turnstile_or_captcha(page)
    assert A._captcha_verified(page, 'turnstile')


def test_recaptcha_waits_for_delayed_token_without_second_click(page, monkeypatch):
    page.route('https://www.google.com/**', lambda r: r.fulfill(body='<div id="recaptcha-anchor" style="width:28px;height:28px">Checkbox</div>', content_type='text/html'))
    page.set_content('<textarea name="g-recaptcha-response"></textarea><iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>')
    page.wait_for_timeout(100)
    clock = [0.0]
    monkeypatch.setattr(A.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(A.time, 'sleep', lambda n: clock.__setitem__(0,clock[0]+n))
    original = A._captcha_verified
    def verified(p, kind):
        if clock[0] >= 5:
            p.locator('textarea').fill('isolated-response-token-long-enough')
        return original(p, kind)
    monkeypatch.setattr(A, '_captcha_verified', verified)
    monkeypatch.setattr(A, 'try_solve_recaptcha_audio', lambda p: pytest.fail('No visible challenge'))
    import stealth_mouse
    clicks = []
    monkeypatch.setattr(stealth_mouse, 'human_click_box', lambda *args: clicks.append(True))
    assert A.try_solve_turnstile_or_captcha(page)
    assert clock[0] >= 5 and clicks == [True]


def test_recaptcha_multiple_audio_rounds_require_final_token(page, monkeypatch):
    import io
    html = '''<p>Multiple correct solutions required - please solve more.</p>
    <a class="rc-audiochallenge-tdownload-link" href="https://www.google.com/recaptcha/audio/1">Download</a>
    <input id="audio-response"><button id="recaptcha-verify-button" onclick="next()">Verify</button>
    <script>let round=1;function next(){if(document.querySelector('input').value!=='answer')return;
    if(++round<=3)document.querySelector('a').href='https://www.google.com/recaptcha/audio/'+round;
    else parent.postMessage('solved','*');}</script>'''
    page.route('https://www.google.com/**', lambda r: r.fulfill(body=html, content_type='text/html'))
    page.set_content('''<textarea name="g-recaptcha-response"></textarea><iframe src="https://www.google.com/recaptcha/api2/bframe"></iframe>
    <script>addEventListener('message',e=>{if(e.data==='solved'){document.querySelector('textarea').value='isolated-response-token-long-enough';document.querySelector('iframe').style.display='none'}})</script>''')
    page.wait_for_timeout(100)
    downloaded = []
    def download(request, **kwargs):
        downloaded.append(request.full_url)
        return io.BytesIO(b'isolated audio')
    monkeypatch.setattr(A.urllib.request, 'urlopen', download)
    monkeypatch.setattr(A, '_transcribe_audio_bytes', lambda b: 'answer')
    assert A.try_solve_recaptcha_audio(page)
    assert downloaded == ['https://www.google.com/recaptcha/audio/'+str(i) for i in range(1,4)]
    assert A._captcha_verified(page, 'recaptcha')
