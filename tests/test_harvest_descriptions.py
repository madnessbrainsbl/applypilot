"""Board harvests store the posting text, not a placeholder; backfill matches rows to
their board and marks vanished postings dead. Fixture payloads, no network."""
import harvest_ats_boards as H

GH = {"jobs": [{"title": " Application Security Engineer ", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                "location": {"name": "Berlin"},
                "content": "&lt;p&gt;We offer &amp;amp; visa sponsorship.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Burp&lt;/li&gt;&lt;/ul&gt;"}]}
ASHBY = {"jobs": [{"title": "Product Security Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/u-1",
                   "location": "Remote", "descriptionPlain": "", "descriptionHtml": "<p>Threat modeling</p>"}]}
LEVER = [{"text": "Security Engineer", "hostedUrl": "https://jobs.lever.co/acme/u-2", "categories": {"location": "London"},
          "descriptionPlain": "About us.", "lists": [{"text": "Requirements", "content": "<li>SAST</li><li>DAST</li>"}],
          "additionalPlain": "Relocation package."}]


def test_board_descriptions():
    payloads = {"greenhouse": GH, "ashby": ASHBY, "lever": LEVER}
    real_get = H._get
    try:
        H._get = lambda url, timeout=20: next(v for k, v in payloads.items() if k in url)
        gh = H.fetch_greenhouse("acme")
        assert gh[0]["description"] == "We offer & visa sponsorship.\nBurp", gh[0]["description"]
        assert gh[0]["title"] == "Application Security Engineer" and gh[0]["persona"] == "appsec"
        assert H.fetch_ashby("acme")[0]["description"] == "Threat modeling", "falls back to descriptionHtml"
        assert H.fetch_lever("acme")[0]["description"] == "About us.\nRequirements\nSAST\nDAST\nRelocation package."

        rows = [{"id": "a", "url": "https://job-boards.greenhouse.io/acme/jobs/1", "title": "x"},
                {"id": "b", "url": "https://job-boards.greenhouse.io/acme/jobs/9", "title": "Application Security Engineer"},
                {"id": "c", "url": "https://job-boards.greenhouse.io/acme/jobs/7", "title": "Gone Role"}]
        found, dead = H._refresh_board("greenhouse", "acme", rows)
        assert set(found) == {"a", "b"} and dead == ["c"], (found, dead)
    finally:
        H._get = real_get
    assert H.board_of({"url": "https://boards.greenhouse.io/figma/jobs/5", "source": "greenhouse"}) == ("greenhouse", "figma")
    assert H.board_of({"url": "https://stripe.com/jobs/search?gh_jid=7", "company": "Stripe",
                       "source": "yc_startups"}) == ("greenhouse", "stripe")
    assert H.board_of({"url": "https://jobs.smartrecruiters.com/Ubisoft2/7440", "source": "smartrecruiters"}) == \
        ("smartrecruiters", "Ubisoft2")
    assert H.board_of({"url": "https://news.ycombinator.com/item?id=1", "source": "hackernews"}) is None
    print("harvest description self-check OK")


if __name__ == "__main__":
    test_board_descriptions()
