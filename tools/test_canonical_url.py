import autopilot as A

j1 = "https://jooble.org/desc/-2577595077859535912?ckey=medtech+cybersecurity&rgn=55180&pos=8&groupId=36617&jobAge=119"
j2 = "https://jooble.org/desc/-2577595077859535912?ckey=clinical+cybersecurity&rgn=55180&pos=9&groupId=99&jobAge=143"
assert A.canonical_url(j1) == A.canonical_url(j2), \
    "the same posting reached through two searches must collapse to one URL"
assert A.sha(A.canonical_url(j1)) == A.sha(A.canonical_url(j2))
assert A.canonical_url(j1) == "https://jooble.org/desc/-2577595077859535912"

gh = "https://boards.greenhouse.io/cloudflare/jobs/8081027?gh_jid=8081027"
assert A.canonical_url(gh) == gh, "a parameter that identifies the job must be kept"

lv = "https://jobs.lever.co/acme/abc-123?lever-origin=applied&utm_source=indeed"
assert "utm_source" not in A.canonical_url(lv)
assert "lever-origin" in A.canonical_url(lv), "unknown params are kept — only tracking is stripped"

assert A.canonical_url("https://acme.com/jobs/1") == "https://acme.com/jobs/1"
assert A.canonical_url("") == ""
assert A.canonical_url(None) == ""

a = "https://x.com/j/1?a=1&utm_campaign=x"
b = "https://x.com/j/1?a=1"
assert A.canonical_url(a) == A.canonical_url(b)

c = "https://x.com/j/1?a=1"
d = "https://x.com/j/2?a=1"
assert A.canonical_url(c) != A.canonical_url(d), "different postings must stay distinct"

gh1 = "http://job-boards.greenhouse.io/acme/jobs/512?gh_src=f89fe4c48us"
gh2 = "https://job-boards.greenhouse.io/acme/jobs/512"
gh3 = "https://Job-Boards.Greenhouse.io/acme/jobs/512/"
assert A.canonical_url(gh1) == A.canonical_url(gh2) == A.canonical_url(gh3) == gh2,     "scheme, host case, trailing slash and gh_src do not change which posting this is"

print("canonical url self-check OK")
