import autopilot as A


def demo():
    cfg = A.read_json(A.CFG)
    profile = A._profile_dir(cfg)
    assert profile is not None, "a real Chrome profile keeps logins between runs"
    assert profile.is_absolute()

    saved = []
    class FakeCtx:
        def storage_state(self, path=None):
            saved.append(path)

    A.save_browser_state(FakeCtx(), cfg)
    assert not saved, \
        "with a profile in use the storage_state snapshot must not be rewritten — it once wiped every saved login"

    own_browser = {k: v for k, v in cfg["browser"].items() if k not in ("user_data_dir", "cdp_url")}
    A.save_browser_state(FakeCtx(), {**cfg, "browser": own_browser})
    assert saved, "with neither a profile nor a CDP attach the snapshot is the only place sessions live"

    saved.clear()
    A.save_browser_state(FakeCtx(), {**cfg, "browser": {**own_browser, "cdp_url": "http://127.0.0.1:9222"}})
    assert not saved, "when attached to the user's own Chrome its sessions are not ours to overwrite"
    print("persistent profile self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
