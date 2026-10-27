import autopilot as A


def demo():
    assert not A._same_entity("Alabama State University", "Springfield State Technical University"), \
        "an unrelated university must never be accepted — that is a fabricated credential"
    assert not A._same_entity("Aberystwyth University", "Springfield State Technical University")
    assert not A._same_entity("State College of Florida", "Springfield State Technical University"), \
        "sharing only generic words like State/College is not a match"

    assert A._same_entity("Springfield State Technical University", "Springfield State Technical University")
    assert A._same_entity("Springfield Technical Univ", "Springfield State Technical University")
    print("no fabricated school self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
