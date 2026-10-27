import autopilot as A
import db

CV = """Application Security Engineer
Acme Labs | Application Security Engineer | Oct 2025 - Present
Maple Systems | Penetration Tester | 2020 - 2025
Reduced the critical AppSec backlog by ~70% via CI review gates.
Education: Doctor of Veterinary Medicine, Springfield State Technical University, 2011-2016.
{"degree": "Doctor of Veterinary Medicine (DVM)", "bio": "5+ years of experience in secure code review"}"""


def demo():
    spec = A._is_company_specific
    assert spec("Why do you want to work at Palantir?")
    assert spec("Why are you interested in this role?")
    assert spec("What excites you about joining us?")
    assert spec("Please attach or paste a cover letter")
    assert spec("What do you know about Stripe?", "Stripe")
    assert not spec("Do you require visa sponsorship?")
    assert not spec("What is your notice period?")
    assert not spec("Describe the project you are proudest of"), "personal, not per-company: keep it cached"

    why = "Why do you want to work here?"
    assert A._qa_key(why, (), "Palantir") != A._qa_key(why, (), "Stripe")
    visa = "Do you require visa sponsorship?"
    assert A._qa_key(visa, ["Yes", "No"], "Palantir") == A._qa_key(visa, ["Yes", "No"], "Stripe") == \
        A._qa_key(visa, ["Yes", "No"]), "facts must stay in the one global cache"
    assert A._qa_key("Headline", (), "", "creative") != A._qa_key("Headline"),         "the AppSec headline was reused on the photographer's form"
    assert A._qa_key("Headline", (), "", "appsec") == A._qa_key("Headline"), "appsec keeps its existing keys"

    con = db.connect()
    ka, kb = A._qa_key(why, (), "__selftest__ A"), A._qa_key(why, (), "__selftest__ B")
    db.save_qa_answer(con, ka, why, "Because A builds X.", "__selftest__ A")
    assert db.get_qa_answer(con, kb) is None, "company B must not reuse the paragraph written for A"
    con.execute("DELETE FROM qa_answers WHERE question_key=?", (ka,))
    con.commit()

    bad = lambda label, ans, opts=None: A._unsupported_claim({"label": label, "options": opts}, ans, CV, "Palantir")
    assert A._max_supported_years(CV) == A.datetime.now().year - 2020, "med school 2011 is not work experience"
    assert "OSCP" in bad("Tell us about your certifications", "I hold the OSCP and have 5 years of AppSec experience.")
    assert "years" in bad("Describe your background", "I have 12 years of experience in application security.")
    assert "PhD" in bad("Education?", "I hold a PhD in computer science.")
    assert bad("Do you have 10+ years of experience in AppSec?", "Yes", ["Yes", "No"])
    assert bad("How many years of experience do you have?", "10+", ["0-2", "3-5", "10+"])
    assert bad("Do you hold a CISSP?", "Yes", ["Yes", "No"])
    assert bad("Walk us through your career", "I previously worked at Google on cloud security.")
    assert bad("Impact?", "I cut false positives by 90% across the org.")

    # grounded answers pass
    assert bad("Describe your background",
               "I have 5+ years of hands-on AppSec experience at Maple Systems and Acme Labs, "
               "and reduced the critical backlog by 70%.") is None
    assert bad("Do you have 3+ years of experience in AppSec?", "Yes", ["Yes", "No"]) is None
    assert bad("Do you hold a CISSP or OSCP?", "No", ["Yes", "No"]) is None
    assert bad("Education?", "I don't have a PhD; I hold a DVM from Springfield State Technical University.") is None
    assert bad("Certifications?", "I am preparing for the OSCP.") is None
    assert bad("Why Palantir?", "I want to work at Palantir because of its mission.") is None
    # false positives seen on the real qa_answers table
    assert bad("Are you 18 years of age or older?", "Yes", ["Yes", "No"]) is None
    assert bad("Highest level of education?", "Professional/Doctoral Degree", ["Bachelor's", "Professional/Doctoral Degree"]) is None
    assert bad("Confirm", "I am applying for the Security Engineer role at Palantir.") is None
    assert A._unsupported_claim({"label": "x"}, "I hold a CISSP.", "") is None, "no CV text: nothing to judge"
    print("llm answer guard self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
