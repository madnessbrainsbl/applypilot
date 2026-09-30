import autopilot as A


def demo():
    cfg = {"personas": {
        "appsec": {"cv": "AppSec.pdf", "search": {"required_title_terms": ["security", "appsec"],
                                                  "excluded_title_terms": ["physical security"]}},
        "doctor": {"cv": "autopilot/resumes/doctor.pdf", "search": {"required_title_terms": ["physician", "doctor", "medical officer"],
                                                                     "excluded_title_terms": ["doctoral"]}},
    }, "documents": {"appsec_cv": "AppSec.pdf"}}

    assert A.classify_persona("Senior Application Security Engineer", cfg) == "appsec"
    assert A.classify_persona("Medical Officer / Physician", cfg) == "doctor", "a third profession must be reachable"
    assert A.classify_persona("Doctoral Researcher", cfg, "appsec") == "appsec", "excluded terms win"
    assert A.classify_persona("Physical Security Guard", cfg) is None

    assert A.pick_cv({"title": "Physician", "persona": "doctor"}, cfg) == "autopilot/resumes/doctor.pdf"
    assert A.pick_cv({"title": "Physician"}, cfg) == "autopilot/resumes/doctor.pdf", "persona from the title"
    assert A.pick_cv({"title": "AppSec Engineer", "persona": "appsec"}, cfg) == "AppSec.pdf"

    real = A.llm_complete
    try:
        A.llm_complete = lambda *a, **k: 'noise {"name": "Physician", "role_title": "General Practitioner", ' \
                                         '"keywords": ["general practitioner"], "required_title_terms": ["GP", "physician"]} tail'
        prof = A.resume_search_profile("x" * 300, "", {"llm": {"providers": [{"name": "f", "kind": "openai", "url": "u"}]}})
        assert prof["role_title"] == "General Practitioner"
        assert prof["search"]["required_title_terms"] == ["gp", "physician"], "title terms are matched lowercase"
        A.llm_complete = lambda *a, **k: ""
        prof = A.resume_search_profile("x" * 300, "Video Editor", {"llm": {"providers": []}})
        assert prof["search"]["keywords"] == ["Video Editor"] and prof["search"]["required_title_terms"] == ["video", "editor"]
    finally:
        A.llm_complete = real
    print("resume library self-check OK")


if __name__ == "__main__":
    demo()
