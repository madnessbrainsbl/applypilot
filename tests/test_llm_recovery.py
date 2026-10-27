import pytest
import autopilot as A


@pytest.fixture
def chain_state(monkeypatch):
    monkeypatch.setattr(A, "_LLM_BENCH", {})
    clock = [100.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(A.time, "time", lambda: clock[0])
    monkeypatch.setattr(A.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock


def config(*names, budget=60):
    return {"llm": {"max_total_timeout_seconds": budget, "providers": [
        {"name": name, "kind": "openai", "url": "https://example.invalid/v1",
         "model": "test", "timeout_seconds": 30} for name in names]}}


def test_primary_timeout_leaves_fallback_budget(monkeypatch, chain_state):
    calls = []
    def call(provider, system, user, timeout, max_tokens):
        calls.append((provider["name"], timeout))
        if provider["name"] == "primary":
            chain_state[0] += timeout
            raise TimeoutError("Request timed out")
        assert timeout >= 20
        return "fallback answer"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_complete(config("primary", "fallback"), "s", "u") == "fallback answer"
    assert calls == [("primary", 30), ("fallback", 30)]


def test_timeout_recovers_after_short_cooldown(monkeypatch, chain_state):
    calls = []
    def call(provider, *args):
        calls.append(provider["name"])
        if calls == ["primary"]:
            raise TimeoutError("Request timed out")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = config("primary", "fallback")
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert A.llm_complete(cfg, "s", "u") == "ok"
    chain_state[0] += 16
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert calls == ["primary", "fallback", "fallback", "primary"]


def test_policy_denial_not_retried(monkeypatch, chain_state):
    calls = []
    def call(provider, *args):
        calls.append(provider["name"])
        if provider["name"] == "denied":
            raise RuntimeError("429 access temporarily unavailable due to policy review")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = config("denied", "good")
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert calls == ["denied", "good", "good"]
    assert A._LLM_BENCH["denied"] == 700


def test_cooldown_failure_remains_visible(monkeypatch, chain_state, capsys):
    A._LLM_BENCH["down"] = 200
    monkeypatch.setattr(A, "_call_openai_llm", lambda *a: pytest.fail("must skip cooldown"))
    assert A.llm_complete(config("down"), "s", "u") == ""
    assert "llm: no provider answered" in capsys.readouterr().err


def test_budget_is_reserved_for_fallback(monkeypatch, chain_state):
    calls = []
    def call(provider, system, user, timeout, max_tokens):
        calls.append(timeout)
        if len(calls) == 1:
            chain_state[0] += timeout
            raise TimeoutError("timed out")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_complete(config("a", "b", budget=12), "s", "u") == "ok"
    assert calls == [6, 6]


def test_rate_limit_never_retries_same_provider_in_one_request(monkeypatch, chain_state):
    calls = []
    def call(provider, *args):
        calls.append(provider["name"])
        if len(calls) == 1:
            raise RuntimeError("429 rate limit exceeded. Retry after 0 seconds")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_complete(config("limited"), "s", "u") == ""
    assert calls == ["limited"]


def test_rate_limit_persists_when_memory_cache_is_cleared(monkeypatch, chain_state):
    from types import SimpleNamespace
    class Limited(RuntimeError):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "120"})
    calls = []
    def call(provider, *args):
        calls.append(provider["name"])
        if provider["name"] == "limited": raise Limited("private request")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = config("limited", "working")
    assert A.llm_complete(cfg, "s", "u") == "ok"
    A._LLM_BENCH.clear()
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert calls == ["limited", "working", "working"]
    chain_state[0] += 121
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert calls[-2:] == ["limited", "working"]


def test_each_remaining_provider_gets_a_time_share(monkeypatch, chain_state):
    calls = []
    def call(provider, s, u, timeout, max_tokens):
        calls.append((provider["name"], timeout))
        if provider["name"] != "c":
            chain_state[0] += timeout
            raise TimeoutError("timed out")
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    assert A.llm_complete(config("a", "b", "c"), "s", "u") == "ok"
    assert calls == [("a", 20), ("b", 20), ("c", 20)]


def test_empty_answer_has_a_cooldown_and_uses_next_provider(monkeypatch, chain_state):
    calls = []
    def call(provider, *args):
        calls.append(provider["name"])
        return "" if provider["name"] == "empty" else "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = config("empty", "working")
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert calls == ["empty", "working", "working"]


def test_missing_vision_models_still_reports_provider_failure(monkeypatch, capsys):
    monkeypatch.setattr(A, '_vision_model_ids', lambda *a: [])
    assert A.llm_vision(config('vision'), 's', 'u', [b'fixture']) == ''
    assert 'llm vision: no provider answered' in capsys.readouterr().err


def test_successful_vision_discovery_is_cached(monkeypatch):
    import io
    import json
    monkeypatch.setattr(A, '_MODEL_CACHE', {})
    calls = []
    def fetch(*a, **kw):
        calls.append(True)
        return io.BytesIO(json.dumps({'data': [{'id': 'fixture-vision', 'input_modalities': ['text', 'image']}]}).encode())
    monkeypatch.setattr(A.urllib.request, 'urlopen', fetch)
    p = {'name': 'test', 'url': 'https://example.invalid/v1'}
    assert A._vision_model_ids(p) == ['fixture-vision']
    assert A._vision_model_ids(p) == ['fixture-vision']
    assert len(calls) == 1
