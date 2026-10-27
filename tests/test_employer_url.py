"""employer_url: application target extraction for arbeitsagentur / eu_portals listings, no network."""
import employer_url as E

EURES = {"jvProfiles": {"nl": {
    "applicationInstructions": ["CV with attached letter,Online application form",
                                '<p>Apply: <a href="https://boards.greenhouse.io/acme/jobs/4242">here</a></p> hr@acme.nl'],
    "personContacts": [{"communications": {"webProfiles": [{"uri": "https://acme.com/jobs/photographer"}],
                                           "emails": [{"uri": "team@acme.nl"}, {"uri": None}]}}]}}}


def test_classify_and_job_url():
    assert E.classify_url("https://jobs.lever.co/acme/1234-abcd") == "ats"
    assert E.classify_url("https://www.xing.com/jobs/x") == "aggregator"
    assert E.classify_url("https://www.arbeitsagentur.de/jobsuche/jobdetail/1") == "aggregator"
    assert E.classify_url("https://acme.com/careers/x?gh_jid=77") == "ats"
    assert E.classify_url("https://acme.com/jobs/photographer") == "page"
    assert E.is_job_url("https://boards.greenhouse.io/acme/jobs/4242")
    assert not E.is_job_url("https://jobs.lever.co/acme")
    assert E.is_job_url("https://acme.jobs.personio.de/job/99")
    assert not E.is_job_url("https://acme.teamtailor.com/jobs")


def test_candidates():
    urls, mails = E.candidates_eures(EURES)
    assert ("https://acme.com/jobs/photographer", True) in urls
    assert ("https://boards.greenhouse.io/acme/jobs/4242", True) in urls
    assert set(mails) == {"team@acme.nl", "hr@acme.nl"}
    desc = "Bewerbung an jobs@firma.de oder https://join.com/companies/firma/123-sec und https://firma.de/impressum"
    urls, mails = E.candidates_ba("https://www.finest-jobs.com/x?cp=BA", desc)
    assert urls == [("https://www.finest-jobs.com/x?cp=BA", True), ("https://join.com/companies/firma/123-sec", False)]
    assert mails == ["jobs@firma.de"]


def test_resolve_branches():
    nofetch = lambda u: None
    # direct ATS job url needs no fetch
    r = E.resolve([("https://jobs.lever.co/acme/1234", True)], [], "t", fetch=nofetch)
    assert (r["kind"], r["url"]) == ("ats", "https://jobs.lever.co/acme/1234")
    # employer page that embeds a job link on an ATS
    page = lambda u: ('<a href="https://apply.workable.com/acme/j/ABC123/">Apply</a>', u)
    assert E.resolve([("https://acme.com/jobs/1", True)], [], "t", fetch=page)["url"] == "https://apply.workable.com/acme/j/ABC123/"
    # embedded board root -> job picked by title
    board_page = lambda u: ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>', u)
    board = lambda ats, slug, cl: [{"title": "Security Engineer (m/w/d)", "url": "https://boards.greenhouse.io/acme/jobs/1"},
                                   {"title": "Cook", "url": "https://boards.greenhouse.io/acme/jobs/2"}]
    r = E.resolve([("https://acme.com/jobs/1", True)], [], "Security Engineer", fetch=board_page, board=board)
    assert r["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    # plain employer page; aggregator-only -> email; nothing -> none
    plain = lambda u: ("<html>no ats</html>", u)
    assert E.resolve([("https://acme.com/jobs/1", True)], ["a@acme.com"], "t", fetch=plain)["kind"] == "page"
    assert E.resolve([("https://www.xing.com/jobs/1", True)], ["a@acme.com"], "t", fetch=plain)["kind"] == "email"
    assert E.resolve([], [], "t", fetch=plain)["kind"] == "none"


def test_apply_to_rows_marks_rows(monkeypatch):
    res = {"https://www.arbeitsagentur.de/jobsuche/jobdetail/1": {"kind": "ats", "url": "https://jobs.lever.co/a/1", "emails": []},
           "https://www.arbeitsagentur.de/jobsuche/jobdetail/2": {"kind": "email", "url": "", "emails": ["x@y.de"]},
           "https://www.arbeitsagentur.de/jobsuche/jobdetail/3": {"kind": "none", "url": "", "emails": []}}
    monkeypatch.setattr(E, "resolve_many", lambda items, workers=4, per_run=None: res)
    rows = [{"url": u, "title": "t", "description": "d"} for u in res]
    out = E.apply_to_rows(rows, "arbeitsagentur")
    assert out[0]["url"] == "https://jobs.lever.co/a/1" and out[0]["listing_url"].endswith("/1") and "Listing: " in out[0]["description"]
    assert out[1]["status"] == "manual_contact" and "x@y.de" in out[1]["description"] and out[1]["url"].endswith("/2")
    assert "status" not in out[2] and out[2]["description"] == "d"
