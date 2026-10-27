"""osint_sources parsers and discover() wiring, no network: _get and the ATS fetchers are faked."""
import base64
import json
import urllib.error

import creative_sources as CS
import osint_sources as O
import sponsors


def classify(t):
    t = t.lower()
    if any(w in t for w in ("senior", "lead")):
        return None
    if any(w in t for w in ("photograph", "fotograf", "video")):
        return "creative"
    if any(w in t for w in ("security", "pentest", "penetration")):
        return "appsec"
    return None


def cfg(persona, **disc):
    return {"active_persona": persona, "discovery": disc}


def fake_get(routes):
    def get(url, data=None, headers=None, timeout=30, gap=1.0):
        for needle, body in routes.items():
            if needle in url:
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, str) else json.dumps(body)
        raise OSError("no route " + url)
    return get


def run(name, config, routes, monkeypatch, tmp_path):
    monkeypatch.setattr(O, "CACHE", tmp_path)
    monkeypatch.setattr(O, "DB", tmp_path / "no.db")
    monkeypatch.setattr(O, "_get", fake_get(routes))
    import employer_url
    monkeypatch.setattr(employer_url, "resolve_listing", lambda *a, **k: None)   # employer lookup: test_employer_url
    O._MEMO.clear()
    return O.discover(name, config, classify, sponsors.europe_location)


def test_arbeitsagentur_keeps_work_offers_of_the_persona_and_caches_details(monkeypatch, tmp_path):
    listing = {"maxErgebnisse": 3, "ergebnisliste": [
        {"referenznummer": "R1", "stellenangebotsTitel": "Fotograf/in (m/w/d)", "stellenangebotsart": "ARBEIT", "firma": "Foto GmbH",
         "stellenlokationen": [{"adresse": {"ort": "Mannheim", "region": "BADEN_WUERTTEMBERG"}}]},
        {"referenznummer": "R2", "stellenangebotsTitel": "Fotograf Azubi", "stellenangebotsart": "AUSBILDUNG", "firma": "X"},
        {"referenznummer": "R3", "stellenangebotsTitel": "Buchhalter", "stellenangebotsart": "ARBEIT", "firma": "Y"}]}
    detail = {"stellenangebotsBeschreibung": "Wir suchen **Fotografie**", "externeURL": "https://emp.example/job/1", "homeofficemoeglich": True}
    routes = {"/v6/jobs": listing, "/v4/jobdetails/" + base64.b64encode(b"R1").decode(): detail}
    out = run("arbeitsagentur", cfg("creative", arbeitsagentur={"queries": {"creative": ["Fotograf"]}}), routes, monkeypatch, tmp_path)
    assert len(out) == 1
    j = out[0]
    assert j["url"] == "https://www.arbeitsagentur.de/jobsuche/jobdetail/R1" and j["company"] == "Foto GmbH"
    assert j["location"] == "Mannheim, Baden Wuerttemberg, Germany (home office possible)"
    assert "https://emp.example/job/1" in j["description"]
    assert "R1" in json.loads((tmp_path / "osint_arbeitsagentur.json").read_text())   # detail cached: next run asks no jobdetails
    # second run: the detail endpoint is gone, the cached description is still used
    out = run("arbeitsagentur", cfg("creative", arbeitsagentur={"queries": {"creative": ["Fotograf"]}}), {"/v6/jobs": listing}, monkeypatch, tmp_path)
    assert "Fotografie" in out[0]["description"]


def test_eu_portals_skips_de_and_maps_country_codes(monkeypatch, tmp_path):
    jvs = {"jvs": [
        {"id": "a1", "title": "Junior Photographer", "employer": {"name": "Prop Ltd"}, "locationMap": {"MT": ["MT001"]}, "description": "Shoot <br> things"},
        {"id": "a2", "title": "Photographer", "employer": None, "locationMap": {"DE": ["DE300"]}, "description": ""},
        {"id": "a3", "title": "Photographer", "employer": None, "locationMap": {"NL": ["NL366"]}, "description": ""}]}
    out = run("eu_portals", cfg("creative", eu_portals={"queries": {"creative": ["photographer"]}}), {"jv-search/search": jvs}, monkeypatch, tmp_path)
    assert [(j["company"], j["location"]) for j in out] == [("Prop Ltd", "Malta"), ("", "Netherlands")]
    assert out[0]["url"].endswith("/jv-details/a1") and out[0]["description"] == "Shoot\nthings"


def test_hn_hiring_header_parse_location_and_persona(monkeypatch, tmp_path):
    def c(i, text):
        return {"id": i, "text": text}
    tree = {"children": [
        c(1, 'Acme | Product Security Engineer | Berlin, Germany | REMOTE<p>Apply <a href="https://boards.greenhouse.io/acme/jobs/1">here</a>'),
        c(2, "Foo | Photographer | Austin, TX (US only)<p>on-site"),
        c(3, "Bar | Senior Security Engineer | London"),
        c(4, "SEEKING WORK: videographer | Paris"),
        c(5, "Baz | Backend dev | Paris")]}
    hits = {"hits": [{"objectID": "99", "title": "Ask HN: Who is hiring? (October 2026)", "created_at_i": int(__import__("time").time())},
                     {"objectID": "98", "title": "Ask HN: Who wants to be hired?", "created_at_i": int(__import__("time").time())}]}
    routes = {"search_by_date": hits, "/items/99": tree, "jobstories.json": [7], "/item/7.json": {"title": "Zed (YC S24) Is Hiring a Videographer", "url": "https://zed.example/jobs"}}
    out = run("hn_hiring", cfg("appsec"), routes, monkeypatch, tmp_path)
    assert [(j["company"], j["title"], j["url"]) for j in out] == [("Acme", "Product Security Engineer", "https://boards.greenhouse.io/acme/jobs/1")]
    cre = run("hn_hiring", cfg("creative"), routes, monkeypatch, tmp_path)   # US-only Foo is dropped, YC story kept
    assert [(j["company"], j["title"]) for j in cre] == [("Zed", "Videographer")]


