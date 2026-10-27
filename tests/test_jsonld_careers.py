import json
import re
from datetime import date

import jsonld_careers as J

PAGE = ('<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebSite"},'
        '{"@type":"JobPosting","title":"Fashion Photographer (m/f/d)","datePosted":"2026-10-12","validThrough":"2099-01-01",'
        '"hiringOrganization":{"@type":"Organization","name":"Acme Studio"},"jobLocation":{"@type":"Place","address":'
        '{"@type":"PostalAddress","addressLocality":"Berlin","addressCountry":"DE"}},"description":"&lt;p&gt;Shoot &amp;amp; edit&lt;/p&gt;"}]}</script>'
        '<script type="application/ld+json">[{"@type":["JobPosting"],"title":"Remote Pentester","jobLocationType":"TELECOMMUTE",'
        '"applicantLocationRequirements":{"@type":"Country","name":"US"},"datePosted":"2020-01-01"}]</script>'
        '<script type="application/ld+json">{broken</script></html>')


def classify(t):
    return "creative" if re.search(r"photograph", t, re.I) else None


def test_parse_jobpostings_reads_graph_lists_and_skips_broken_json():
    ps = J.parse_jobpostings(PAGE, "https://acme.test/jobs/1")
    assert [p["title"] for p in ps] == ["Fashion Photographer (m/f/d)", "Remote Pentester"]
    assert (ps[0]["company"], ps[0]["location"], ps[0]["posted"]) == ("Acme Studio", "Berlin, Germany", "2026-10-12")
    assert ps[0]["description"] == "Shoot & edit"
    assert ps[1]["location"] == "Remote - United States"


def test_freshness_uses_date_posted_and_valid_through():
    ps = J.parse_jobpostings(PAGE)
    today = date(2026, 10, 16)
    assert J.is_fresh(ps[0], today) and not J.is_fresh(ps[1], today)
    assert not J.is_fresh({"posted": "2026-10-15", "valid_through": "2026-10-01"}, today)
    assert J.is_fresh({"title": "no dates"}, today)


def test_ats_detection_ignores_marketing_hosts():
    html = ('<a href="https://boards.greenhouse.io/acme/jobs/1">x</a> <iframe src="https://acme.recruitee.com/o/x"></iframe>'
            '<a href="https://jobs.lever.co/foo">y</a><script src="https://boards.greenhouse.io/embed/job_board/js?for=zed"></script>'
            ' https://www.recruitee.com/x')
    assert J.find_ats(html) == [("greenhouse", "zed"), ("greenhouse", "acme"), ("lever", "foo"), ("recruitee", "acme")]


def test_career_and_detail_links():
    home = '<a href="/about">About</a><a href="/en/careers/">Join our team</a><a href="mailto:jobs@x.test">m</a>'
    assert J.career_links(home, "https://acme.test/") == ["https://acme.test/en/careers/"]
    other = '<a href="https://evil.example/careers">Careers</a><a href="https://jobs.acme.test/">Jobs</a>'
    assert J.career_links(other, "https://www.acme.test/") == ["https://jobs.acme.test/"]
    lst = '<a href="/jobs/42-photographer-berlin">Photographer</a><a href="/jobs/43-accountant">Accountant</a>'
    assert J.detail_links(lst, "https://acme.test/careers", classify) == [("https://acme.test/jobs/42-photographer-berlin", "creative")]
    assert J.sitemap_urls("<urlset><url><loc>https://a.test/jobs/1</loc></url></urlset>") == ["https://a.test/jobs/1"]


def test_probe_site_follows_careers_link_to_detail_page_without_network(monkeypatch):
    pages = {
        "https://acme.test/robots.txt": "User-agent: *\nDisallow: /private\n",
        "https://acme.test/": '<a href="/careers">Careers</a>',
        "https://acme.test/careers": '<a href="/jobs/42-photographer-berlin">Photographer</a>',
        "https://acme.test/jobs/42-photographer-berlin": PAGE,
    }
    monkeypatch.setattr(J, "get", lambda url, timeout=12, throttle=True: (pages[url], url) if url in pages else None)
    monkeypatch.setattr(J, "is_fresh", lambda p, *a, **k: True)
    res = J.probe_site("https://acme.test", classify)
    assert res["careers"] == ["https://acme.test/careers"]
    assert [p["title"] for p in res["postings"]] == ["Fashion Photographer (m/f/d)"]


def test_probe_site_stops_when_robots_disallow_everything(monkeypatch):
    monkeypatch.setattr(J, "get", lambda url, timeout=12, throttle=True: ("User-agent: *\nDisallow: /\n", url) if url.endswith("robots.txt") else pytest_fail())
    assert J.probe_site("https://closed.test", classify)["status"] == "robots"


def pytest_fail():
    raise AssertionError("fetched a page that robots.txt forbids")


def test_due_is_incremental():
    assert J.due(None)
    fresh = {"t": "2026-10-15", "careers": ["x"], "ats": []}
    assert not J.due(fresh, date(2026, 10, 16))
    assert J.due(fresh, date(2026, 10, 23))
    assert not J.due({"t": "2026-10-01", "careers": [], "ats": []}, date(2026, 10, 16))


def test_load_seeds_merges_files_one_entry_per_host(tmp_path):
    (tmp_path / "jsonld_a.json").write_text(json.dumps([{"site": "https://www.x.test/", "name": "X"}]))
    (tmp_path / "jsonld_b.json").write_text(json.dumps([{"site": "https://x.test", "name": "dup"}, {"site": "https://y.test", "name": "Y"}]))
    assert sorted(s["name"] for s in J.load_seeds(tmp_path)) == ["X", "Y"]


def test_source_is_switchable_off_in_config(monkeypatch):
    monkeypatch.setattr(J, "crawl", lambda *a, **k: (_ for _ in ()).throw(AssertionError("crawled although disabled")))
    cfg = {"discovery": {"jsonld_careers": {"enabled": False}}}
    assert J.discover_postings(cfg, 0, lambda t, c: None) == []
