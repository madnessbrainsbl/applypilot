import json
import logging
import sqlite3
import time

import pandas as pd

import autopilot as A
import jobspy_fast as jf


def test_disk_cache_keeps_entries_until_their_own_expiry(tmp_path):
    path = tmp_path / "c.json"
    c = jf.DiskCache(path, ttl_s=3600)
    c.put("a", {"u": "x"})
    c.put("gone", 1, ttl_s=-1)
    c.flush()
    again = jf.DiskCache(path, ttl_s=3600)
    assert again.get("a") == {"u": "x"} and again.get("gone") is None
    assert again.get("missing", "dflt") == "dflt"


def test_market_rotation_walks_the_list_and_wraps(tmp_path):
    path, markets = tmp_path / "m.json", ["a", "b", "c", "d", "e"]
    seen = []
    for _ in range(4):
        window, commit = jf.pick_markets(markets, 2, "appsec", path=path)
        seen.append(window)
        commit()
    assert seen == [["a", "b"], ["c", "d"], ["e", "a"], ["b", "c"]]
    window, commit = jf.pick_markets(markets, 2, "creative", path=path)   # personas rotate independently
    assert window == ["a", "b"]
    commit()
    assert json.loads(path.read_text())["appsec"]["cursor"] == 3


def test_market_rotation_starts_from_the_legacy_cursor_once(tmp_path):
    legacy = tmp_path / "old.json"
    legacy.write_text('{"cursor": 6}')
    window, commit = jf.pick_markets(list("abcdefgh"), 3, "appsec", path=tmp_path / "m.json", legacy=legacy)
    assert window == ["g", "h", "a"]
    commit()
    legacy.write_text('{"cursor": 1}')
    assert jf.pick_markets(list("abcdefgh"), 3, "appsec", path=tmp_path / "m.json", legacy=legacy)[0] == ["b", "c", "d"]


def test_rescan_window_covers_only_the_gap_since_the_last_complete_scan():
    now = 1_000_000.0
    assert jf.window_hours(None, 336, now) == 336
    assert jf.window_hours(now - 3600, 336, now) == 24                    # floor
    assert jf.window_hours(now - 5 * 86400, 336, now) == int(120 * 1.5 + 12)
    assert jf.window_hours(now - 90 * 86400, 336, now) == 336             # never wider than the base


def test_site_gate_counts_one_burst_once_and_pauses_after_repeats():
    g = jf.SiteGate(max_hits=2, base_cool_s=0.01)
    g.hit("linkedin")
    g.hit("linkedin")                     # same burst, still cooling
    assert not g.paused("linkedin")
    time.sleep(0.03)
    g.hit("linkedin")
    assert g.paused("linkedin") and not g.paused("indeed")


def test_rate_limit_watch_attributes_429_logs_to_their_site():
    watch = jf.RateLimitWatch()
    log = logging.getLogger("JobSpy:LinkedIn")
    log.addHandler(watch)
    try:
        log.error("429 Response - Blocked by LinkedIn for too many requests")
        log.error("LinkedIn: something else")
    finally:
        log.removeHandler(watch)
    assert watch.count("linkedin") == 1 and watch.count("indeed") == 0


def test_interleave_alternates_sites():
    assert jf.interleave([[1, 2, 3], ["a"], ["x", "y"]]) == [1, "a", "x", 2, "y", 3]


def _collect_env(monkeypatch, rows, known_ids=()):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(A.db.SCHEMA)
    for i in known_ids:
        con.execute("INSERT INTO jobs (id, source, keyword, title, company, url, created_at) VALUES (?,?,?,?,?,?,?)",
                    (f"id{i}", "jobspy", "k", "t", "c", f"https://www.linkedin.com/jobs/view/{i}", "now"))
    monkeypatch.setattr(A.db, "connect", lambda: con)
    monkeypatch.setattr(A, "_full_list_cache", {})
    monkeypatch.setattr(A, "_jobspy_lenient_countries", lambda: None)
    monkeypatch.setattr(A, "_jobspy_scrape", lambda kwargs, bounded=False: pd.DataFrame(rows))
    monkeypatch.setattr(A, "linkedin_apply_url", lambda *a: None)
    calls = {"board": [], "desc": []}
    def company_board(company, *a, **k):
        calls["board"].append(company)
        return (("Security Engineer", f"https://boards.example/{company}/1", ""),) if company == "Acme" else ()
    monkeypatch.setattr("ats_schema.company_board", company_board)
    monkeypatch.setattr(A, "_linkedin_description", lambda i: calls["desc"].append(i) or "We offer visa sponsorship.")
    return calls


