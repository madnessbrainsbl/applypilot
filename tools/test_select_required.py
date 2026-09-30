from playwright.sync_api import sync_playwright

import autopilot as A

PAGE = """<!doctype html><meta charset=utf-8><body><form>
  <div class="q"><label>First Name*</label><input id="first" required value="James"></div>
  <div class="q"><label>How did you hear about us?</label>
     <div class="select__control" id="optional"><div class="select__value-container">Select...</div></div></div>
  <div class="q"><label>Country*</label>
     <div class="select__control" id="req"><div class="select__value-container">Select...</div></div></div>
  <div class="q"><label>Gender</label>
     <div class="select__control" id="ariareq" aria-required="true"></div></div>
  <div class="q"><label>Seniority</label>
     <div class="select__control" id="blank_err"></div>
     <div class="error-message"></div></div>
  <div class="q"><label>Team size</label>
     <div class="select__control" id="real_err"></div>
     <div class="error-message">This field is required</div></div>
</form></body>"""

LABEL_PAGE = """<!doctype html><meta charset=utf-8><body>
<div class="field">
  <label>Location (City)</label>
  <input id="city">
  <div class="title">Please select your ethnicity</div>
  <div class="select__control" id="ethnicity"></div>
</div>
<div class="field2"><label>Country</label>
  <div class="select__control" id="country"></div></div>
</body>"""


def demo():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page()

        page.set_content(PAGE)

        def req(sel):
            return page.locator(sel).evaluate(A._SELECT_REQUIRED_JS)

        assert req("#req") is True, "a dropdown whose own label carries * is required"
        assert req("#ariareq") is True, "aria-required on the control itself counts"
        assert req("#real_err") is True, "a validation message with text counts"
        assert req("#optional") is False, \
            "an optional dropdown must not inherit 'required' from another field on the form"
        assert req("#blank_err") is False, \
            "Greenhouse renders an empty error placeholder on every field; it is not a failure"

        page.set_content(LABEL_PAGE)
        eth = page.locator("#ethnicity").evaluate(A._SELECT_LABEL_JS)
        assert "city" not in eth.lower(), \
            f"an ethnicity dropdown must never resolve to the city label (got {eth!r}) - that typed Springfield into it"
        assert page.locator("#country").evaluate(A._SELECT_LABEL_JS) == "Country"

        b.close()
    print("select required self-check OK")


if __name__ == "__main__":
    demo()
