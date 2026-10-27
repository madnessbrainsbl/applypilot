from playwright.sync_api import sync_playwright

import autopilot as A

PAGE = """<!doctype html><meta charset=utf-8><body>
<div class="field">
  <label>Location (City)</label>
  <input id="city">
  <label>Gender</label>
  <div class="select__control" id="gender"><div class="select__value-container">Select...</div></div>
  <label>Are you Hispanic/Latino?</label>
  <div class="select__control" id="hispanic"><div class="select__value-container">Select...</div></div>
</div>
<div class="wrapper">
  <div class="own"><label>Preferred working location</label>
    <div class="select__control" id="preferred"></div></div>
</div>
<div class="orphan"><div class="select__control" id="nolabel"></div></div>
</body>"""


def demo():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(PAGE)

        def label_of(sel):
            return page.locator(sel).evaluate(A._SELECT_LABEL_JS)

        assert label_of("#hispanic") == "Are you Hispanic/Latino?", label_of("#hispanic")
        assert label_of("#gender") == "Gender", label_of("#gender")
        assert label_of("#preferred") == "Preferred working location", label_of("#preferred")
        assert label_of("#nolabel") == "", "a control with no preceding label must not borrow one"

        for sel in ("#hispanic", "#gender"):
            lbl = label_of(sel).lower()
            assert not ("location" in lbl and "city" in lbl), \
                f"{sel} must never be treated as the city field — that typed Springfield into it"

        b.close()
    print("select label self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