def test_dorks_slugs_from_index_and_board_scan(monkeypatch, tmp_path):
    host = {"ats": "greenhouse", "pattern": "job-boards.greenhouse.io/*", "rx": r"job-boards\.greenhouse\.io/([a-z0-9][\w-]+)"}
    text = '{"url": "https://job-boards.greenhouse.io/newco/jobs/1"}\n{"url": "https://job-boards.greenhouse.io/embed/job_app"}\n{"url": "https://job-boards.greenhouse.io/OtherCo"}'
    assert O.slugs_from_index(text, host) == {"newco", "otherco"}

    cc = {"collinfo.json": [{"cdx-api": "https://index.commoncrawl.org/CC-X-index"}],
          "showNumPages": {"pages": 1}, "CC-X-index": '{"url": "https://job-boards.greenhouse.io/newco/jobs/1"}\n{"url": "https://job-boards.greenhouse.io/gone/jobs/2"}'}
    gh = {"jobs": [{"title": "Videographer", "absolute_url": "https://job-boards.greenhouse.io/newco/jobs/5", "location": {"name": "Madrid, Spain"}, "content": "<p>Film</p>"},
                   {"title": "Videographer", "absolute_url": "https://job-boards.greenhouse.io/newco/jobs/6", "location": {"name": "Austin, TX"}, "content": ""}]}

    def board(slug):
        if slug == "gone":
            raise urllib.error.HTTPError("u", 404, "nf", {}, None)
        return [CS._row(j["title"], j["absolute_url"], j["location"]["name"], CS._text(j["content"])) for j in gh["jobs"]]
    monkeypatch.setitem(CS.FETCHERS, "greenhouse", board)
    monkeypatch.setattr(O, "_seed", lambda f: {"osint_feeds.json": {"ats_hosts": [host]}}.get(f, {}))
    config = cfg("creative", dorks={"gap": 0})
    config["vendor_boards"] = {"greenhouse": ["knownco"]}
    out = run("dorks", config, cc, monkeypatch, tmp_path)
    assert [(j["company"], j["location"], j["src"]) for j in out] == [("Newco", "Madrid, Spain", "greenhouse:newco")]
    cache = json.loads((tmp_path / "osint_dorks.json").read_text())
    assert cache["boards"]["greenhouse:gone"]["dead"] and cache["boards"]["greenhouse:newco"]["hits"] == 2


ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>[HIRING] Videographer for fashion label</title>
<link href="https://www.reddit.com/r/forhire/comments/x/"/><author><name>/u/boss</name></author>
<content type="html">&lt;p&gt;Paid gig, apply &lt;a href="https://label.example/jobs/video"&gt;here&lt;/a&gt;&lt;/p&gt;</content></entry>
<entry><title>[FOR HIRE] Photographer</title><link href="https://www.reddit.com/r/forhire/comments/y/"/><content type="html">x</content></entry></feed>"""
RSS = """<?xml version="1.0"?><rss><channel><item><title></title><link>https://mastodon.social/@a/1</link>
<description>&lt;p&gt;#fedihire Studio X seeks a video editor. &lt;a href="https://studiox.example/careers"&gt;studiox.example/careers&lt;/a&gt;&lt;/p&gt;</description></item></channel></rss>"""


def test_community_feeds_atom_and_rss(monkeypatch, tmp_path):
    feeds = {"osint_feeds.json": {"community": [
        {"name": "reddit:forhire", "kind": "atom", "url": "https://www.reddit.com/r/forhire/new/.rss", "require": r"\[hiring\]"},
        {"name": "mastodon:fedihire", "kind": "rss", "url": "https://mastodon.social/tags/fedihire.rss"},
        {"name": "dead", "kind": "rss", "url": "https://dead.example/feed"}]}}
    monkeypatch.setattr(O, "_seed", lambda f: feeds.get(f, {}))
    routes = {"reddit.com": ATOM, "mastodon.social": RSS, "dead.example": OSError("down")}
    out = run("community", cfg("creative"), routes, monkeypatch, tmp_path)
    assert sorted((j["title"], j["url"]) for j in out) == [
        ("Studio X seeks a video editor", "https://studiox.example/careers"),
        ("Videographer for fashion label", "https://label.example/jobs/video")]
    assert "fail" in json.loads((tmp_path / "osint_community.json").read_text())["dead"]   # skipped for fail_skip_days


def test_disabled_source_does_nothing(monkeypatch, tmp_path):
    assert run("arbeitsagentur", cfg("creative", arbeitsagentur={"enabled": False}), {}, monkeypatch, tmp_path) == []


def test_shipped_seeds_are_valid():
    q, f = O._seed("osint_queries.json"), O._seed("osint_feeds.json")
    for src in ("arbeitsagentur", "eu_portals"):
        assert q[src]["creative"] and q[src]["appsec"]
    assert q["dorks"]["smartrecruiters"] and q["hn_hiring"]["creative"]
    assert {h["ats"] for h in f["ats_hosts"]} <= set(CS.FETCHERS)
    for h in f["ats_hosts"]:
        assert O.re.compile(h["rx"]).groups == 1
    assert f["community"] and all(x["url"].startswith("https://") for x in f["community"])


def test_board_seed_file_names_known_fetchers():
    seed = O._seed("ats_boards.json")
    assert set(seed) - {"_comment"} <= set(CS.FETCHERS)
    assert all(isinstance(v, list) for k, v in seed.items() if k != "_comment")
