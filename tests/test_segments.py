import autopilot as A


def demo():
    j = {"id": "1", "persona": "creative", "title": "Junior Photographer", "location": "Dubai, UAE",
         "source": "jobspy", "description": "Startup (YC W24, team 12)"}
    assert A.segment_of(j) == "creative/junior/other/jobspy/<50"
    assert A.job_level("Senior Security Engineer") == "senior+" and A.job_level("Security Engineer") == "mid"
    assert A.job_level("Associate Director of Security") == "senior+", "senior words win over junior ones"
    for loc, region in [("London, England, UK", "UK"), ("Amsterdam", "NL"), ("Berlin, Berlin, Germany", "DE"),
                        ("Warsaw, Poland", "other EU"), ("San Francisco, CA", "US"), ("Remote - US", "US"),
                        ("Remote", "remote"), ("Belgrade, Serbia", "other"), ("Tbilisi, Georgia", "other"),
                        ("Atlanta, Georgia, United States", "US"), ("Bangkok, Thailand", "other"),
                        ("Nis, Serbia", "other")]:
        assert A.region_bucket({"location": loc}) == region, (loc, A.region_bucket({"location": loc}))
    assert A.region_bucket({"location": "Washington, DC"}) == "US"
    assert A.company_size({"description": "we are 1,200 employees"}) == "500+"
    assert A.company_size({"description": "great team spirit"}) == "?"

    # auto-pause: >= 30 applications and no interview/assessment/offer
    def jobs(n, src, start=0):
        return [{**j, "id": f"{src}{i}", "source": src} for i in range(start, start + n)]
    dead, alive, small = jobs(30, "dead"), jobs(30, "alive"), jobs(29, "small")
    outcomes = [{"job_id": "dead0", "status": "rejected"}, {"job_id": "alive3", "status": "interview"},
                {"job_id": "alive3", "status": "rejected"}]
    stats = {s["source"]: s for s in A.segment_stats(dead + alive + small, outcomes)}
    assert stats["dead"]["applied"] == 30 and stats["dead"]["rejected"] == 1 and stats["dead"]["positive"] == 0
    assert stats["alive"]["positive"] == 1 and stats["alive"]["rejected"] == 0, "an interview outweighs a later rejection"
    assert stats["alive"]["response_rate"] == round(1 / 30, 3)
    paused = A.segments_to_pause(list(stats.values()))
    assert paused == [stats["dead"]["segment"]], paused

    cfg = {"apply": {"paused_segments": paused}}
    assert A.segment_paused(dead[0], cfg) == stats["dead"]["segment"]
    assert not A.segment_paused(alive[0], cfg) and not A.segment_paused(dead[0], {"apply": {}})
    print("segments self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
