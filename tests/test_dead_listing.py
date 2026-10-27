import re

import autopilot as A


class FakePage:
    def __init__(self, url, body=""):
        self.url = url
        self._body = body

    def inner_text(self, *a, **k):
        return self._body

    @property
    def frames(self):
        return []


def demo():
    dead = FakePage("https://job-boards.greenhouse.io/anthropic?error=true")
    assert A.is_dead_listing(dead), \
        "a posting that bounces to the board root with error=true is gone, not a form to retry 11 times"

    live = FakePage("https://job-boards.greenhouse.io/anthropic/jobs/5392968008", "Apply for this job")
    assert not A.is_dead_listing(live)

    on_job = FakePage("https://job-boards.greenhouse.io/acme/jobs/12?error=true", "First Name")
    assert not A.is_dead_listing(on_job), "an error param on the job page itself is not proof it is delisted"

    closed = FakePage("https://acme.com/jobs/1", "This job is no longer accepting applications")
    assert A.is_dead_listing(closed)
    print("dead listing self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
