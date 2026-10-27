import autopilot as A

CFG = {"profile": {"location": "Springfield, Exampleland", "country": "Exampleland", "languages": "English",
                   "salary_expectation": "60,000 - 80,000 EUR", "notice_period": "1 month",
                   "linkedin": "https://www.linkedin.com/in/jane-doe-example/", "years_experience": "5"},
       "apply": {"require_sponsorship": True, "answers": {"open_to_relocation": "Yes", "open_to_onsite": "Yes", "arbitration_agreement": "Yes"}}}


def demo():
    def ans(label, options=()):
        return A.offline_answer(label, list(options), CFG)

    assert ans("How did you hear about us?") is None
    assert ans("Do you require visa sponsorship?", ["Yes", "No"]) == "Yes"
    assert ans("Are you legally authorized to work in the UK?", ["Yes", "No"]) == "No"
    assert A.offline_answer("Do you require visa sponsorship?", ["Yes", "No"], {**CFG, "apply": {}}) is None,         "an unset apply.require_sponsorship must not be guessed"
    assert A.offline_answer("Do you require visa sponsorship?", ["Yes", "No"], {**CFG, "apply": {"require_sponsorship": False}}) == "No"
    assert ans("Are you open to relocation for this role?", ["Yes", "No"]) == "Yes"
    assert ans("Have you worked for Loenbro or any of its affiliates in the past?", ["Yes", "No"]) is None
    assert ans("Have you ever interviewed at Anthropic before?", ["Yes", "No"]) is None
    assert ans("What is your preferred language?", ["English", "Spanish"]) is None
    assert ans("What are your compensation expectations for this role?") == CFG["profile"]["salary_expectation"]
    assert ans("What is your LinkedIn profile?") == CFG["profile"]["linkedin"]
    assert ans("Agreement to Arbitrate", ["I understand and agree to the terms."]) == \
        "I understand and agree to the terms."
    assert ans("Please read the arbitration agreement below",
               ["I will read the arbitration agreement below."]) == "I will read the arbitration agreement below."

    assert ans("Are you currently based in Lisbon?", ["Yes", "No"]) == "No", \
        "the candidate lives in Springfield — answering Yes here would be a lie"
    assert ans("Do you currently live in Springfield?", ["Yes", "No"]) == "Yes"

    assert ans("Describe a security incident you handled") is None, \
        "an essay question must stay unanswered rather than get a canned reply"
    assert ans("") is None
    print("offline answers self-check OK")


if __name__ == "__main__":
    demo()
