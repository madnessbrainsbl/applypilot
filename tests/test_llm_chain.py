import autopilot as A


def demo():
    cfg = {"llm": {"providers": [{"name": "hosted", "kind": "openai", "url": "http://x/v1", "model": "m"}, {"name": "codex", "kind": "cli", "cli": "codex"}]}}
    chain = A.llm_providers(cfg)
    assert len(chain) >= 2, "the point of the chain is that one dead provider is not the end"
    names = [p.get("name") for p in chain]
    assert "codex" in names, "the CLI provider needs no API key, so it belongs last in the chain"

    for p in chain:
        assert p.get("kind") in ("openai", "cli"), p
        if p["kind"] == "openai":
            assert p.get("url"), p
        else:
            assert p.get("cli"), p

    legacy = A.llm_providers({"llm": {"base_url": "http://x/v1", "model": "m"}})
    assert len(legacy) == 1 and legacy[0]["url"] == "http://x/v1", \
        "a config without a providers list must still work"

    disabled = A.llm_providers({"llm": {"providers": [
        {"name": "off", "kind": "openai", "url": "http://x", "enabled": False},
        {"name": "on", "kind": "openai", "url": "http://y"}]}})
    assert [p["name"] for p in disabled] == ["on"], "a disabled provider must be skipped"

    assert A.llm_complete({"llm": {"providers": []}}, "s", "u") == "", \
        "no providers means no answer, not a crash"

    calls = []
    def fake(provider, system, user, timeout, max_tokens):
        calls.append(provider["name"])
        if provider["name"] == "big":
            raise RuntimeError("Error code: 429 - Request too large for model")
        if calls.count("busy") == 1:
            raise RuntimeError("Error code: 429 - Rate limit exceeded. Retry after 0 seconds")
        return "ok"
    real, real_sleep = A._call_openai_llm, A.time.sleep
    A._call_openai_llm, A.time.sleep = fake, lambda s: None
    try:
        chain = {"llm": {"providers": [{"name": "busy", "kind": "openai", "url": "u"},
                                       {"name": "big", "kind": "openai", "url": "u"}]}}
        assert A.llm_complete(chain, "s", "u") == "", "limited providers are skipped without repeating the request"
        assert calls == ["busy", "big"], calls

        def slow(provider, system, user, timeout, max_tokens):
            calls.append(provider["name"])
            if provider["name"] == "hang":
                raise RuntimeError("timed out after 90s")
            return "ok"
        A._call_openai_llm, calls[:] = slow, []
        chain = {"llm": {"providers": [{"name": "hang", "kind": "openai", "url": "u"},
                                       {"name": "good", "kind": "openai", "url": "u"}]}}
        assert A.llm_complete(chain, "s", "u") == "ok" and A.llm_complete(chain, "s", "u") == "ok"
        assert calls == ["hang", "good", "good"], "a provider that timed out is benched, not waited on every call"
        assert "big" in A._LLM_BENCH and "busy" in A._LLM_BENCH, "limits have a cooldown across requests"

        A._LLM_BENCH.clear()
        order = [p["name"] for p in A.llm_providers({"llm": {"providers": [
            {"name": "codex", "kind": "cli", "cli": "codex"},
            {"name": "groq", "kind": "openai", "url": "https://api.groq.com/openai/v1", "model": "auto"}]}})]
        assert order == ["codex", "groq"], order

        assert A._chat_model_ids(
            ["whisper-large-v3", "llama-3.1-8b-instant", "meta-llama/llama-guard-4", "openai/gpt-oss-120b"]
        )[0] == "openai/gpt-oss-120b"

        def listed(_provider, force=False):
            return ["whisper-large-v3", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
        real_listed, calls[:] = A._listed_models, []
        A._listed_models = listed

        def rotated(provider, system, user, timeout, max_tokens):
            calls.append((provider["name"], provider.get("model")))
            if provider.get("model") == "llama-3.3-70b-versatile":
                raise RuntimeError("The model `llama-3.3-70b-versatile` does not exist")
            return "ok"
        A._call_openai_llm = rotated
        chain = {"llm": {"providers": [
            {"name": "groq", "kind": "openai", "url": "https://api.groq.com/openai/v1", "model": "auto"},
            {"name": "next", "kind": "openai", "url": "https://other/v1", "model": "pinned"}]}}
        assert A.llm_complete(chain, "s", "u") == "ok"
        assert calls == [("groq", "llama-3.3-70b-versatile"), ("groq", "llama-3.1-8b-instant")], calls
        assert "groq" not in A._LLM_BENCH, "a renamed model must not bench the whole provider"
        A._listed_models = real_listed
    finally:
        A._call_openai_llm, A.time.sleep = real, real_sleep
    print("llm chain self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
