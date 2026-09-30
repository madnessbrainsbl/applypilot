import json
import pathlib

import ats_schema as S
import autopilot as A

CFG = A.read_json(A.CFG)
FIX = pathlib.Path(__file__).with_name("fixtures")


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _ko(schema):
    return S.knockout(schema, lambda l, o: A.offline_answer(l, o, CFG), A._HARD_DISQUALIFY_PHRASES)


def demo():
    # URL routing — no network: an unsupported host never reaches the HTTP call
    assert S._route("https://job-boards.greenhouse.io/anthropic/jobs/5250063008") == ("greenhouse", "anthropic", "5250063008")
    assert S._route("https://boards.greenhouse.io/cloudflare/jobs/8084212?gh_jid=8084212")[1:] == ("cloudflare", "8084212")
    assert S._route("https://job-boards.greenhouse.io/embed/job_app?for=elastic&token=8161837")[1:] == ("elastic", "8161837")
    assert S._route("https://jobs.elastic.co/jobs?gh_jid=8173299") is None, "no board to ask on a custom domain"
    assert S._route("https://jobs.ashbyhq.com/openai/36e30720-0a32-4b7b-8172-e31ad0a20cdc/application") == \
        ("ashby", "openai", "36e30720-0a32-4b7b-8172-e31ad0a20cdc")
    assert S._route("https://apply.workable.com/our-future-health/j/88E2E3A427/")[2] == "88E2E3A427"
    assert S._route("https://apply.workable.com/j/FA6B9FB59E")[2] == "FA6B9FB59E"
    assert S.fetch_schema("https://jobs.lever.co/acme/123") is None

    gh = {q["label"]: q for q in S.normalize_greenhouse(_load("greenhouse_job.json"))}
    assert gh["First Name"] == {"label": "First Name", "required": True, "type": "text", "options": [], "name": "first_name"}
    assert gh["Resume/CV"]["type"] == "file"
    assert gh["Why Anthropic?"]["type"] == "textarea"
    assert gh["Do you require visa sponsorship?"]["type"] == "yesno", "label trimmed, Yes/No select is a yesno"
    multi = next(q for l, q in gh.items() if l.startswith("Which of these"))
    assert multi["type"] == "multiselect" and "SOC 2 Type II" in multi["options"]
    assert gh["VeteranStatus"]["required"] is False, "compliance questions are included"

    ash = {q["label"]: q for q in S.normalize_ashby(_load("ashby_job.json"))}
    poly = ash["Do you have an active Full Scope Polygraph clearance?"]
    assert poly["type"] == "yesno" and poly["options"] == ["Yes", "No"] and poly["required"]
    assert ash["Resume"]["type"] == "file" and ash["Legal Name"]["name"] == "_systemfield_name"
    assert ash["Applicant Arbitration Agreement Acknowledgement"]["type"] == "multiselect"
    assert S.normalize_ashby({"data": {"jobPosting": None}}) is None, "a dead posting is not an empty form"

    wk = S.normalize_workable(_load("workable_form.json"))
    assert not any(q["type"] == "group" for q in wk)
    assert any(q["name"] == "gdpr" and q["type"] == "yesno" for q in wk), "unlabelled field falls back to its id"

    # pre-flight knock-outs
    assert _ko(list(ash.values())).startswith("requires an active security clearance")
    assert "unable to offer visa sponsorship" in _ko(wk), "Workable's own right-to-work gate"
    assert _ko(list(gh.values())) is None, "a plain 'do you require sponsorship?' is not a knock-out"

    def one(label, required=True, kind="yesno", options=("Yes", "No")):
        return [{"label": label, "required": required, "type": kind, "options": list(options), "name": "q"}]
    assert _ko(one("Are you willing to obtain a security clearance?")) is None
    assert _ko(one("Do you have an active security clearance?", required=False)) is None
    assert _ko(one("Active Security Clearance(s)", kind="multiselect", options=("None", "Secret"))) is None
    assert _ko(one("Are you a US citizen?")) is None, "asked, not required: no knock-out"
    assert _ko(one("This role requires U.S. citizenship (ITAR). Are you a US citizen?")) == \
        "requires US citizenship: This role requires U.S. citizenship (ITAR). Are you a US citizen?"
    assert S.knockout(one("Do you hold an active TS/SCI clearance?"), lambda l, o: "Yes") is None, \
        "a profile that truthfully answers Yes is never knocked out"
    assert _ko(None) is None
    print("ats schema self-check OK")


if __name__ == "__main__":
    demo()