def li(i, title="Security Engineer", company="Acme"):
    return {"site": "linkedin", "title": title, "company": company, "job_url": f"https://www.linkedin.com/jobs/view/{i}"}


CFG = {"search": {"required_title_terms": ["security"]}, "jobspy": {"sites": ["linkedin"], "workers": 1, "locations": ["Germany"]}}


def test_linkedin_costs_requests_only_for_fitting_new_postings(monkeypatch):
    calls = _collect_env(monkeypatch, [li(111111), li(222222, "Nurse"), li(333333, company="NoBoard"), li(444444)], known_ids=[444444])
    out = A.jobspy_collect(["security"], 20, CFG)["security"]
    assert [j["url"] for j in out] == ["https://boards.example/Acme/1"]
    assert out[0]["description"] == "We offer visa sponsorship."
    assert sorted(calls["board"]) == ["Acme", "NoBoard"]      # nurse and the stored posting were never looked up
    assert calls["desc"] == ["111111"]                        # description only for the posting that has a company form


def test_second_run_answers_from_the_disk_cache(monkeypatch):
    calls = _collect_env(monkeypatch, [li(111111), li(333333, company="NoBoard")])
    A.jobspy_collect(["security"], 20, CFG)
    first = {k: len(v) for k, v in calls.items()}
    monkeypatch.setattr(A, "_full_list_cache", {})
    again = A.jobspy_collect(["security"], 20, CFG)["security"]
    assert {k: len(v) for k, v in calls.items()} == first     # no new board or description request
    assert len(again) == 1 and again[0]["description"]


def test_sponsorship_is_required_when_configured(monkeypatch):
    calls = _collect_env(monkeypatch, [li(111111)])
    monkeypatch.setattr(A, "_linkedin_description", lambda i: "Local candidates only, no sponsorship.")
    monkeypatch.setattr(A, "offers_sponsorship", lambda job: False)
    monkeypatch.setattr(A, "visa_free_location", lambda job: False)
    monkeypatch.setattr("sponsors.sponsorship_waived", lambda *a, **k: False)
    cfg = {**CFG, "apply": {"require_sponsorship": True}}
    assert A.jobspy_collect(["security"], 20, cfg)["security"] == []


def test_batched_keywords_share_one_pass(monkeypatch):
    calls = []
    def scrape(kwargs, bounded=False):
        calls.append(kwargs["search_term"])
        return pd.DataFrame([{"site": "indeed", "title": "Security Engineer", "company": "Acme",
                              "job_url_direct": "https://jobs.test/" + kwargs["search_term"]}])
    monkeypatch.setattr(A, "_jobspy_scrape", scrape)
    monkeypatch.setattr(A, "_jobspy_lenient_countries", lambda: None)
    monkeypatch.setattr(A, "_full_list_cache", {})
    A._JOBSPY_PRE.clear()
    cfg = {"active_persona": "appsec", "jobspy": {"sites": ["indeed"], "locations": ["Germany"], "workers": 1,
                                                  "prefetch_keywords": ["a", "b"]}}
    assert [j["url"] for j in A.jobspy_source("a", 5, cfg)] == ["https://jobs.test/a"]
    assert [j["url"] for j in A.jobspy_source("b", 5, cfg)] == ["https://jobs.test/b"]
    assert sorted(calls) == ["a", "b"]                        # the second keyword was already fetched
