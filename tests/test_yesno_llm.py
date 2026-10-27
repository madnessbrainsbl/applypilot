import autopilot as A

CFG = {"profile": {}, "apply": {"answers": {"open_to_onsite": "Yes"}}}


def test_open_to_travel_is_yes():
    assert A.offline_answer("Are you open to travel?", ["Yes", "No"], CFG) == "Yes"


def test_ashby_application_s_in_is_success():
    assert A._is_success("Success Your application’s in! We’ll contact you if there are next steps.")
    assert not A._is_success("Fill in your application so far")


def test_creative_persona_has_no_bare_cutter_term():
    terms = [t.lower() for t in A._cfg_raw()["personas"]["creative"]["search"]["required_title_terms"]]
    assert "cutter" not in terms
