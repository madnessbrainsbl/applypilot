from playwright.sync_api import sync_playwright
import autopilot as A

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<form><label>Email <input name=email type=email required></label><label>CV <input name=cv type=file required></label><label><input name=ok type=checkbox required>Accept</label><label><input name=choice type=radio value=a required>A</label><label><input name=choice type=radio value=b required>B</label><select name=country required><option value=''>Choose</option><option value=de>DE</option></select><button type=submit>Submit</button></form>""")
    issues = A.required_field_issues(page)
    assert {"Email", "CV", "Accept", "A", "country"}.issubset(set(issues)), issues
    page.locator("input[name=email]").fill("person@example.com")
    page.locator("input[name=cv]").set_input_files({"name": "cv.pdf", "mimeType": "application/pdf", "buffer": b"pdf"})
    page.locator("input[name=ok]").check()
    page.locator("input[value=a]").check()
    page.locator("select[name=country]").select_option("de")
    assert A.required_field_issues(page) == []
    assert A._is_form_submit(page.get_by_role("button", name="Submit"))
    browser.close()
print("form validation self-check OK")
