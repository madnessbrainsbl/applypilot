import autopilot as A

assert A._strip_html("&lt;span style=\"font-weight:700\"&gt;Kubernetes&lt;/span&gt; security") == \
    "Kubernetes security", "escaped markup must not survive into the text"
assert A._strip_html("<style>.a{font-weight:700}</style><p>Terraform</p>") == "Terraform"
assert A._strip_html("<div><p>AWS &amp; GCP</p></div>") == "AWS & GCP"
assert A._strip_html(None) == ""

t = A._ats_terms("Kubernetes and Terraform with the AWS cloud, Kubernetes again")
assert t["kubernetes"] == 2
assert "terraform" in t and "aws" in t
assert "and" not in t and "the" not in t and "with" not in t

assert A._ats_terms("we use Go")["go"] == 1, "short whitelisted tech terms must survive"
assert "we" not in A._ats_terms("we use Go")

assert A._is_skill("kubernetes")
assert A._is_skill("aws serverless")
assert not A._is_skill("customers")
assert not A._is_skill("better")

cv = "experienced engineer using terraform, kubernetes and python for ci/cd security"
assert A._cv_contains(cv, "terraform")
assert A._cv_contains(cv, "python")
assert not A._cv_contains(cv, "rust")
assert not A._cv_contains(cv, "java"), "substring of 'javascript'-like words must not false-match"

cv2 = "javascript developer"
assert not A._cv_contains(cv2, "java"), "java must not match inside javascript"
assert A._cv_contains(cv2, "javascript")

job = {"id": "x", "company": "Acme Security", "title": "Security Engineer",
       "description": "<p>We need Terraform, Kubernetes, Rust and BGP. Acme Security is great.</p>",
       "cv": None}
cfg = {"documents": {"appsec_cv": "AppSec.pdf"}}
A.extract_cv_text = lambda name: "security engineer with python, aws, docker and kubernetes experience"
r = A.ats_gap(job, cfg, top=10)
missing = {m["term"] for m in r["missing_skills"]}
assert "rust" in missing or "bgp" in missing, "genuinely absent skills must be reported"
assert not any("acme" in m["term"] for m in r["missing_skills"]), \
    "the employer's own name must not be reported as a missing skill"
assert r["match_pct"] >= 0 and r["skill_match_pct"] is not None

empty = A.ats_gap({"id": "y", "company": "C", "title": "T", "description": "", "cv": None}, cfg)
assert "error" in empty, "a job with no description must report an error, not a fake score"

print("ats self-check OK")
