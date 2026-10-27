import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from autopilot import _best_option_index, _phrase_in


def pick(answer, options):
    i = _best_option_index(answer, list(enumerate(o.lower() for o in options)))
    return options[i] if i >= 0 else None


def demo():
    visa = "Yes, but not one of the visas listed here"
    assert pick(visa, ["No", "Yes"]) == "Yes", pick(visa, ["No", "Yes"])
    assert pick(visa, ["Yes", "No"]) == "Yes"
    assert pick("No", ["None of the above", "No", "Yes"]) == "No"
    assert pick("No", ["Norway", "No"]) == "No"
    assert pick("Man", ["Woman", "Man"]) == "Man"
    assert pick("Man", ["Female", "Male"]) == "Male"
    assert pick("Woman", ["Woman", "Man"]) == "Woman"
    assert pick("Decline To Self Identify",
                ["Hispanic or Latino", "I don't wish to answer"]) == "I don't wish to answer"
    assert pick("Yes", ["Yes, I am authorized", "No"]) == "Yes, I am authorized"
    assert pick("Yes", ["Not at this time", "Yes"]) == "Yes"
    assert pick("Bachelor's Degree", ["Master's Degree", "Bachelor's Degree"]) == "Bachelor's Degree"
    assert pick("Purple", ["Red", "Green"]) is None
    assert pick("", ["Red"]) is None

    assert _phrase_in("no", "no") is True
    assert _phrase_in("no", "not one of the visas") is False
    assert _phrase_in("no", "none of the above") is False
    assert _phrase_in("man", "woman") is False
    assert _phrase_in("a", "a b") is False
    print("option match self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
