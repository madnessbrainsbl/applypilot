import autopilot as A

cfg = A.read_json(A.CFG)
assert cfg["apply"]["fill_timeout_seconds"] >= 10
answers = {
    "currently based": "Exampleland",
    "authorized to work in this country": "No",
    "professional references and criminal record checks": "Yes",
    "relatives": "No",
    "willing to relocate permanently": "Yes",
    "public official": "No",
    "current employer": "Other Company",
    "live in one of the following hubs": "I do not live and not willing to relocate to this job's location.",
}

assert A._match_dropdown_answer("Where are you currently based?", answers) == "Exampleland"
assert A._match_dropdown_answer("Are you authorized to work in this country?", answers) == "No"
assert A._match_dropdown_answer("Do you have a criminal record?", answers) is None
assert A._match_dropdown_answer("Are you available for on-call work?", answers) is None
assert A._match_dropdown_answer("Do you have a minimum of 3 years of experience?", answers) is None
assert A._match_dropdown_answer("Are you able to provide professional references and criminal record checks?", answers) == "Yes"
assert A._match_dropdown_answer("Do you have any relatives currently working for N26?", answers) == "No"
assert A._match_dropdown_answer("Are you willing to relocate permanently/long-term to London, UK for this role?", answers) == "Yes"
assert A._match_dropdown_answer("Have you been a Public Official?", answers) == "No"
assert A._match_dropdown_answer("What's your current employer?", answers) == "Other Company"
assert A._match_dropdown_answer("What's your current employer?", cfg["apply"]["answers"]) == ""
assert A._match_dropdown_answer(
    "This role requires that you live in one of the following hubs: Austin, TX or NYC. Are you currently located in one of the hub locations or willing to relocate?",
    answers,
) == "I do not live and not willing to relocate to this job's location."
assert A._qa_key("Where are you authorized to work?", ["Yes", "No"]) != A._qa_key(
    "Where are you authorized to work?", ["Exampleland", "United Kingdom"]
)

print("answer matching self-check OK")
