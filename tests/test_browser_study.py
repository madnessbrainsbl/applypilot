import json

import pytest

import autopilot as A
import browser_study as S


URL = "https://careers.example.invalid/jobs/security-engineer"
TARGET = URL + "/application"
CFG = {"llm": {"enabled": True}}


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        instance = pw.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    instance = context.new_page()
    instance.route("https://**/*", lambda route: route.fulfill(
        body="<h1>Security engineer</h1>", content_type="text/html"))
    instance.goto(URL)
    yield instance
    context.close()


def choose(page, reply='{"id": 0}'):
    return S.choose_apply_url(page, CFG, lambda *args, **kwargs: reply)


def test_study_uses_only_observed_links_and_no_candidate_data(page):
    page.set_content(f'''<h1>Security engineer</h1>
        <a href="{TARGET}?campaign=public" onclick="document.body.dataset.clicked='yes'">Start your journey</a>
        <p>Private unrelated page content</p><script>window.secret='not for the model'</script>
        <input type="hidden" value="private candidate data">''')
    prompts = []
    def complete(cfg, system, user, **kwargs):
        assert kwargs == {"max_tokens": 80, "budget": 12}
        prompts.append(json.loads(user))
        return '{"id":0}'
    assert S.choose_apply_url(page, CFG, complete) == TARGET + "?campaign=public"
    assert prompts == [{"heading": "Security engineer", "links": [{"id": 0,
        "label": "Start your journey", "host": "careers.example.invalid",
        "path": "/jobs/security-engineer/application"}]}]
    assert page.url == URL
    assert page.locator("body").get_attribute("data-clicked") is None
    assert S.choose_apply_url(page, CFG, complete) is None
    assert len(prompts) == 1, "one bounded study per page URL"


@pytest.mark.parametrize("target", [
    "javascript:alert(1)", "https://other.example.invalid/apply/123",
    "https://jobs.lever.co.evil.invalid/company/123", "https://jobs.lever.co/company",
    "https://127.0.0.1/apply/123", "http://localhost:3737/apply",
    "https://user:password@jobs.lever.co/company/123",
    "https://jobs.lever.co:9222/company/123",
    URL + "/application/submit", URL + "/application?redirect=https://evil.invalid",
    URL + "/application?%2574oken=private", URL + "/application?do=delete",
    "https://careers.example.invalid/jobs/another-vacancy/application",
    URL + "/login", "data:text/html,apply",
])
def test_unsafe_targets_never_reach_model(page, target):
    page.set_content(f'<a href="{target}">Start your journey</a>')
    assert S.choose_apply_url(page, CFG, lambda *a, **kw: pytest.fail("unsafe link offered")) is None


@pytest.mark.parametrize("reply", ['{"id":true}', '{"id":-1}', '{"id":99}',
    '{"id":"0"}', '{"id":null}', '[0]', 'not json', '{"id":0,"script":"send()"}'])
def test_untrusted_model_output_cannot_add_actions(page, reply):
    page.set_content(f'<a href="{TARGET}">Start your journey</a>')
    assert choose(page, reply) is None
    assert page.url == URL


@pytest.mark.parametrize("reply", ['```json\n{"id": 0}\n```', '```JSON\n{"id": 0}\n```\n',
                                  '```\n{"id": 0}\n```'])
def test_valid_provider_json_in_markdown_is_accepted(page, reply):
    page.set_content(f'<a href="{TARGET}">Start your journey</a>')
    assert choose(page, reply) == TARGET


def test_markdown_does_not_allow_extra_model_actions(page):
    page.set_content(f'<a href="{TARGET}">Start your journey</a>')
    assert choose(page, '```json\n{"id":0,"script":"send()"}\n```') is None


@pytest.mark.parametrize("html", [
    '<input value="candidate@example.invalid">', '<textarea>Private answer</textarea>',
    '<select><option>Russia</option></select>', '<div contenteditable="true">draft</div>',
    '<div contenteditable>draft</div>', '<div role="textbox">draft</div>',
    '<iframe srcdoc="&lt;input value=private&gt;"></iframe>',
])
def test_existing_fields_stop_study_before_model(page, html):
    page.set_content(html + f'<a href="{TARGET}">Start your journey</a>')
    assert S.choose_apply_url(page, CFG, lambda *a, **kw: pytest.fail("existing form")) is None


@pytest.mark.parametrize("mutation", [
    "document.querySelector('a').remove()",
    "document.querySelector('a').href='/application/other'",
    "document.querySelector('a').textContent='Different action'",
    "document.querySelector('a').setAttribute('download','yes')",
    "document.body.insertAdjacentHTML('beforeend','<input value=private>')",
])
def test_changed_dom_invalidates_proposal(page, mutation):
    page.set_content(f'<a href="{TARGET}">Start your journey</a>')
    def complete(*a, **kw):
        page.evaluate(mutation)
        return '{"id":0}'
    assert S.choose_apply_url(page, CFG, complete) is None


