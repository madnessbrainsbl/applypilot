from types import SimpleNamespace

import pytest
import autopilot as A
from opencode_llm import OpenCodeError, health


def provider(name="groq", kind="openai"):
    return {"name": name, "kind": kind, "cli": name, "model": "auto",
            "auto_models": ["fast", "backup"], "timeout_seconds": 15}


@pytest.mark.parametrize("name,kind", [("groq", "openai"), ("tokenharbor", "openai"),
    ("codex", "cli"), ("claude", "cli"), ("grok", "cli"), ("lmstudio", "openai"), ("openrouter", "openai")])
@pytest.mark.parametrize("error", [OpenCodeError("limited", status=429, retry_after=90),
    TimeoutError("timed out"), RuntimeError("503 unavailable"), None])
def test_each_provider_rotates_models_and_persists_cooldown(monkeypatch, name, kind, error):
    calls = []
    def call(p, *args, **kwargs):
        calls.append(p["model"])
        if p["model"] == "fast":
            if error:
                raise error
            return ""
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    monkeypatch.setattr(A, "_call_cli_llm", call)
    cfg = {"llm": {"providers": [provider(name, kind)]}}
    assert A.llm_complete(cfg, "s", "u") == "ok"
    A._LLM_BENCH.clear()
    assert A.llm_complete(cfg, "s", "u") == "ok"
    assert calls == ["fast", "backup", "backup"]
    assert health(state_path=A.LLM_STATE)[name]["status"] == "ok"


def test_no_verified_models_does_not_invent_a_default(monkeypatch):
    p = provider()
    p["auto_models"] = []
    monkeypatch.setattr(A, "_call_openai_llm", lambda *a: pytest.fail("no candidate was verified"))
    assert A.llm_complete({"llm": {"providers": [p]}}, "s", "u") == ""


def test_full_configured_chain_for_every_failure_and_target(monkeypatch, tmp_path):
    providers = [provider("antigravity"), provider("tokenharbor"), provider("groq"),
        provider("opencode", "cli"), provider("codex", "cli"), provider("tokenharbor-mimo"),
        provider("claude", "cli"), provider("lmstudio"), provider("grok", "cli"), provider("openrouter")]
    providers[0]["auto_models"] = ["gemini-fast", "gemini-backup"]
    # Exercise the real OpenCode model loop using the same isolated health store.
    import opencode_llm as O
    monkeypatch.setattr(O, "models", lambda mode="free": ["opencode/a-free", "opencode/b-free"])
    providers[3]["free_models"] = ["opencode/a-free", "opencode/b-free"]
    real_cli = A._call_cli_llm
    for error_index, error in enumerate([OpenCodeError("limit", status=429, retry_after=120),
            TimeoutError("timed out"), RuntimeError("503 unavailable"), None]):
        for winner in range(len(providers)):
            monkeypatch.setattr(A, "LLM_STATE", tmp_path / f"chain-{error_index}-{winner}.db")
            monkeypatch.setattr(O, "STATE", tmp_path / f"opencode-{error_index}-{winner}.db")
            A._LLM_BENCH.clear()
            calls = []
            def call(p, *args, **kwargs):
                calls.append((p["name"], p["model"]))
                if p["name"] == providers[winner]["name"]:
                    return "winner"
                if error:
                    raise error
                return ""
            def cli(p, *args):
                return real_cli(p, *args) if p["name"] == "opencode" else call(p, *args)
            monkeypatch.setattr(A, "_call_openai_llm", call)
            monkeypatch.setattr(A, "_call_cli_llm", cli)
            monkeypatch.setattr(O, "complete", lambda mid, *a: call({"name": "opencode", "model": mid}))
            cfg = {"llm": {"providers": providers, "max_total_timeout_seconds": 90}}
            assert A.llm_complete(cfg, "s", "u") == "winner"
            assert list(dict.fromkeys(name for name, _ in calls)) == [p["name"] for p in providers[:winner+1]]
            assert len(calls) == len(set(calls)), "never repeat the same model in one request"
            A._LLM_BENCH.clear()
            calls.clear()
            assert A.llm_complete(cfg, "s", "u") == "winner"
            assert all(name == providers[winner]["name"] for name, _ in calls)


def test_cli_catalog_reads_current_codex_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(A.Path, "home", lambda: tmp_path)
    cache = tmp_path / ".codex/models_cache.json"
    cache.parent.mkdir()
    cache.write_text('{"models":[{"slug":"current-model","visibility":"list"},'
                     '{"slug":"internal-review","visibility":"hide"}]}')
    assert A._cli_model_ids("codex") == ["current-model"]


def test_auto_pool_rejects_tools_wildcards_and_flags():
    p = provider()
    p["auto_models"] = ["fast", "fast", "--unsafe", "model*", "whisper-audio", "image-generator", 7, {}]
    assert A._auto_model_ids(p) == ["fast"]


def test_account_quota_advances_provider_without_trying_other_cli_models(monkeypatch):
    calls = []
    def call(p, *args):
        calls.append(p["model"])
        raise OpenCodeError("account quota exhausted", status=402)
    monkeypatch.setattr(A, "_call_cli_llm", call)
    monkeypatch.setattr(A, "_call_openai_llm", lambda *a: "other")
    cfg = {"llm": {"providers": [provider("grok", "cli"), {"name": "other", "model": "fixed"}]}}
    assert A.llm_complete(cfg, "s", "u") == "other"
    assert calls == ["fast"]


