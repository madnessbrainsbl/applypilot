import csv
import json
import re
import sqlite3
from datetime import date, timedelta

import findall_osint as F
import jsonld_careers as J

TODAY = date(2026, 10, 16)
O = {**F.DEFAULTS["hosts"]}


def classify(t):
    return "appsec" if re.search(r"security|pentest", t, re.I) else ("creative" if re.search(r"photograph", t, re.I) else None)


def test_ct_names_survive_truncated_crtsh_body_and_wildcards():
    body = ('[{"name_value":"*.acme.com\\nacme.com","id":1},{"name_value":"careers.acme.com\\njobs.eu.acme.com","id":2},'
            '{"name_value":"staging-jobs.acme.com","id":3},{"name_va')   # cut mid-record like an 8 MB cap
    names = F.ct_names(body)
    assert {"acme.com", "careers.acme.com", "jobs.eu.acme.com"} <= names
    assert F.career_hosts(names, "acme.com") == ["careers.acme.com", "jobs.eu.acme.com"]   # staging dropped, apex ignored


def test_ct_names_reads_certspotter_list():
    assert "karriere.acme.de" in F.ct_names(json.dumps([{"dns_names": ["karriere.acme.de", "*.acme.de"]}]))


def test_career_hosts_rejects_foreign_domains_and_noise():
    names = {"careers.evil.com", "careers.acme.com.evil.io", "jobs-api.acme.com", "recruiting.acme.com", "www.acme.com"}
    assert F.career_hosts(names, "acme.com") == ["recruiting.acme.com"]


def test_cdx_entries_pick_shallow_listing_pages_on_the_company_site():
    rows = ["http://www.acme.com:80/about-us/careers", "http://www.acme.com/about-us/careers/index.html",
            "http://acme.com/careers/data-architect/senior/2012/x/y", "https://other.com/careers", "https://acme.com/blog/careers-day",
            "https://jobs.acme.com/", "https://acme.com/karriere?utm=1", "https://acme.com/assets/jobs.js",
            "https://blog.acme.com/careers", "https://br.acme.com/careers", "https://www.acme.com/about-us/careers/"]
    got = F.cdx_entries(rows, "acme.com")
    assert got == ["https://jobs.acme.com", "https://acme.com/karriere", "https://www.acme.com/about-us/careers"]


def test_registered_domain_handles_co_uk_and_subdomains():
    assert F.registered_domain("careers.datadoghq.com") == "datadoghq.com"
    assert F.registered_domain("www.bbc.co.uk") == "bbc.co.uk"
    assert F.registered_domain("acme.de") == "acme.de"


def test_find_workday_extracts_tenant_instance_site_and_skips_noise():
    text = ("href='https://crowdstrike.wd5.myworkdayjobs.com/en-US/crowdstrikecareers/job/x' "
            "https://acme.wd1.myworkdayjobs.com/External https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs")
    assert F.find_workday(text) == [("crowdstrike", "wd5", "crowdstrikecareers"), ("acme", "wd1", "External")]


def test_merge_tracking_keeps_first_seen_and_counts_only_new():
    old, n = F.merge_tracking({}, [{"url": "u1", "title": "A"}], TODAY - timedelta(days=3))
    assert n == 1
    merged, n = F.merge_tracking(old, [{"url": "u1", "title": "A"}, {"url": "u2", "title": "B"}], TODAY)
    assert n == 1 and merged["u1"]["first_seen"] == (TODAY - timedelta(days=3)).isoformat() and merged["u2"]["first_seen"] == TODAY.isoformat()


def test_active_hosts_are_reread_sooner_than_quiet_ones():
    t = (TODAY - timedelta(days=6)).isoformat()
    assert F.entry_due(None, O, TODAY)
    assert F.entry_due({"t": t, "status": "ok", "last_new": (TODAY - timedelta(days=2)).isoformat()}, O, TODAY)
    assert not F.entry_due({"t": t, "status": "ok", "last_new": ""}, O, TODAY)
    assert not F.entry_due({"t": t, "status": "dead"}, O, TODAY)