def test_ambiguous_destinations_are_not_guessed(page):
    page.set_content(f'<a href="{TARGET}">First form</a><a href="{URL}/apply">Second form</a>')
    assert S.choose_apply_url(page, CFG, lambda *a, **kw: pytest.fail("ambiguous jobs")) is None


def test_vacancy_id_is_a_whole_path_or_query_value():
    source = "https://careers.example.invalid/jobs/123"
    assert S._safe_link(source, "https://careers.example.invalid/apply/123")
    assert S._safe_link(source, "https://careers.example.invalid/application?job=123")
    assert not S._safe_link(source, "https://careers.example.invalid/jobs/12345/apply")
    assert not S._safe_link(source, "https://careers.example.invalid/apply?job=12345")
    assert not S._safe_link("https://jobs.lever.co/fixture/123", "https://jobs.lever.co/fixture/123#application")


def test_workable_overview_is_not_a_second_application_destination(page):
    source = "https://apply.workable.com/pact-coffee/j/38E974649E"
    page.goto(source)
    page.set_content(f'<h1>Social Media &amp; Content Creator</h1><a href="{source}/">OVERVIEW</a>'
                     f'<a href="{source}/apply/">APPLICATION</a><a href="{source}/apply/">Apply for this job</a>')
    assert choose(page) == source + "/apply/"


def test_provider_failure_is_bounded_and_does_not_navigate(page):
    page.set_content(f'<a href="{TARGET}">Start your journey</a>')
    def fail(*a, **kw):
        raise TimeoutError("provider limit")
    assert S.choose_apply_url(page, CFG, fail) is None
    assert page.url == URL
    assert S.choose_apply_url(page, {"llm": {"enabled": False}}, fail) is None


def test_embedded_link_uses_its_real_base_url(page):
    page.route("https://jobs.lever.co/fixture/123", lambda r: r.fulfill(
        body='<a href="/fixture/123/apply">Start your journey</a>', content_type="text/html"))
    page.set_content('<iframe src="https://jobs.lever.co/fixture/123"></iframe>')
    page.frame_locator("iframe").locator("a").wait_for()
    assert choose(page) == "https://jobs.lever.co/fixture/123/apply"


def test_normal_apply_recovers_without_clicking_site_script_or_submitting(page, monkeypatch, capsys):
    page.set_content(f'<h1>Security engineer</h1><a href="{TARGET}" '
                     'onclick="fetch(\'/submit\',{method:\'POST\'})">Start your journey</a>')
    page.route(TARGET, lambda route: route.fulfill(body='''<form method="post" action="/submit">
        <label>Email<input type="email" required></label><input type="file">
        <button>Submit application</button></form>''', content_type="text/html"))
    requests = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    monkeypatch.setattr(A, "llm_complete", lambda *a, **kw: '{"id":0}')
    monkeypatch.setattr(page, "wait_for_timeout", lambda *a: None)
    assert A.click_apply(page, cfg=CFG)
    page.wait_for_url(TARGET, timeout=3000)
    assert page.url == TARGET
    assert page.locator('input[type="email"]').input_value() == ""
    assert requests == [("GET", TARGET)]
    assert "Browser study: opened" in capsys.readouterr().err


def test_bot_wall_does_not_enter_study(page, monkeypatch):
    page.set_content(f'<p>Access denied</p><a href="{TARGET}">Start your journey</a>')
    monkeypatch.setattr(A, "is_bot_wall", lambda *_: True)
    monkeypatch.setattr(A, "llm_complete", lambda *a, **kw: pytest.fail("bot wall"))
    monkeypatch.setattr(page, "wait_for_timeout", lambda *a: None)
    assert not A.click_apply(page, cfg=CFG)


def test_existing_ats_selector_keeps_priority(page, monkeypatch):
    page.set_content(f'<a href="{TARGET}">Apply now</a>')
    monkeypatch.setattr(A, "llm_complete", lambda *a, **kw: pytest.fail("selector already found the link"))
    monkeypatch.setattr(page, "wait_for_timeout", lambda *a: None)
    assert A.click_apply(page, cfg=CFG)
    page.wait_for_url(TARGET, timeout=3000)
    assert page.url == TARGET


def test_study_does_not_report_http_error_as_opened(page, monkeypatch):
    page.set_content(f'<a href="{TARGET}">Start your journey</a>')
    page.route(TARGET, lambda route: route.fulfill(status=404, body="Not found"))
    monkeypatch.setattr(A, "llm_complete", lambda *a, **kw: '{"id":0}')
    monkeypatch.setattr(page, "wait_for_timeout", lambda *a: None)
    assert not A.click_apply(page, cfg=CFG)
