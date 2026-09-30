import autopilot as A


def demo():
    assert not A._same_entity("Alabama State University", "Example State University"), \
        "an unrelated university must never be accepted — that is a fabricated credential"
    assert not A._same_entity("Aberystwyth University", "Example State University")
    assert not A._same_entity("State College of Florida", "Example State University"), \
        "sharing only generic words like State/College is not a match"

    assert A._same_entity("Example State University", "Example State University")
    assert A._same_entity("Example Medical Univ", "Example State University")
    print("no fabricated school self-check OK")


if __name__ == "__main__":
    demo()