def test_read_findall_reads_db_read_only_and_csv_reports(tmp_path):
    con = sqlite3.connect(tmp_path / "jobfind.db")
    con.execute("CREATE TABLE jobs (url TEXT, title TEXT, company TEXT, location TEXT, description TEXT, posted TEXT, first_seen TEXT)")
    con.execute("INSERT INTO jobs VALUES ('https://x.test/1','AppSec Engineer','hims &#038; hers','Remote','real text','2026-10-14','2026-10-14T05:00:00Z')")
    con.commit()
    con.close()
    out = tmp_path / "jobfind_out"
    out.mkdir()
    with open(out / "jobs_1.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "company", "location", "remote", "source", "found_emails", "url", "posted"])
        w.writerow(["Security Engineer", "Acme", "Berlin", "False", "greenhouse", "", "https://job-boards.greenhouse.io/acme/jobs/1", "2026-10-15"])
    (out / "jobs_check_2.csv").write_text("title,url\nIgnored,https://y.test\n", encoding="utf-8")
    rows = F.read_findall(tmp_path, TODAY)
    urls = sorted(r["url"] for r, _ in rows)
    assert urls == ["https://job-boards.greenhouse.io/acme/jobs/1", "https://x.test/1"]
    assert J.find_ats("https://job-boards.greenhouse.io/acme/jobs/1") == [("greenhouse", "acme")]


def test_import_postings_rereads_named_boards_and_keeps_fresh_db_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "STORE", tmp_path / "store.json")
    monkeypatch.setattr(F, "CACHE", tmp_path)
    today = date.today().isoformat()
    rows = [({"title": "Security Engineer", "company": "Acme &amp; Co", "url": "https://job-boards.greenhouse.io/acme/jobs/1",
              "description": "", "posted": today}, today),
            ({"title": "AppSec", "company": "Lead Inc", "url": "https://x.test/1", "description": "real text", "posted": today}, today),
            ({"title": "Old", "company": "Lead Inc", "url": "https://x.test/2", "description": "t", "posted": "2020-01-01"}, "2020-01-01")]
    monkeypatch.setattr(F, "read_findall", lambda root, t: rows)
    monkeypatch.setattr(J, "_ats_rows", lambda ats, slug, c: [{"title": "Pentest Lead", "url": "https://job-boards.greenhouse.io/acme/jobs/9",
                                                              "location": "Berlin", "description": "d", "posted": today, "company": ""}])
    out = F.import_postings({}, classify, {**F.DEFAULTS["import"]})
    assert sorted(q["url"] for q in out) == ["https://job-boards.greenhouse.io/acme/jobs/9", "https://x.test/1"]
    assert [q for q in out if "acme/jobs/9" in q["url"]][0]["company"] == "Acme & Co"


PAGE = ('<html><a href="/jobs/1">Security Engineer</a>'
        '<script type="application/ld+json">{"@type":"JobPosting","title":"Pentester","datePosted":"%s","description":"x",'
        '"hiringOrganization":{"name":"Acme"},"jobLocation":{"address":{"addressLocality":"Berlin","addressCountry":"DE"}}}</script>'
        'https://boards.greenhouse.io/acme https://acme.wd5.myworkdayjobs.com/Careers</html>' % date.today().isoformat())


def test_read_entry_combines_jsonld_ats_and_workday_without_network(monkeypatch):
    import time
    F._ROBOTS.clear()
    monkeypatch.setattr(J, "_robots", lambda base, fetch=None: (None, []))
    monkeypatch.setattr(J, "get", lambda url, timeout=10, throttle=True: (PAGE, url))
    monkeypatch.setattr(J, "_ats_rows", lambda ats, slug, c: [{"title": "Security Analyst", "url": f"https://{ats}.test/{slug}/1",
                                                              "location": "", "description": "", "posted": ""}])
    monkeypatch.setattr(F, "workday_rows", lambda t, wd, site, kw, c, cap: [{"title": "Security Lead", "url": f"https://{t}.test/1"}])
    r = F.read_entry("https://careers.acme.com/", classify, ["security"], O, time.monotonic() + 30)
    assert r["status"] == "ok" and r["ats"] == [["greenhouse", "acme"]] and r["workday"] == [["acme", "wd5", "Careers"]]
    assert {q["title"] for q in r["postings"]} >= {"Pentester", "Security Analyst", "Security Lead"}


def test_read_entry_stops_on_robots_and_reports_dead_pages(monkeypatch):
    import time, urllib.robotparser
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /"])
    F._ROBOTS.clear()
    monkeypatch.setattr(J, "_robots", lambda base, fetch=None: (rp, []))
    assert F.read_entry("https://a.test/careers", classify, [], O, time.monotonic() + 5)["status"] == "robots"
    F._ROBOTS.clear()
    monkeypatch.setattr(J, "_robots", lambda base, fetch=None: (None, []))
    monkeypatch.setattr(J, "get", lambda *a, **k: None)
    assert F.read_entry("https://a.test/careers", classify, [], O, time.monotonic() + 5)["status"] == "dead"


