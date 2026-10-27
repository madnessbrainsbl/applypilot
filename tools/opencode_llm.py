"""Text-only OpenCode 2 provider; Free and Go never share credentials or fallback."""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from email.utils import parsedate_to_datetime


_CATALOG = {}
STATE = Path(__file__).resolve().parents[1] / "autopilot/cache/opencode_free.db"


class OpenCodeError(RuntimeError):
    def __init__(self, message, *, status=None, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _state(state_path=None):
    path = state_path or STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=2)
    try:
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE IF NOT EXISTS models (model TEXT PRIMARY KEY, status TEXT, "
                    "seconds REAL, checked_at REAL, retry_at REAL)")
    except Exception:
        con.close()
        raise
    return con


def health(*, state_path=None):
    try:
        with closing(_state(state_path)) as con:
            return {row["model"]: dict(row) for row in con.execute("SELECT * FROM models")}
    except (OSError, sqlite3.Error):
        print("OpenCode model health cache unavailable", file=sys.stderr)
        return {}


def record_result(model, seconds, error=None, *, state_path=None):
    status, delay = "ok", 0
    if error is not None:
        message = str(error).lower()
        code = getattr(error, "status", None)
        if "quota" in message or code == 402:
            status, delay = "quota", 3600
        elif code == 429:
            status, delay = "rate_limit", 300
        elif code in (401, 403, 404) or "unavailable" in message:
            status, delay = "unavailable", 600
        elif "timed out" in message or "timeout" in message:
            status, delay = "timeout", 15
        else:
            status, delay = "error", 30
        delay = getattr(error, "retry_after", 0) or delay
    now = time.time()
    try:
        with closing(_state(state_path)) as con, con:
            con.execute("INSERT INTO models VALUES (?, ?, ?, ?, ?) ON CONFLICT(model) DO UPDATE SET "
                        "status=excluded.status, seconds=excluded.seconds, checked_at=excluded.checked_at, "
                        "retry_at=excluded.retry_at", (model, status, seconds, now, now + delay if delay else 0))
    except (OSError, sqlite3.Error):
        print("OpenCode model health could not be saved", file=sys.stderr)
    return now + delay if delay else 0


