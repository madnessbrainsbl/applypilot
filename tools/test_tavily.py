import autopilot as a

CFG = a.read_json(a.CFG)

WORTH_AI = "\n".join(["Image 1: Worth AI", "Security Engineer", "RemoteIT Full time",
                      "Orlando, Florida, United States", "Atlanta, Georgia, United States", "Overview"])
XO = "\n".join(["XO Logo", "Cyber Security Engineer",
                "XO believes healthcare is fixable and we are growing a multi-disciplinary team",
                "CyberSecurity & Infrastructure Engineer - India (Remote)", "About the Role:"])
BERLIN = "\n".join(["Acme", "Security Engineer", "Berlin, Germany", "We are a company"])
PROSE = ("Acme is a global platform trusted by teams in the United States and Europe "
         "to ship software safely every single day of the week")


def demo():
    j = a._tavily_job({"url": "https://boards.greenhouse.io/acme-labs/jobs/4123456",
                       "title": "Job Application for Senior Security Engineer at Acme Labs",
                       "raw_content": "full posting text"})
    assert j["company"] == "Acme Labs", j
    assert j["title"] == "Senior Security Engineer", j
    assert j["description"] == "full posting text"

    j = a._tavily_job({"url": "https://jobs.lever.co/nord/9f2c-1", "title": "Nord - Application Security Analyst",
                       "content": "snippet"})
    assert j["title"] == "Application Security Analyst", j
    assert j["description"] == "snippet"

    j = a._tavily_job({"url": "https://jobs.ashbyhq.com/BigCo/uuid", "title": "Product Security Engineer @ BigCo"})
    assert (j["company"], j["title"]) == ("BigCo", "Product Security Engineer"), j

    j = a._tavily_job({"url": "https://job-boards.greenhouse.io/boxinc/jobs/812",
                       "title": "Security Engineer II at Box"})
    assert (j["company"], j["title"]) == ("Box", "Security Engineer II"), j

    j = a._tavily_job({"url": "https://apply.workable.com/cloudlinux-1/j/BB0",
                       "title": "Security Engineer - Node.js (remote)"})
    assert (j["company"], j["title"]) == ("Cloudlinux", "Security Engineer - Node.js (remote)"), j

    j = a._tavily_job({"url": "https://apply.workable.com/foo/j/ABC123/", "title": "Security Engineer - Remote"})
    assert j["title"] == "Security Engineer - Remote", j

    assert a._tavily_job({"url": "https://boards.greenhouse.io/acme"}) is None
    assert a._tavily_job({"url": "https://boards.greenhouse.io/embed/job_app"}) is None

    j = a._tavily_job({"url": "https://job-boards.greenhouse.io/clearviewhealthcarepartners/jobs/5",
                       "title": "IT Security Engineer at ClearView Healthcare"})
    assert (j["company"], j["title"]) == ("ClearView Healthcare", "IT Security Engineer"), j

    j = a._tavily_job({"url": "https://jobs.lever.co/acme/1", "title": "Security Engineer - Remote"})
    assert (j["company"], j["title"]) == ("Acme", "Security Engineer - Remote"), j

    j = a._tavily_job({"url": "https://apply.workable.com/worthai/j/CA9", "title": "Security Engineer",
                       "raw_content": WORTH_AI})
    assert j["location"].startswith("Orlando, Florida, United States"), j
    assert a.score(dict(j, keyword=""), CFG) < 0, "a US-only posting must not outrank a real target"

    j = a._tavily_job({"url": "https://job-boards.greenhouse.io/xo/jobs/5", "title": "Cyber Security Engineer",
                       "raw_content": XO})
    assert "India" in j["location"], j

    j = a._tavily_job({"url": "https://jobs.lever.co/acme/3", "title": "Security Engineer", "raw_content": BERLIN})
    assert j["location"] == "Berlin, Germany", j
    assert a.score(dict(j, keyword=""), CFG) > 0, "a Berlin posting must survive the off-target penalty"

    j = a._tavily_job({"url": "https://jobs.lever.co/acme/4", "title": "Security Engineer", "raw_content": PROSE})
    assert j["location"] == "", "prose must not be mistaken for a location"

    assert a.tavily_source("x", 5, {"api_keys": {}}) == []
    print("ok")


if __name__ == "__main__":
    demo()
