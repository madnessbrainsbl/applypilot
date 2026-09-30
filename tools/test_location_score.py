import autopilot as A


def demo():
    cfg = A.read_json(A.CFG)
    def sc(loc, title="Security Engineer"):
        return A.score({"title": title, "company": "X", "location": loc, "description": "", "persona": "appsec"}, cfg)
    assert sc("Remote - US") < 0 and sc("US Remote") < 0, "US-only remote is off target"
    assert sc("US/EU - Remote") > 0, "a role open to the EU is not"
    assert sc("São Paulo") < 0, "accented city names are still recognised"
    assert sc("Remote (EMEA)") > 0 and sc("Amsterdam, Netherlands") > 0
    assert sc("Amsterdam, Netherlands", "Senior Security Engineer") < 0, "junior focus: senior titles are excluded"
    print("location score self-check OK")


if __name__ == "__main__":
    demo()
