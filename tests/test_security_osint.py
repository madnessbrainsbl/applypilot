"""security_osint: no network. Seeds merge, ATS-link / JSON-LD parsing, and the title + visa-path
filter run on a fake board fetcher."""
import json

import security_osint as O
import sponsors as S


def _autopilot_fns():
    import autopilot as A
    cfg = A.get_persona_cfg({"personas": {"appsec": {"search": {
        "required_title_terms": ["security", "pentest", "penetration"],
        "excluded_title_terms": ["senior", "lead", "sales"]}}}}, "appsec")
    return A, cfg


def test_ats_links_and_slugs():
    page = ('<a href="https://boards.greenhouse.io/embed/job_board?for=acme">x</a> '
            '<iframe src="https://jobs.lever.co/acme-sec"></iframe> https://jobs.ashbyhq.com/Foo%20Bar/abc '
            'https://zed.recruitee.com/o/x https://static.teamtailor.com/x https://foo.jobs.personio.de/job/1')
    got = O.ats_links(page)
    assert ("greenhouse", "acme") in got and ("lever", "acme-sec") in got and ("recruitee", "zed") in got
    assert ("ashby", "Foo Bar") in got and ("personio", "foo") in got
    assert not any(slug == "static" for _a, slug in got), "generic hosts are not company boards"
    assert O._slugs({"name": "Acme Security Ltd", "domain": "acme-sec.co.uk", "slugs": ["acme"]}) == [
        "acme", "acme-sec", "acmesecurity", "acme-security"]


def test_jsonld_postings():
    ld = {"@graph": [{"@type": "JobPosting", "title": "Penetration Tester", "url": "/jobs/7",
                      "description": "<p>Break things</p>", "jobLocation": {"address": {
                          "addressLocality": "Amsterdam", "addressCountry": "NL"}}}]}
    page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    rows = O.jsonld_postings(page, "https://acme.test/careers")
    assert rows[0]["url"] == "https://acme.test/jobs/7" and rows[0]["location"] == "Amsterdam, NL"
    assert rows[0]["description"] == "Break things"


def test_seeds_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "SEEDS", tmp_path)
    for lst, rows in {"curated": [{"name": "Acme Security Ltd", "domain": "acme.io"}],
                      "crest": [{"name": "ACME Security", "country": "UK"}, {"name": "Other Pentest", "slugs": ["op"]}]}.items():
        O._write_seed(lst, rows)
    m = O.load_seeds(["curated", "crest", "wikidata"])
    assert len(m) == 2
    acme = m["acme security"]
    assert acme["domain"] == "acme.io" and acme["country"] == "UK" and acme["src"] == ["curated", "crest"]


def test_discover_keeps_only_title_fits_with_a_visa_path(tmp_path, monkeypatch):
    A, cfg = _autopilot_fns()
    monkeypatch.setattr(O, "CACHE_FILE", tmp_path / "cache.json")
    saved = dict(S._INDEX), dict(S._ROWS)
    S.set_register("uk", ["Acme Security Limited"])
    try:
        rows = {
            "acme security": [
                {"title": "Penetration Tester", "url": "https://x/1", "location": "London, UK", "description": ""},
                {"title": "Senior Security Engineer", "url": "https://x/2", "location": "London, UK", "description": ""},
                {"title": "Sales Manager", "url": "https://x/3", "location": "London, UK", "description": ""},
                {"title": "Security Engineer", "url": "https://x/4", "location": "Austin, TX", "description": ""}],
            "nosponsor co": [
                {"title": "Security Analyst", "url": "https://y/1", "location": "London, UK", "description": ""},
                {"title": "Security Analyst", "url": "https://y/2", "location": "Remote - Worldwide", "description": "fully remote"}],
        }
        companies = {"acme security": {"name": "Acme Security", "src": ["curated"], "slugs": []},
                     "nosponsor co": {"name": "Nosponsor Co", "src": ["crest"], "slugs": []},
                     "dead co": {"name": "Dead Co", "src": ["bounty"], "slugs": []}}

        def fetcher(c, cached):
            r = rows.get(_key(c))
            return (["greenhouse", "x"], r, True) if r else (None, [], True)
        _key = lambda c: c["name"].lower()
        cfg["discovery"] = {"security_osint": {"max_companies_per_run": 10}}
        jobs = O.discover(cfg, 0, A.classify_persona, A.offers_sponsorship,
                          lambda j: "worldwide" in (j.get("location") or "").lower(), A.visa_free_location,
                          companies=companies, fetcher=fetcher)
        got = {(j["company"], j["url"]) for j in jobs}
        assert got == {("Acme Security", "https://x/1"), ("Nosponsor Co", "https://y/2")}
        assert "UK: company is on the Home Office register" in jobs[0]["description"]
        assert O.SUMMARY["boards"] == 2 and O.SUMMARY["postings"] == 6 and O.SUMMARY["title_fit"] == 4
        cache = json.loads((tmp_path / "cache.json").read_text())
        assert cache["acme security"]["board"] == ["greenhouse", "x"] and cache["dead co"]["board"] is None

        # second run: the clean miss is not probed again; boards are re-read
        calls = []
        O.discover(cfg, 0, A.classify_persona, None, None, None, companies=companies,
                   fetcher=lambda c, e: calls.append(c["name"]) or fetcher(c, e))
        assert sorted(calls) == ["Acme Security", "Nosponsor Co"]
        cfg["discovery"] = {"security_osint": {"enabled": False}}
        assert O.discover(cfg, 0, A.classify_persona, companies=companies, fetcher=fetcher) == []
    finally:
        S._INDEX.clear(); S._INDEX.update(saved[0]); S._ROWS.clear(); S._ROWS.update(saved[1])
