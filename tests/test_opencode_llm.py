import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import autopilot as A
import opencode_llm as O


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(O, "_CATALOG", {})
    monkeypatch.setattr(O, "command", lambda: "opencode.exe")


@pytest.fixture
def auto_state(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "STATE", tmp_path / "free.db", raising=False)
    clock = [1000.0]
    monkeypatch.setattr(O.time, "time", lambda: clock[0])
    monkeypatch.setattr(O.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(O, "models", lambda mode="free": ["opencode/a-free", "opencode/b-free"])
    monkeypatch.setattr(O, "_go_key", lambda: pytest.fail("Auto read paid credentials"))
    return clock


def test_auto_switches_after_rate_limit_and_shares_cooldown(monkeypatch, auto_state):
    calls = []
    def complete(model, *args):
        calls.append(model)
        if model.endswith("a-free"):
            raise O.OpenCodeError("HTTP 429", status=429, retry_after=90)
        return "answer"
    monkeypatch.setattr(O, "complete", complete)
    assert O.complete_auto("s", "u", 30, None) == "answer"
    assert O.complete_auto("s", "u", 30, None) == "answer"
    assert calls == ["opencode/a-free", "opencode/b-free", "opencode/b-free"]
    assert O.health()["opencode/a-free"]["retry_at"] == 1090
    auto_state[0] += 91
    assert O.complete_auto("s", "u", 30, None) == "answer"
    assert calls[-2:] == ["opencode/a-free", "opencode/b-free"]


def test_auto_timeout_reserves_time_for_next_model(monkeypatch, auto_state):
    calls = []
    def complete(model, system, user, timeout, run):
        calls.append((model, timeout))
        if model.endswith("a-free"):
            auto_state[0] += timeout
            raise RuntimeError("timed out")
        auto_state[0] += 1
        return "ok"
    monkeypatch.setattr(O, "complete", complete)
    assert O.complete_auto("s", "u", 25, None) == "ok"
    assert calls[0][1] <= 20
    assert calls[1][1] >= 5
    assert auto_state[0] <= 1025
    assert O.health()["opencode/a-free"]["status"] == "timeout"


def test_auto_never_uses_paid_or_unlisted_models(monkeypatch, auto_state):
    calls = []
    monkeypatch.setattr(O, "complete", lambda model, *args: calls.append(model) or "ok")
    assert O.complete_auto("s", "u", 30, None,
        ["opencode-go/paid", "opencode/paid", "opencode/missing-free", "opencode/b-free"]) == "ok"
    assert calls == ["opencode/b-free"]
    with pytest.raises(RuntimeError, match="No verified free"):
        O.complete_auto("s", "u", 30, None, ["opencode-go/paid"])


def test_auto_all_cooling_skips_calls_without_waiting(monkeypatch, auto_state):
    for model in O.models():
        O.record_result(model, 1, O.OpenCodeError("quota", status=429, retry_after=300))
    monkeypatch.setattr(O, "complete", lambda *a: pytest.fail("cooling model was called"))
    with pytest.raises(RuntimeError, match="cooldown"):
        O.complete_auto("s", "u", 30, None)
    assert auto_state[0] == 1000


def test_auto_never_overruns_total_deadline(monkeypatch, auto_state):
    calls = []
    def complete(model, system, user, timeout, run):
        calls.append(timeout)
        auto_state[0] += timeout
        raise RuntimeError("timed out")
    monkeypatch.setattr(O, "complete", complete)
    with pytest.raises(RuntimeError):
        O.complete_auto("s", "u", 8, None)
    assert sum(calls) <= 8 + 1e-6


def test_cli_error_preserves_limit_and_retry_after(monkeypatch):
    prepare(monkeypatch)
    output = json.dumps({"type": "error", "error": {"status": 429, "message": "private prompt",
        "response": {"headers": {"Retry-After": "120"}}}})
    with pytest.raises(O.OpenCodeError) as error:
        O.complete("opencode/big-pickle", "s", "u", 30,
                   lambda *a, **kw: SimpleNamespace(returncode=1, stdout=output))
    assert error.value.status == 429 and error.value.retry_after == 120
    assert "private prompt" not in str(error.value)


def test_live_catalog_filters_and_caches(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({"data": [{"id": x} for x in
                ["big-pickle", "new-free", "new-free", "paid", "jev-1.13-free", "bad/id", "test"]]}).encode()
    monkeypatch.setattr(O.urllib.request, "urlopen", lambda req, **kw: calls.append(req.full_url) or Response())
    assert O.models() == ["opencode/big-pickle", "opencode/new-free"]
    assert O.models() == O.models()
    assert len(calls) == 1
    assert O.models("go") == ["opencode-go/big-pickle", "opencode-go/new-free", "opencode-go/paid"]
    assert len(calls) == 2


def prepare(monkeypatch):
    monkeypatch.setattr(O, "models", lambda mode="free": ["opencode/big-pickle"] if mode == "free" else ["opencode-go/paid"])


def test_free_isolated_exact_model_and_no_paid_credentials(monkeypatch):
    prepare(monkeypatch)
    monkeypatch.setattr(O, "_go_key", lambda: pytest.fail("Free read Go credentials"))
    for name in ("OPENAI_API_KEY", "OPENCODE_CONFIG", "APPLYPILOT_OPENCODE_GO_KEY"):
        monkeypatch.setenv(name, "must-not-leak")
    def run(cmd, prompt, timeout, *, env, cwd):
        assert cmd[cmd.index("--model") + 1] == "opencode/big-pickle"
        assert "--standalone" in cmd and "--auto" not in cmd
        assert "system" in prompt and "question" in prompt
        assert 0 < timeout <= 30
        assert not any("must-not-leak" == v for v in env.values())
        assert Path(env["XDG_DATA_HOME"]).parent == Path(cwd)
        cfg = json.loads((Path(cwd) / "opencode.json").read_text())
        assert cfg["permissions"] == [{"action": "*", "resource": "*", "effect": "ask"}]
        assert cfg["policies"][-1]["resource"] == "opencode"
        assert "providers" not in cfg
        return SimpleNamespace(returncode=0, stdout='noise\n{"type":"text","part":{"id":"a","text":"o"}}\n{"type":"text","part":{"id":"a","text":"ok"}}', stderr="")
    assert O.complete("opencode/big-pickle", "system", "question", 30, run) == "ok"


def test_go_only_when_selected(monkeypatch):
    prepare(monkeypatch)
    monkeypatch.setattr(O, "_go_key", lambda: "test-secret")
    def run(cmd, prompt, timeout, *, env, cwd):
        assert cmd[cmd.index("--model") + 1] == "opencode-go/paid"
        assert env["APPLYPILOT_OPENCODE_GO_KEY"] == "test-secret"
        cfg = json.loads((Path(cwd) / "opencode.json").read_text())
        assert "test-secret" not in json.dumps(cfg)
        assert cfg["policies"][-1]["resource"] == "opencode-go"
        return SimpleNamespace(returncode=0, stdout='{"type":"text","part":{"text":"ok"}}')
    assert O.complete("opencode-go/paid", "s", "u", 30, run) == "ok"


@pytest.mark.parametrize("model", ["openai/gpt-any", "opencode/paid", "opencode/missing-free"])
def test_never_substitutes_unknown_or_paid_free_model(monkeypatch, model):
    prepare(monkeypatch)
    with pytest.raises(ValueError):
        O.complete(model, "s", "u", 30, lambda *a, **kw: pytest.fail("invalid model executed"))


@pytest.mark.parametrize("output,code", [
    ('{"type":"error","error":{"message":"secret"}}', 0),
    ('{"type":"tool","part":{"type":"tool"}}', 0),
    ('{"type":"permission.asked","part":{}}', 0),
    ('{"type":"text","part":{"text":"partial"}}', 1),
    ('{}', 0),
])
def test_errors_tools_and_partial_failure_are_not_answers(monkeypatch, output, code):
    prepare(monkeypatch)
    with pytest.raises(RuntimeError) as error:
        O.complete("opencode/big-pickle", "s", "u", 30,
                   lambda *a, **kw: SimpleNamespace(returncode=code, stdout=output))
    assert "secret" not in str(error.value)


def test_dispatch_uses_shared_chain(monkeypatch):
    monkeypatch.setattr(O, "complete", lambda model, s, u, t, run: (model, s, u, t, run))
    result = A._call_cli_llm({"cli": "opencode", "model": "opencode/big-pickle"}, "s", "u", 25)
    assert result == ("opencode/big-pickle", "s", "u", 25, A._run_cli)


def test_auto_dispatch_uses_verified_configured_pool(monkeypatch):
    pool = ["opencode/a-free", "opencode/b-free"]
    monkeypatch.setattr(O, "complete_auto", lambda s, u, t, run, candidates: (s, u, t, run, candidates))
    result = A._call_cli_llm({"cli": "opencode", "model": "auto", "free_models": pool}, "s", "u", 45)
    assert result == ("s", "u", 45, A._run_cli, pool)


@pytest.mark.parametrize("error,status,delay", [
    (O.OpenCodeError("limit", status=429), "rate_limit", 300),
    (O.OpenCodeError("quota exhausted", status=429), "quota", 3600),
    (O.OpenCodeError("request failed", status=402), "quota", 3600),
    (O.OpenCodeError("model unavailable"), "unavailable", 600),
    (RuntimeError("empty answer"), "error", 30),
])
def test_failure_cooldowns_and_success_recovery(auto_state, error, status, delay):
    mid = "opencode/a-free"
    O.record_result(mid, 2, error)
    assert O.health()[mid]["status"] == status
    assert O.health()[mid]["retry_at"] == 1000 + delay
    O.record_result(mid, 1)
    assert O.health()[mid]["status"] == "ok" and O.health()[mid]["retry_at"] == 0


@pytest.mark.parametrize("retry,expected", [("Thu, 01 Jan 1970 00:18:20 GMT", 100), ("inf", 0), ("invalid", 0)])
def test_retry_after_http_date_and_invalid_values(monkeypatch, auto_state, retry, expected):
    prepare(monkeypatch)
    output = json.dumps({"type": "error", "error": {"status": 429,
        "response": {"headers": {"retry-after": retry}}}})
    with pytest.raises(O.OpenCodeError) as error:
        O.complete("opencode/big-pickle", "s", "u", 30,
                   lambda *a, **kw: SimpleNamespace(returncode=1, stdout=output))
    assert error.value.retry_after == expected


def test_access_denial_visible_without_provider_payload(monkeypatch):
    prepare(monkeypatch)
    output = json.dumps({"type": "error", "error": {"status": 403, "message": "private request"}})
    with pytest.raises(RuntimeError, match="HTTP 403") as error:
        O.complete("opencode/big-pickle", "s", "u", 30,
                   lambda *a, **kw: SimpleNamespace(returncode=1, stdout=output))
    assert "private request" not in str(error.value)


def args(**kwargs):
    return SimpleNamespace(**{**dict(set_key=None, enable=None, disable=None, models=None,
                                    set_model=None, only=None, probe=False), **kwargs})


def test_enable_preserves_existing_config_and_does_not_persist_injected_keys(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    original = {"personas": {"appsec": {"keep": True}}, "llm": {"providers": [
        {"name": "api", "kind": "openai"}, {"name": "codex", "kind": "cli", "enabled": False}]}}
    config.write_text(json.dumps(original))
    monkeypatch.setattr(A, "CFG", config)
    monkeypatch.setattr(A, "read_json", lambda p: {"secret": "injected-key"})
    A.llm_cmd(args(enable="opencode"))
    A.llm_cmd(args(enable="opencode"))
    saved = json.loads(config.read_text())
    providers = saved["llm"]["providers"]
    assert [p["name"] for p in providers] == ["api", "opencode", "codex"]
    assert providers[0] == original["llm"]["providers"][0]
    assert providers[2] == original["llm"]["providers"][1]
    assert providers[1]["model"] == "auto"
    assert saved["personas"] == original["personas"]
    assert "injected-key" not in config.read_text()


def test_cli_model_list_survives_go_catalog_failure(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"llm": {"providers": [{"name": "opencode", "kind": "cli", "cli": "opencode"}]}}))
    monkeypatch.setattr(A, "CFG", config)
    monkeypatch.setattr(A, "read_json", lambda p: {})
    def catalog(mode):
        if mode == "go":
            raise TimeoutError()
        return ["opencode/big-pickle"]
    monkeypatch.setattr(O, "models", catalog)
    A.llm_cmd(args(models="opencode"))
    result = json.loads(capsys.readouterr().out)
    assert result == {"ok": True, "models": ["auto", "opencode/big-pickle"], "unavailable": ["go"]}


def test_go_credentials_read_only_from_selected_integration(tmp_path, monkeypatch):
    import sqlite3
    data = tmp_path / "opencode"
    data.mkdir()
    database = data / "opencode.db"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE credential (integration_id TEXT, active INTEGER, time_updated INTEGER, value TEXT)")
        con.executemany("INSERT INTO credential VALUES (?, 1, ?, ?)", [
            ("openai", 9, '{"key":"unrelated"}'),
            ("opencode-go", 1, '{"key":"old"}'),
            ("opencode-go", 2, '{"key":"current"}')])
    before = database.read_bytes()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert O._go_key() == "current"
    assert database.read_bytes() == before


def test_go_legacy_auth_and_missing_login(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="/connect"):
        O._go_key()
    data = tmp_path / "opencode"
    data.mkdir()
    (data / "auth.json").write_text('{"opencode-go":{"key":"legacy"},"openai":{"key":"unrelated"}}')
    assert O._go_key() == "legacy"