def test_load_domains_orders_unresolved_security_companies_first(tmp_path, monkeypatch):
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "security_x.json").write_text(json.dumps({"companies": [{"name": "Akamai", "domain": "akamai.com"},
                                                                       {"name": "Okta", "domain": "okta.com"}]}), encoding="utf-8")
    (seeds / "findall_domains.json").write_text(json.dumps(["Acme.IO", {"domain": "boards.greenhouse.io"}]), encoding="utf-8")
    (tmp_path / "security_osint.json").write_text(json.dumps({"akamai": {"board": None}, "okta": {"board": ["ashby", "okta"]}}), encoding="utf-8")
    monkeypatch.setattr(F, "SEEDS", seeds)
    monkeypatch.setattr(F, "CACHE", tmp_path)
    monkeypatch.setattr(F, "ROOT", tmp_path)   # no jobs.db there: the DB part is skipped
    d = F.load_domains()
    assert d["akamai.com"]["prio"] == 0 and d["okta.com"]["prio"] == 2 and d["acme.io"]["prio"] == 2
    assert "greenhouse.io" not in d   # an ATS is not an employer


def test_discover_filters_persona_and_visa_and_reports_first_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "STORE", tmp_path / "s.json")
    posted = date.today().isoformat()
    store = {"domains": {}, "boards": {}, "entries": {"https://careers.acme.com/": {"t": posted, "postings": {
        "u1": {"title": "Security Engineer", "company": "Acme", "url": "u1", "location": "Berlin, Germany", "posted": posted, "first_seen": posted,
               "description": "d"},
        "u2": {"title": "Security Engineer", "company": "Acme", "url": "u2", "location": "Austin, US", "posted": posted, "first_seen": posted},
        "u3": {"title": "Photographer", "company": "Acme", "url": "u3", "location": "Paris", "posted": posted, "first_seen": posted},
        "u4": {"title": "Pentester", "company": "Acme", "url": "u4", "location": "", "posted": "2020-01-01", "first_seen": "2020-01-01"}}}}}
    F.save_store(store)
    monkeypatch.setattr(F, "crawl", lambda *a, **k: store)
    F._LAST.clear()
    visa = lambda job, persona: "Europe" in job["location"] or "Germany" in job["location"]
    out = F.discover("findall_hosts", {"active_persona": "appsec"}, classify, lambda j: True, visa)
    assert [j["url"] for j in out] == ["u1"] and "First seen: " + posted in out[0]["description"]
    assert F.discover("findall_hosts", {"active_persona": "appsec", "discovery": {"findall_osint": {"enabled": False}}},
                      classify, lambda j: True, visa) == []


def test_rippling_and_gem_boards_are_found_and_read(monkeypatch):
    assert F.find_extra_ats("x https://ats.rippling.com/en-GB/Acme-Co/jobs/1 https://jobs.gem.com/Beta ") == [("rippling", "acme-co"), ("gem", "beta")]
    answers = {
        "https://api.gem.com/job_board/v0/beta/job_posts/": json.dumps([{"title": "Security Engineer", "absolute_url": "https://jobs.gem.com/beta/1",
                                                                         "location": {"name": "Berlin"}, "content_plain": "plain",
                                                                         "first_published_at": "2026-10-10T00:00:00Z"},
                                                                        {"title": "Chef", "absolute_url": "https://jobs.gem.com/beta/2"}]),
        "https://api.rippling.com/platform/api/ats/v1/board/acme-co/jobs": json.dumps([{"uuid": "u1", "name": " Pentester ", "url": "https://ats.rippling.com/acme-co/jobs/u1"},
                                                                                         {"uuid": "u2", "name": "Cook", "url": "x"}]),
        "https://api.rippling.com/platform/api/ats/v1/board/acme-co/jobs/u1": json.dumps(
            {"description": {"company": "<p>About</p>", "role": "<p>Hack things</p>"}, "workLocations": ["Berlin"], "createdOn": "2026-10-09T00:00:00-07:00"})}
    monkeypatch.setattr(J, "get", lambda url, timeout=10, throttle=True: (answers[url], url))
    gem = F.extra_ats_rows("gem", "beta", classify)
    assert [(r["title"], r["location"], r["posted"]) for r in gem] == [("Security Engineer", "Berlin", "2026-10-10")]
    rip = F.extra_ats_rows("rippling", "acme-co", classify)
    assert len(rip) == 1 and rip[0]["title"] == "Pentester" and "Hack things" in rip[0]["description"] and rip[0]["location"] == "Berlin"
