from playwright.sync_api import sync_playwright

import autopilot as A

PAGE = """<!doctype html><meta charset=utf-8><body><form>
<fieldset id="events"><legend>Have we met you at one of our events? If so, which one(s)? *</legend>
  <label><input type="checkbox" id="talks"> Tech Talks</label>
  <label><input type="checkbox" id="meetups"> Sponsored Meetups</label>
  <label><input type="checkbox" id="ev_none"> None</label></fieldset>
<fieldset id="skills"><legend>Which of these have you used? *</legend>
  <label><input type="checkbox" id="burp"> Burp Suite</label>
  <label><input type="checkbox" id="nessus"> Nessus</label>
  <label><input type="checkbox" id="sk_none"> None of the above</label></fieldset>
</form></body>"""


def demo(monkeypatch):
    cfg = A.read_json(A.CFG)
    cv = cfg["personas"]["appsec"]["cv"]
    monkeypatch.setattr(A, "extract_cv_text", lambda *a: "Jane Doe. Used Burp Suite for secure application testing.")
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(PAGE)
        A.fill_scope(page, cfg, cv, None, page)
        checked = lambda i: page.locator(f"#{i}").is_checked()
        assert checked("ev_none") and not checked("talks") and not checked("meetups"), \
            "no event attended: the honest answer is None"
        assert checked("burp") and not checked("sk_none"), "a CV-backed option wins over None"
        b.close()
    print("none checkbox self-check OK")


def test_demo(monkeypatch):
    demo(monkeypatch)


if __name__ == "__main__":
    demo()
