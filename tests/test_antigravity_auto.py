from types import SimpleNamespace

import pytest
import autopilot as A
from opencode_llm import health


def provider():
    return {"name": "antigravity", "kind": "openai", "model": "auto",
            "timeout_seconds": 15, "auto_models": ["gemini-fast", "gemini-backup"]}


@pytest.mark.parametrize("failure", [RuntimeError("429 limit"), TimeoutError("timed out"), None])
def test_model_failure_uses_next_model_without_cooling_service(monkeypatch, failure):
    calls = []
    def call(p, *args):
        calls.append(p["model"])
        if p["model"] == "gemini-fast":
            if failure:
                raise failure
            return ""
        return "answer"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [provider()]}}
    assert A.llm_complete(cfg, "s", "u") == "answer"
    A._LLM_BENCH.clear()
    assert A.llm_complete(cfg, "s", "u") == "answer"
    assert calls == ["gemini-fast", "gemini-backup", "gemini-backup"]
    assert health(state_path=A.LLM_STATE)["antigravity"]["status"] == "ok"


def test_retry_after_is_per_model(monkeypatch):
    class Limited(RuntimeError):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "120"})
    now = [100.0]
    monkeypatch.setattr(A.time, "time", lambda: now[0])
    calls = []
    def call(p, *args):
        calls.append(p["model"])
        if p["model"] == "gemini-fast":
            raise Limited("private provider error")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [provider()]}}
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert health(state_path=A.LLM_STATE)["antigravity/gemini-fast"]["retry_at"] == 220
    now[0] = 221
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert calls == ["gemini-fast", "gemini-backup"] * 2


def test_stalled_model_leaves_time_for_next_model(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    calls = []
    def call(p, s, u, timeout, max_tokens):
        calls.append(timeout)
        if len(calls) == 1:
            clock[0] += timeout
            raise TimeoutError("timed out")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_complete({"llm": {"providers": [provider()]}}, "s", "u") == "ok"
    assert calls == [9, 6]


def test_all_cooling_models_use_other_service(monkeypatch):
    p = provider()
    for mid in p["auto_models"]:
        A._llm_record({"name": "antigravity/" + mid}, 0, RuntimeError("429 limit"))
    calls = []
    def call(p, *args):
        calls.append(p["name"])
        return "fallback"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [p, {"name": "other", "model": "fixed"}]}}
    assert A.llm_complete(cfg, "s", "u") == "fallback"
    assert calls == ["other"]


def test_catalog_excludes_wildcards_images_and_internal_models(monkeypatch):
    monkeypatch.setattr(A, "_listed_models", lambda *a, **kw: [
        "claude-3-*", "gemini-3-pro-image", "internal-background-task",
        "tab_flash_lite_preview", "gemini-3-flash-agent", "gemini-3.1-flash-lite", 123, None,
        "gemini-3.8-flash-low", "claude-sonnet-4-6"])
    p = provider()
    p.pop("auto_models")
    assert A._antigravity_models(p) == ["gemini-3.8-flash-low", "gemini-3.1-flash-lite", "claude-sonnet-4-6"]


def test_manual_model_remains_exact(monkeypatch):
    p = provider()
    p["model"] = "gemini-pinned"
    calls = []
    def call(p, *args):
        calls.append(p["model"])
        raise RuntimeError("429 limit")
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_complete({"llm": {"providers": [p]}}, "s", "u") == ""
    assert calls == ["gemini-pinned"]


def test_retired_model_banner_is_an_error(monkeypatch):
    import sys
    message = SimpleNamespace(content="Gemini 3.5 Flash is no longer available. Please switch to Gemini 3.7 Flash.")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **kw: client))
    with pytest.raises(RuntimeError, match="model not found"):
        A._call_openai_llm(provider(), "s", "u", 5, 100)


def test_vision_auto_uses_same_model_fallback(monkeypatch):
    calls = []
    def call(p, s, u, timeout, max_tokens, images):
        assert images == [b"fixture"]
        calls.append(p["model"])
        if len(calls) == 1:
            raise RuntimeError("429 limit")
        return "visible answer"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_vision({"llm": {"providers": [provider()]}}, "s", "u", [b"fixture"]) == "visible answer"
    assert calls == ["gemini-fast", "gemini-backup"]


def test_provider_probe_uses_auto_model_fallback(monkeypatch, capsys):
    p = provider()
    monkeypatch.setattr(A, "_cfg_raw", lambda: {"llm": {"providers": [p]}})
    monkeypatch.setattr(A, "read_json", lambda _: {})
    calls = []
    def call(p, *args):
        calls.append(p["model"])
        if len(calls) == 1:
            raise RuntimeError("429 limit")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    args = SimpleNamespace(set_key=None, enable=None, disable=None, models=None,
                           set_model=None, only="antigravity", probe=True)
    A.llm_cmd(args)
    import json
    assert json.loads(capsys.readouterr().out)[0]["ok"] is True
    assert calls == ["gemini-fast", "gemini-backup"]


def test_invalid_key_stops_model_iteration(monkeypatch):
    class Denied(RuntimeError):
        status_code = 401
    calls = []
    def call(p, *args):
        calls.append(p["model"])
        raise Denied("bad credentials")
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_complete({"llm": {"providers": [provider()]}}, "s", "u") == ""
    assert calls == ["gemini-fast"]


def test_all_models_failing_reaches_next_provider(monkeypatch):
    calls = []
    def call(p, *args):
        calls.append((p["name"], p["model"]))
        if p["name"] == "antigravity":
            raise RuntimeError("503 unavailable")
        return "other answer"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [provider(), {"name": "other", "model": "fixed"}]}}
    assert A.llm_complete(cfg, "s", "u") == "other answer"
    assert calls == [("antigravity", "gemini-fast"), ("antigravity", "gemini-backup"), ("other", "fixed")]


def test_model_list_keeps_auto_selectable(monkeypatch, capsys):
    import io
    import json
    p = {**provider(), "url": "https://example.invalid/v1"}
    monkeypatch.setattr(A, "_cfg_raw", lambda: {"llm": {"providers": [p]}})
    monkeypatch.setattr(A, "read_json", lambda _: {})
    monkeypatch.setattr(A.urllib.request, "urlopen", lambda *a, **kw:
                        io.BytesIO(b'{"data":[{"id":"gemini-fast"}]}'))
    args = SimpleNamespace(set_key=None, enable=None, disable=None, models="antigravity", set_model=None)
    A.llm_cmd(args)
    assert json.loads(capsys.readouterr().out)["models"] == ["auto", "gemini-fast"]
