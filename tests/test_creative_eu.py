import json

import creative_sources as C
import sponsors

GH = {"jobs": [
    {"title": "Photographer (m/f/d)", "absolute_url": "https://boards.greenhouse.io/x/jobs/1", "location": {"name": "Berlin, Germany"},
     "content": "&lt;p&gt;Shoot &lt;b&gt;fashion&lt;/b&gt;&lt;/p&gt;"},
    {"title": "Video Editor", "absolute_url": "https://boards.greenhouse.io/x/jobs/2", "location": {"name": "Austin, TX"}, "content": ""},
    {"title": "Account Manager", "absolute_url": "https://boards.greenhouse.io/x/jobs/3", "location": {"name": "Paris, France"}, "content": ""},
]}
RSS = """<?xml version="1.0"?><rss><channel><item><title>Videographer</title><link>https://a.teamtailor.com/jobs/9</link>
<description>&lt;p&gt;Film&lt;/p&gt;</description></item></channel></rss>"""


def classify(t):
    return "creative" if any(w in t.lower() for w in ("photograph", "video")) else None


def test_discover_parses_boards_and_keeps_only_european_creative(monkeypatch):
    def get(url, timeout=12):
        if "greenhouse" in url:
            return json.dumps(GH)
        if "teamtailor" in url:
            return RSS
        raise OSError("no such board")
    monkeypatch.setattr(C, "_get", get)
    jobs = C.discover({}, classify, lambda *a, **k: "", sponsors.europe_location,
                      boards={"greenhouse": ["acme"], "teamtailor": ["a"], "lever": ["dead"]}, feeds={})
    got = {(j["title"], j["location"], j["src"]) for j in jobs}
    assert got == {("Photographer (m/f/d)", "Berlin, Germany", "greenhouse:acme"), ("Videographer", "", "teamtailor:a")}
    assert jobs[0]["company"] == "Acme" and jobs[0]["description"] == "Shoot fashion"
    assert C.SUMMARY == {"greenhouse:acme": 1, "teamtailor:a": 1}   # the dead board is absent, not zero
