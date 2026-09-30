import json
import autopilot as A


def demo():
    A._QUIT_RUN["v"] = False
    A._QUIT_POLL["t"] = 0.0
    A._FILL_DEADLINE = 0.0
    A.CONTROL.write_text("{}", encoding="utf-8")
    assert not A._fill_expired()

    A.CONTROL.write_text(json.dumps({"action": "pause"}), encoding="utf-8")
    A._QUIT_POLL["t"] = 0.0
    assert not A._fill_expired(), "a non-quit action must not abort the fill"
    assert json.loads(A.CONTROL.read_text(encoding="utf-8")).get("action") == "pause", \
        "the fill loop must not swallow commands meant for the review loop"

    A.CONTROL.write_text(json.dumps({"action": "quit"}), encoding="utf-8")
    A._QUIT_POLL["t"] = 0.0
    assert A._fill_expired(), "quit must abort the fill immediately, not only between jobs"
    assert A._QUIT_RUN["v"], "quit must persist so apply() stops after this job"
    assert json.loads(A.CONTROL.read_text(encoding="utf-8")) == {}

    A._QUIT_RUN["v"] = False
    A.CONTROL.write_text(json.dumps({"action": "quit"}), encoding="utf-8")
    assert A.read_control() == "quit"
    assert A._QUIT_RUN["v"], "a drain of the control file must not swallow a pending stop"

    A._QUIT_RUN["v"] = False
    A.CONTROL.write_text("{}", encoding="utf-8")
    print("quit control self-check OK")


if __name__ == "__main__":
    demo()
