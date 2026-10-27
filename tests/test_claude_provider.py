import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import autopilot as A


def test_discovers_vscode_claude_native_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(A.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(A.shutil, "which", lambda _: None)
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    binary = tmp_path / ".vscode/extensions/anthropic.claude-code-2.1.301-win32-x64/resources/native-binary/claude.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    assert A._cli_path("claude") == str(binary)


def test_claude_uses_text_only_isolated_print_mode(monkeypatch):
    monkeypatch.setattr(A, "_cli_path", lambda _: "claude.exe")
    def run(cmd, prompt, timeout, *, cwd):
        assert "--safe-mode" in cmd and "--no-session-persistence" in cmd
        assert cmd[cmd.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in cmd
        assert "private question" in prompt and "private question" not in cmd
        assert Path(cwd).resolve() != A.ROOT.resolve()
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": "ok", "is_error": False}))
    monkeypatch.setattr(A, "_run_cli", run)
    assert A._call_cli_llm({"cli": "claude"}, "s", "private question", 20) == "ok"


def test_claude_quota_is_failure_not_an_answer(monkeypatch):
    monkeypatch.setattr(A, "_cli_path", lambda _: "claude.exe")
    monkeypatch.setattr(A, "_run_cli", lambda *a, **kw: SimpleNamespace(returncode=1,
        stdout=json.dumps({"result": "You've hit your weekly limit", "is_error": True})))
    with pytest.raises(RuntimeError, match="quota exhausted"):
        A._call_cli_llm({"cli": "claude"}, "s", "u", 20)
