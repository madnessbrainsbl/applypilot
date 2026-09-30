import autopilot as A
from datetime import datetime, timezone, timedelta

NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
FULL = "x" * 900

def job(**kw):
    base = {"company": "Acme", "title": "Security Engineer", "source": "vendor",
            "description": FULL, "url": "https://acme.com/jobs/1",
            "created_at": (NOW - timedelta(days=3)).isoformat()}
    base.update(kw)
    return base

fresh = A.ghost_signals(job(), now_dt=NOW)
assert fresh["risk"] == 0, f"a fresh, specific, unique posting must be clean, got {fresh}"

old = A.ghost_signals(job(created_at=(NOW - timedelta(days=100)).isoformat()), now_dt=NOW)
assert old["risk"] >= 35 and any("open" in r for r in old["reasons"])

inclusive = ("We are always looking for people who bring something new. "
             "Required: 5+ years of application security experience. " + FULL)
r = A.ghost_signals(job(description=inclusive), now_dt=NOW)
assert r["risk"] == 0, \
    "inclusive-hiring boilerplate inside a real posting must not be called a ghost job"

r = A.ghost_signals(job(title="General Application - Security"), now_dt=NOW)
assert r["risk"] >= 60 and any("pipeline" in x for x in r["reasons"]), \
    "an open/pipeline application title is the real evergreen signal"

r = A.ghost_signals(job(description="Join our talent pool for future roles. " + FULL), now_dt=NOW)
assert any("talent-pool" in x for x in r["reasons"])

snippet = job(source="jooble", description="short blurb")
assert not any("thin" in x for x in A.ghost_signals(snippet, now_dt=NOW)["reasons"]), \
    "aggregators return excerpts by design; that is data shape, not a ghost signal"
assert any("thin" in x for x in A.ghost_signals(job(description="short blurb"), now_dt=NOW)["reasons"]), \
    "a full-text source with a tiny description is genuinely suspicious"

u = "https://jooble.org/desc/1?ckey=x&jobAge=215&pos=1"
r = A.ghost_signals(job(source="jooble", url=u, description="short blurb"), now_dt=NOW)
assert r["age_days"] == 215 and r["age_source"] == "board", \
    "the board's own posting age must win over the date we first saw it"

reposts = {A.role_key("Acme", "Security Engineer"): 18}
r = A.ghost_signals(job(), now_dt=NOW, reposts=reposts)
assert r["risk"] >= 45 and r["reposts"] == 18

r = A.ghost_signals(job(), now_dt=NOW, reposts={A.role_key("Acme", "Security Engineer"): 2})
assert r["risk"] == 0, "two listings of a role is normal, not a ghost signal"

assert A.role_key("Acme Ltd.", "Security Engineer") == A.role_key("acme  ltd", "SECURITY ENGINEER!")

assert A.ghost_signals(job(created_at=None, description=FULL), now_dt=NOW)["age_days"] is None

print("ghost self-check OK")
