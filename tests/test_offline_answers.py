import autopilot as A

CFG = A.read_json(A.CFG)
CFG["profile"]["location"] = "Springfield, Exampleland"
CFG["apply"]["require_sponsorship"] = True
CFG["apply"]["answers"].update({"open_to_relocation": "Yes", "arbitration_agreement": "Yes"})


def test_offline_answers():
    def ans(label, options=()):
        return A.offline_answer(label, list(options), CFG)

    # the rule only picks among given options; a free-text source is the form filler's call, not a canned guess
    assert ans("How did you hear about us?") is None
    assert ans("How did you hear about us?", ["Company careers page", "Other"]) == "Company careers page"
    assert ans("How did you hear about us?", ["Friend", "LinkedIn", "Other"]) == "LinkedIn"
    assert ans("Do you require visa sponsorship?", ["Yes", "No"]) == "Yes"
    assert ans("Are you open to relocation for this role?", ["Yes", "No"]) == "Yes"
    assert ans("Have you worked for Loenbro or any of its affiliates in the past?", ["Yes", "No"]) is None
    assert ans("Have you ever interviewed at Anthropic before?", ["Yes", "No"]) is None
    assert ans("What is your preferred language?", ["English", "Spanish"]) is None
    # the rule hardcodes the range without the profile's " / year" suffix
    assert CFG["profile"]["salary_expectation"].startswith(ans("What are your compensation expectations for this role?"))
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
    test_offline_answers()