def test_aliases_of_one_api_do_not_retry_cooling_models(monkeypatch):
    first = {**provider("tokenharbor"), "url": "https://same.invalid/v1"}
    alias = {**provider("tokenharbor-mimo"), "url": "https://same.invalid/v1"}
    calls = []
    def call(p, *args):
        calls.append((p["name"], p["model"]))
        if p["name"] != "other":
            raise OpenCodeError("limit", status=429, retry_after=120)
        return "other"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [first, alias, {"name": "other", "model": "fixed"}]}}
    assert A.llm_complete(cfg, "s", "u") == "other"
    assert calls == [("tokenharbor", "fast"), ("tokenharbor", "backup"), ("other", "fixed")]


def test_codex_uses_provider_reasoning_level_without_changing_global_config(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(A, "_cli_path", lambda _: "codex.exe")
    def run(cmd, prompt, timeout):
        assert cmd[cmd.index("-c") + 1] == 'model_reasoning_effort="low"'
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text("ok")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(A, "_run_cli", run)
    assert A._call_cli_llm({"cli": "codex", "model": "current", "reasoning_effort": "low"}, "s", "u", 12) == "ok"


def test_grok_uses_isolated_text_mode_and_reports_account_quota(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(A, "_cli_path", lambda _: "grok.exe")
    def run(cmd, prompt, timeout, *, cwd):
        assert Path(cwd).resolve() != A.ROOT.resolve()
        assert cmd[cmd.index("--tools") + 1] == ""
        assert "--disable-web-search" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "plain"
        return SimpleNamespace(returncode=1, stdout="Grok Build usage balance exhausted", stderr="")
    monkeypatch.setattr(A, "_run_cli", run)
    with pytest.raises(OpenCodeError, match="account quota") as error:
        A._call_cli_llm({"cli": "grok"}, "s", "u", 12)
    assert error.value.status == 402


def test_auto_models_share_deadline_and_reserve_other_providers(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    calls = []
    def call(p, s, u, timeout, max_tokens):
        calls.append((p["name"], timeout))
        clock[0] += timeout
        raise TimeoutError("timed out")
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [provider("a"), provider("b"), provider("c")],
                   "max_total_timeout_seconds": 12}}
    assert A.llm_complete(cfg, "s", "u") == ""
    assert clock[0] == pytest.approx(112)
    assert [name for name, _ in calls] == ["a", "a", "b", "b", "c", "c"]
    assert [timeout for _, timeout in calls] == pytest.approx([2.4, 1.6] * 3)


def test_short_review_budget_does_not_starve_a_working_model_in_a_long_chain(monkeypatch):
    clock, calls = [100.0], []
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    def call(p, s, u, timeout, max_tokens):
        calls.append(p["name"])
        clock[0] += min(4, timeout)
        if timeout < 4:
            raise TimeoutError("review needs four seconds")
        return '{"reviews":[]}'
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [provider(f"p{i}") for i in range(10)]}}
    assert A.llm_complete(cfg, "s", "u", budget=25) == '{"reviews":[]}'
    assert calls == ["p0"] and clock[0] == 104


def test_short_review_budget_still_bounds_a_long_chain_of_timeouts(monkeypatch):
    clock, calls = [100.0], []
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    def call(p, s, u, timeout, max_tokens):
        calls.append(p["name"])
        clock[0] += timeout
        raise TimeoutError("timed out")
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [provider(f"p{i}") for i in range(10)]}}
    assert A.llm_complete(cfg, "s", "u", budget=25) == ""
    assert len(set(calls)) >= 2 and clock[0] <= 125


def test_vision_auto_keeps_fallback_after_rejected_answer(monkeypatch):
    monkeypatch.setattr(A, "_vision_model_ids", lambda *a: ["fast", "backup"])
    calls = []
    def call(p, *args, images):
        assert images == [b"fixture"]
        calls.append(p["model"])
        return "wrong" if p["model"] == "fast" else "accepted"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    cfg = {"llm": {"providers": [provider()]}}
    assert A.llm_vision(cfg, "s", "u", [b"fixture"], accept=lambda text: text == "accepted") == "accepted"
    assert calls == ["fast", "backup"]


@pytest.mark.parametrize("kind", ["cli", "openai"])
def test_panel_probe_uses_shared_auto_selection(monkeypatch, capsys, kind):
    import json
    p = provider("codex" if kind == "cli" else "groq", kind)
    monkeypatch.setattr(A, "_cfg_raw", lambda: {"llm": {"providers": [p]}})
    monkeypatch.setattr(A, "read_json", lambda _: {})
    monkeypatch.setattr(A, "_cli_path", lambda _: "cli.exe")
    calls = []
    def call(p, *a):
        calls.append(p["model"])
        if len(calls) == 1:
            raise OpenCodeError("limit", status=429)
        return "ok"
    monkeypatch.setattr(A, "_call_openai_llm", call)
    monkeypatch.setattr(A, "_call_cli_llm", call)
    args = SimpleNamespace(set_key=None, enable=None, disable=None, models=None,
                           set_model=None, only=p["name"], probe=True)
    A.llm_cmd(args)
    assert json.loads(capsys.readouterr().out)[0]["ok"] is True
    assert calls == ["fast", "backup"]


def test_cli_timeout_keeps_a_quota_error_reported_before_shutdown(monkeypatch):
    waits = []
    def popen(cmd, **kwargs):
        kwargs["stderr"].write(b'API error (status 402 Payment Required): Grok Build usage balance exhausted')
        kwargs["stderr"].flush()
        def wait(timeout):
            waits.append(timeout)
            if len(waits) == 1:
                raise A.subprocess.TimeoutExpired(cmd, timeout)
        return SimpleNamespace(pid=123, wait=wait, kill=lambda: None)
    monkeypatch.setattr(A.subprocess, "Popen", popen)
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **kw: None)
    with pytest.raises(OpenCodeError, match="account quota") as error:
        A._run_cli(["test-cli"], None, 2)
    assert error.value.status == 402
