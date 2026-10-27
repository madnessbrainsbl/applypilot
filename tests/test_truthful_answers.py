"""Truthfulness guards: experience/employment yes-no, education fields, answer-vs-question type."""
import autopilot as A

CV = 'Application Security Engineer, 5 years experience. Built Python review tools. Experience with Kubernetes hardening, security controls in CI/CD pipelines, SAST and DAST. Assessed security for financial clients. Education: Doctor of Veterinary Medicine, Springfield State Technical University, 2011-2016.'
GROUND = CV + '\n"school": "Springfield State Technical University"\n"degree": "Doctor of Veterinary Medicine (DVM)"\n"discipline": "Veterinary Medicine"\n'
YN = ["Yes", "No"]


def bad(label, ans, opts=None, company="Kraken"):
    return A._unsupported_claim({"label": label, "options": opts}, ans, GROUND, company)


def vet(label, ans, opts=YN, company="Kraken"):
    q = {"label": label, "options": opts}
    ans = A._no_for_unsupported_yes(q, ans, GROUND, company)
    return ans, bad(label, ans, opts, company)


# --- 1. experience / employment yes-no ------------------------------------------------
def test_unsupported_employment_yes_becomes_no():
    ans, why = vet("Have you worked in an insurance or reinsurance company?", "Yes")
    assert ans == "No" and not why


def test_kraken_yes_is_never_submitted():
    # assessments FOR finance clients are not employment IN a regulated financial firm
    for label in ("Have you worked in a regulated financial services, fintech or virtual asset firm?",
                  "Have you personally conducted ICT or security risk assessments and business impact assessments?",
                  "Have you worked with both Terraform and Kubernetes in production environments?"):
        ans, why = vet(label, "Yes")
        assert ans != "Yes" or why, f"unsupported affirmative must be rejected or left for review: {label}"


def test_supported_experience_yes_stays():
    for label in ("Do you have experience with Kubernetes hardening?",
                  "Do you have experience with Python?",
                  "Have you implemented or maintained security controls in CI/CD pipelines?",
                  "Do you have hands-on experience with SAST or DAST tools?"):
        assert vet(label, "Yes") == ("Yes", None), label


def test_unsupported_tools_and_ownership_are_not_yes():
    for label in ("Do you have hands-on experience with Okta or Ping Identity?",
                  "Do you have experience in the insurance sector?"):
        ans, why = vet(label, "Yes")
        assert ans == "No" and not why, label


def test_free_text_unsupported_yes_is_rejected_not_rewritten():
    assert bad("Do you have experience with Okta? If yes, briefly explain.", "Yes, I delivered Okta implementations.")


def test_no_answers_are_never_touched():
    assert vet("Have you worked in a regulated financial services firm?", "No") == ("No", None)


def test_used_the_employers_product():
    assert bad("Have you used Kraken's products?", "Yes")
    assert bad("Have you used Kraken's products?", "No"), "named product: the candidate must say"
    assert bad("Have you used our platform?", "Yes")
    assert not bad("Have you used our platform?", "No")


# --- 2. education fields ---------------------------------------------------------------
def test_education_field_takes_only_diploma_words():
    assert bad("Major", "Veterinary Medicine (DVM) & Cybersecurity")
    assert not bad("Major", "Veterinary Medicine")
    assert not bad("School", "Springfield State Technical University")


def test_appsec_discipline_has_no_skills_merged_in():
    d = A.get_persona_cfg({"profile": {"discipline": "Veterinary Medicine"}, "personas": {"appsec": {}}}, "appsec")["profile"]["discipline"]
    assert not any(w in d.lower() for w in ("cyber", "security", "devsecops")), d


def test_high_school_year_is_not_a_university_name_nor_a_guess():
    label = "What year did you graduate high school?"
    assert bad(label, "Springfield State Technical University")
    assert bad(label, "2016"), "the university year is not the school year; no school year in CV/profile"
    assert bad(label, "2008"), "not in config/CV: left blank for the human"


def test_university_graduation_year_comes_from_the_cv():
    assert not bad("What year did you graduate?", "2016")
    assert bad("What year did you graduate?", "2019")


# --- 3. generic type check -----------------------------------------------------------------
def test_type_mismatch():
    m = A._answer_type_mismatch
    assert m("What year did you graduate?", "Springfield State Technical University")
    assert not m("What year did you graduate?", "2016")
    assert m("How many years of experience do you have?", "I have worked on many projects across web and API security.")
    assert not m("How many years of experience do you have?", "5")
    assert m("Are you legally authorized to work in the US?", "Springfield State Technical University")
    assert not m("Are you legally authorized to work in the US?", "No")
    assert not m("Do you have experience with Okta? If yes, briefly explain.", "No, but I know OAuth2 well enough to learn quickly.")
    assert m("Date of birth", "I was born in a small coastal town and grew up there")
    assert not m("Date of birth", "01/02/1990")
    assert m("What year did you graduate?", "2016", ["2019", "2016"]) is None, "option lists are validated elsewhere"


def test_type_mismatch_rejects_in_the_llm_pipeline():
    assert "type mismatch" in bad("Are you open to relocation?", "Springfield State Technical University")
