import sys, types
import autopilot as A
import tempfile
from pathlib import Path

ORIGINAL = "<p>" + "Original draft body. " * 8 + "</p>"

def fake_openai(reply=None, raise_exc=None):
    mod = types.ModuleType("openai")

    class FakeMessage:
        def __init__(self, content): self.content = content

    class FakeChoice:
        def __init__(self, content): self.message = FakeMessage(content)

    class FakeResp:
        def __init__(self, content): self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            if raise_exc: raise raise_exc
            return FakeResp(reply)

    class FakeChat:
        def __init__(self): self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs): self.chat = FakeChat()

    mod.OpenAI = FakeClient
    return mod

def run(reply=None, raise_exc=None):
    sys.modules["openai"] = fake_openai(reply, raise_exc)
    A._LLM_BENCH.clear()   # each case is a fresh provider; a failed one is benched for 10 min
    job = {"title": "Security Engineer", "company": "Acme"}
    cfg = {"llm": {"base_url": "http://127.0.0.1:8045/v1", "model": "m", "timeout_seconds": 5,
                   "providers": [{"name": "t", "kind": "openai", "url": "http://127.0.0.1:8045/v1",
                                  "model": "m", "enabled": True}]}}
    previous = A.LLM_STATE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            A.LLM_STATE = Path(tmp) / "providers.db"
            return A.review_cover_letter(ORIGINAL, job, cfg, "CV text", "posting text")
    finally:
        A.LLM_STATE = previous

improved = "<p>" + "Improved and grounded body. " * 8 + "</p>"
assert run(reply=improved) == improved, "a valid revision must replace the draft"

assert run(reply="<p>too short</p>") == ORIGINAL, "a too-short revision must keep the original"

assert run(reply="Improved but plain text with no markup at all, " * 6) == ORIGINAL, \
    "a revision without HTML structure must keep the original"

assert run(reply="") == ORIGINAL, "an empty revision must keep the original"

assert run(raise_exc=RuntimeError("proxy down")) == ORIGINAL, \
    "an LLM failure must keep the original, never lose the letter"

fenced = "```html\n" + improved + "\n```"
assert run(reply=fenced) == improved, "code fences must be stripped"

print("cover letter review self-check OK")
