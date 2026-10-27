"""Unknown candidate facts stay blank; explicit profile values drive form filling."""
import pytest
import autopilot as A
import sponsors


@pytest.fixture
def page(monkeypatch):
    from playwright.sync_api import sync_playwright
    monkeypatch.setattr(A, '_QUIT_RUN', {'v': False})
    monkeypatch.setattr(A._TL, 'fill_deadline', 0, raising=False)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        yield browser.new_page()
        browser.close()


@pytest.mark.parametrize("label,value", [("Street Address", "12 Example Street"), ("Address 1", "12 Example Street"), ("City", "Springfield")])
def test_short_address_uses_own_profile(page, label, value):
    page.set_content(f'<label for="address">{label}</label><input id="address">')
    cfg = {"profile": {"address": "12 Example Street", "location": "Springfield, Exampleland"}, "apply": {}}
    assert A.fill_field(page.locator('#address'), cfg)
    assert page.locator('#address').input_value() == value


def test_unknown_postal_code_is_not_guessed(page):
    page.set_content('<label for="zip">Postal code</label><input id="zip">')
    assert not A.fill_field(page.locator('#zip'), {"profile": {}, "apply": {}})
    assert page.locator('#zip').input_value() == ''


def test_sponsorship_waiver_requires_explicit_preference():
    job = {"location": "Berlin, Germany"}
    assert not sponsors.sponsorship_waived(job, 'creative')
    assert sponsors.sponsorship_waived(job, 'creative', {"apply": {"sponsorship_waived": {"creative": "europe"}}})


@pytest.mark.parametrize('phrase', ['Only open to currently enrolled students', 'Only open to active students', 'Only open to enrolled students'])
def test_campus_enrollment_requirement_is_a_gate(page, phrase):
    page.set_content(f'<p>{phrase}</p>')
    assert A.hard_disqualifier(page)
