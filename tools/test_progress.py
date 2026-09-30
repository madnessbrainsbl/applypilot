import autopilot as A


def demo():
    A.write_progress(0, 40, "starting")
    p = A.read_progress()
    assert p["percent"] == 1, "a just-started run must not show 0%"

    A.write_progress(10, 40, "tavily")
    p = A.read_progress()
    assert (p["percent"], p["label"]) == (25, "tavily"), p

    A.write_progress(39, 40)
    assert A.read_progress()["percent"] == 98

    A.write_progress(40, 40)
    assert A.read_progress() is None, "a finished run must clear the bar, not park it at 100%"

    A.write_progress(3, 0)
    assert A.read_progress() is None, "no steps means no bar"

    A.PROGRESS.unlink(missing_ok=True)
    assert A.read_progress() is None
    print("progress self-check OK")


if __name__ == "__main__":
    demo()
