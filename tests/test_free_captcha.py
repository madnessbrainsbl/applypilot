import sys
import threading
import io
import json
from types import SimpleNamespace

import autopilot as A
from setup_free_captcha import repair_windows_manifest


def test_local_llama_vision_discovery_requires_server_vision_capability(monkeypatch):
    calls = []
    def response(request, **kwargs):
        calls.append(request.full_url)
        data = {'data': [{'id': 'qwen3-vl-4b-instruct'}]} if request.full_url.endswith('/models') else {'modalities': {'vision': True}}
        return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(A, '_MODEL_CACHE', {})
    monkeypatch.setattr(A.urllib.request, 'urlopen', response)
    assert A._vision_model_ids({'name': 'lmstudio', 'url': 'http://127.0.0.1:1234/v1'}) == ['qwen3-vl-4b-instruct']
    assert calls == ['http://127.0.0.1:1234/v1/models', 'http://127.0.0.1:1234/props']


def test_local_llama_text_only_is_never_assumed_to_support_images(monkeypatch):
    monkeypatch.setattr(A, '_MODEL_CACHE', {})
    def response(request, **kwargs):
        data = {'data': [{'id': 'qwen-model'}]} if request.full_url.endswith('/models') else {'modalities': {'vision': False}}
        return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(A.urllib.request, 'urlopen', response)
    assert A._vision_model_ids({'name': 'lmstudio', 'url': 'http://127.0.0.1:1234/v1'}) == []


def test_driver_factory_matches_selected_browser(monkeypatch):
    monkeypatch.setattr(A, 'sync_playwright', lambda: 'patchright')
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=lambda: 'playwright'))
    assert A.browser_playwright({}) == 'patchright'
    assert A.browser_playwright({'browser': {'engine': 'camoufox'}}) == 'playwright'


def test_free_vision_never_discovers_or_calls_remote_providers(monkeypatch):
    monkeypatch.setattr(A, 'llm_providers', lambda cfg: [
        {'name': 'remote', 'url': 'https://api.example.com/v1'},
        {'name': 'fake-local', 'url': 'https://localhost.evil.example/v1'},
        {'name': 'antigravity', 'url': 'http://127.0.0.1:8317/v1'},
        {'name': 'lmstudio', 'url': 'https://api.example.com/v1'}])
    monkeypatch.setattr(A, '_vision_model_ids', lambda p: (_ for _ in ()).throw(AssertionError('Remote API called')))
    assert A.llm_vision({'apply': {'captcha_local_only': True}}, '', '', []) == ''


def test_free_vision_can_use_existing_loopback_model(monkeypatch):
    monkeypatch.setattr(A, 'llm_providers', lambda cfg: [
        {'name': 'lmstudio', 'url': 'http://127.0.0.1:1234/v1'},
        {'name': 'remote', 'url': 'https://api.example.com/v1'}])
    monkeypatch.setattr(A, '_quit_requested', lambda: False)
    monkeypatch.setattr(A, '_vision_model_ids', lambda p: ['local-vision'])
    calls = []
    monkeypatch.setattr(A, '_call_openai_llm', lambda p, *a, **k: calls.append(p['name']) or 'answer')
    assert A.llm_vision({'apply': {'captcha_local_only': True}}, '', '', []) == 'answer'
    assert calls == ['lmstudio']


def test_local_vision_never_falls_back_to_loopback_cloud_gateway(monkeypatch, capsys):
    monkeypatch.setattr(A, 'llm_providers', lambda cfg: [
        {'name': 'antigravity', 'url': 'http://127.0.0.1:8317/v1'},
        {'name': 'lmstudio', 'url': 'http://127.0.0.1:1234/v1'}])
    monkeypatch.setattr(A, '_quit_requested', lambda: False)
    def models(provider):
        assert provider['name'] == 'lmstudio'
        return []
    monkeypatch.setattr(A, '_vision_model_ids', models)
    monkeypatch.setattr(A, '_call_openai_llm', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Cloud gateway called')))
    assert A.llm_vision({'apply': {'captcha_local_only': True}}, '', '', []) == ''
    assert 'lmstudio: no available vision models' in capsys.readouterr().err


def test_camoufox_preserves_chrome_profile_and_identity(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(A, "camoufox_setup", lambda: None)
    ctx = SimpleNamespace(add_init_script=lambda *a: None, set_default_timeout=lambda *a: None,
                          set_default_navigation_timeout=lambda *a: None)
    monkeypatch.setitem(sys.modules, 'camoufox.sync_api', SimpleNamespace(
        NewBrowser=lambda pw, **kwargs: calls.append((pw, kwargs)) or ctx))
    monkeypatch.setattr(A, '_profile_dir', lambda cfg, worker_id: tmp_path / 'chrome-w2')
    browser = A.launch_browser('driver', {'browser': {'engine': 'camoufox', 'cdp_url': 'unused'}}, True, 2)
    assert A.new_browser_context(browser, {}, user_agent='must-not-override-firefox') is ctx
    assert calls[0][0] == 'driver'
    assert calls[0][1]['user_data_dir'] == str(tmp_path / 'chrome-w2') + '-camoufox'
    assert calls[0][1]['persistent_context'] is True
    assert 'user_agent' not in calls[0][1] and 'args' not in calls[0][1]
    assert not (tmp_path / 'chrome-w2').exists()


def test_local_audio_reuses_model_and_never_calls_api(monkeypatch):
    calls = []
    class Model:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))
        def transcribe(self, stream, **kwargs):
            assert stream.read() == b'audio'
            return iter([SimpleNamespace(text=' hello '), SimpleNamespace(text=' world ')]), None
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(A, '_TL', threading.local())
    monkeypatch.setattr(A, '_quit_requested', lambda: False)
    monkeypatch.setattr(A.urllib.request, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('API called')))
    assert A._transcribe_audio_bytes(b'audio') == 'hello world'
    assert A._transcribe_audio_bytes(b'audio') == 'hello world'
    assert len(calls) == 1 and calls[0][1]['local_files_only'] is True


def test_stopped_audio_does_not_load_model(monkeypatch):
    monkeypatch.setattr(A, '_quit_requested', lambda: True)
    assert A._transcribe_audio_bytes(b'audio') == ''


def test_audio_failure_is_explicit_and_not_retried_via_paid_api(monkeypatch, capsys):
    def missing(*a, **k):
        raise RuntimeError('missing local model')
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=missing))
    monkeypatch.setattr(A, '_TL', threading.local())
    monkeypatch.setattr(A, '_quit_requested', lambda: False)
    assert A._transcribe_audio_bytes(b'audio') == ''
    assert 'missing local model' in capsys.readouterr().err


def test_windows_manifest_repair_preserves_original_and_is_idempotent(tmp_path):
    executable = tmp_path / 'camoufox.exe'
    original = b'prefix<dependency><dependentAssembly><assemblyIdentity type="win32" name="mozglue" version="1.0.0.0" language="*" /></dependentAssembly></dependency>suffix'
    executable.write_bytes(original)
    assert repair_windows_manifest(executable)
    assert executable.with_suffix('.exe.before-sxs-fix').read_bytes() == original
    assert len(executable.read_bytes()) == len(original)
    assert b'<file name="mozglue.dll" />' in executable.read_bytes()
    assert not repair_windows_manifest(executable)