def retry_after(headers):
    value = next((v for k, v in (headers or {}).items() if k.lower() == "retry-after"), "")
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except (ValueError, TypeError):
        try:
            return max(0, parsedate_to_datetime(str(value)).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


def complete_auto(system, user, timeout, run, candidates=None):
    deadline = time.monotonic() + timeout
    live = models("free")
    pool = list(dict.fromkeys(mid for mid in (live if candidates is None else candidates) if mid in live))
    if not pool:
        raise RuntimeError("No verified free OpenCode models in the current catalog")
    failures = []
    for index, mid in enumerate(pool):
        if health().get(mid, {}).get("retry_at", 0) > time.time():
            continue
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        # Leave time for another free model after a stalled request.
        attempt_timeout = min(20, remaining * 0.6) if index + 1 < len(pool) else remaining
        start = time.monotonic()
        try:
            answer = complete(mid, system, user, attempt_timeout, run)
            if not answer:
                raise RuntimeError("empty answer")
        except (RuntimeError, OSError, ValueError) as error:
            record_result(mid, time.monotonic() - start, error)
            failures.append(mid)
            print(f"OpenCode: {mid} failed; trying next free model", file=sys.stderr, flush=True)
            continue
        record_result(mid, time.monotonic() - start)
        print(f"OpenCode: answered by {mid}", file=sys.stderr, flush=True)
        return answer
    raise RuntimeError(f"OpenCode Free: no answer ({len(failures)} attempts; other models in cooldown or timeout)")


def command():
    found = os.environ.get("OPENCODE_BIN") or shutil.which("opencode")
    if not found:
        raise RuntimeError("OpenCode CLI not installed: https://opencode.ai")
    if os.name == "nt" and found.lower().endswith((".cmd", ".ps1")):
        found = str(Path(found).parent / "node_modules/@opencode/cli/bin/opencode.exe")
    if not Path(found).is_file():
        raise RuntimeError("OpenCode 2 executable not found; install OpenCode 2")
    return found


def models(mode="free"):
    if mode not in ("free", "go"):
        raise ValueError("Choose OpenCode Free or Go")
    cached = _CATALOG.get(mode)
    if cached and cached[0] > time.monotonic():
        return list(cached[1])
    suffix = "/go" if mode == "go" else ""
    req = urllib.request.Request("https://opencode.ai/inference" + suffix + "/v1/models",
                                 headers={"User-Agent": "ApplyPilot/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as response:
        payload = json.load(response)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("OpenCode returned an invalid model catalog")
    prefix = "opencode-go/" if mode == "go" else "opencode/"
    ids = []
    for row in rows:
        mid = row.get("id") if isinstance(row, dict) else None
        if not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", mid):
            continue
        # Jev produces typed decisions rather than conversational answers.
        if mid.startswith("jev-") or mid == "test":
            continue
        if mode == "free" and not (mid.endswith("-free") or mid == "big-pickle"):
            continue
        if prefix + mid not in ids:
            ids.append(prefix + mid)
    if not ids:
        raise RuntimeError("OpenCode has no available text models")
    _CATALOG[mode] = (time.monotonic() + 300, ids)
    return list(ids)


def _go_key():
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "opencode"
    database = data / "opencode.db"
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as con:
            row = con.execute("SELECT value FROM credential WHERE integration_id=? AND active=1 "
                              "ORDER BY time_updated DESC LIMIT 1", ("opencode-go",)).fetchone()
            if row:
                key = json.loads(row[0]).get("key")
                if isinstance(key, str) and key.strip():
                    return key.strip()
    auth = data / "auth.json"
    if auth.is_file():
        key = json.loads(auth.read_text(encoding="utf-8")).get("opencode-go", {}).get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    raise RuntimeError("Connect OpenCode Go in OpenCode using /connect")


def complete(model, system, user, timeout, run):
    deadline = time.monotonic() + timeout
    exe = command()
    model = model or "opencode/big-pickle"
    prefix = model.partition("/")[0]
    if prefix not in ("opencode", "opencode-go"):
        raise ValueError("Use an opencode/ Free model or an opencode-go/ paid model")
    mode = "go" if prefix == "opencode-go" else "free"
    if model not in models(mode):
        raise ValueError("OpenCode model is not in the current catalog")
    key = _go_key() if mode == "go" else ""
    with tempfile.TemporaryDirectory(prefix="applypilot-opencode-", ignore_cleanup_errors=True) as directory:
        profile = Path(directory)
        config = {"update": "disable", "snapshots": False,
                  "policies": [{"action": "provider.use", "resource": "*", "effect": "deny"},
                               {"action": "provider.use", "resource": prefix, "effect": "allow"}],
                  # Free requires the stock tool definitions; never grant a tool approval.
                  "permissions": [{"action": "*", "resource": "*", "effect": "ask"}]}
        if key:
            config["providers"] = {prefix: {"settings": {"apiKey": "{env:APPLYPILOT_OPENCODE_GO_KEY}"}}}
        (profile / "opencode.json").write_text(json.dumps(config), encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("OPENCODE_", "XDG_", "OPENAI_", "ANTHROPIC_", "GOOGLE_", "GEMINI_"))
               and not k.endswith(("_API_KEY", "_API_TOKEN"))}
        for name, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                             ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache")):
            env[name] = str(profile / folder)
        env["OPENCODE_DISABLE_CLAUDE_CODE"] = "true"
        env.pop("APPLYPILOT_OPENCODE_GO_KEY", None)
        if key:
            env["APPLYPILOT_OPENCODE_GO_KEY"] = key
        prompt = ("Answer directly. Do not use tools, read files, run commands or ask for permissions.\n\n"
                  + system + "\n\n" + user)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("OpenCode timed out during model discovery")
        result = run([exe, "run", "--standalone", "--model", model, "--agent", "build", "--format", "json"],
                     prompt, remaining, env=env, cwd=directory)
        parts = {}
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            part = event.get("part") or {}
            if not isinstance(part, dict):
                continue
            if event.get("type") == "error":
                # Do not echo provider payloads: they may include request text or credentials.
                error = event.get("error") or {}
                if isinstance(error, dict):
                    if error.get("type") == "provider.no-route":
                        raise OpenCodeError("OpenCode model unavailable in the installed CLI")
                    status = error.get("status")
                    response = error.get("response") or {}
                    headers = response.get("headers") if isinstance(response, dict) else None
                    retry = retry_after(headers if isinstance(headers, dict) else {})
                    low = str(error.get("message") or "").lower()
                    quota = any(word in low for word in ("quota", "usage limit", "insufficient credits", "daily limit", "monthly limit"))
                    if quota or "rate limit" in low or "too many requests" in low:
                        status = 429
                    label = "quota exhausted" if quota or status == 402 else "request failed"
                    if isinstance(status, int) and 400 <= status <= 599:
                        raise OpenCodeError(f"OpenCode HTTP {status}: {label}", status=status, retry_after=retry)
                raise RuntimeError("OpenCode request failed; check model availability in OpenCode")
            if any(word in str(event.get("type", "")) for word in ("tool", "permission")) or part.get("type") == "tool":
                raise RuntimeError("OpenCode requested a tool; this provider accepts text answers only")
            if event.get("type") == "text" and isinstance(part.get("text"), str):
                parts[part.get("id", "text")] = part["text"]
        text = "\n".join(parts.values()).strip()
        if result.returncode or not text:
            raise RuntimeError("OpenCode returned no answer; check the selected model and CLI version")
        return text
