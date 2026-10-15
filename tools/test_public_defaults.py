import copy
from playwright.sync_api import sync_playwright
import autopilot as A


def demo():
    cfg = A.read_json(A.CFG)
    assert cfg['apply']['require_sponsorship'] is None
    assert cfg['apply']['submit_without_review'] is False
    assert all(p.get('enabled') is False for p in cfg['llm']['providers'])
    for label in ['Are you 18 years of age or older?', 'Have you ever worked for Acme?',
                  'Do you require visa sponsorship?', 'Are you authorized to work in the UK?',
                  'Agreement to Arbitrate', 'Do you hold a work permit?']:
        assert A.offline_answer(label, ['Yes', 'No'], cfg) is None, label
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        markup = '''<form>
        <fieldset><legend>Do you require visa sponsorship?</legend>
        <label><input type=radio name=visa value=yes>Yes</label>
        <label><input type=radio name=visa value=no>No</label></fieldset>
        <div class=_yesno_test><h3>Are you authorized to work in the UK?</h3>
        <button type=button onclick="this.setAttribute('aria-pressed','true')">Yes</button>
        <button type=button onclick="this.setAttribute('aria-pressed','true')">No</button></div>
        </form>'''
        for needs in [None, False, True]:
            configured = copy.deepcopy(cfg)
            configured['apply']['require_sponsorship'] = needs
            page.set_content(markup)
            A.fill_radio_groups(page, configured)
            A.fill_yesno_buttons(page, configured)
            if needs is None:
                assert page.locator('input:checked, button[aria-pressed=true]').count() == 0
            else:
                assert page.locator('input:checked').get_attribute('value') == ('yes' if needs else 'no')
                assert page.locator('button[aria-pressed=true]').inner_text() == ('No' if needs else 'Yes')
        browser.close()
    print('public defaults self-check OK')


if __name__ == '__main__':
    demo()
