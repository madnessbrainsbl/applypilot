import autopilot as A


def demo():
    cfg = {"llm": {"providers": [
        {"name": "hosted", "kind": "openai", "url": "http://x/v1", "model": "m"},
        {"name": "codex", "kind": "cli", "cli": "codex"}]}}
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
        assert A.llm_complete(chain, "s", "u") == "ok", "a rate-limited provider gets one retry"
        assert calls == ["busy", "big", "busy"], calls
    finally:
        A._call_openai_llm, A.time.sleep = real, real_sleep
    print("llm chain self-check OK")


if __name__ == "__main__":
    demo()
