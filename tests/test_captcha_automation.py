import base64
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import autopilot as A


@pytest.mark.parametrize("text,expected", [
    ('{"coordinates":[{"box_2d":[0,0]},{"box_2d":[0,2]}]}', [0, 2]),
    ('{"coordinates":[{"box_2d":[1,1]},{"box_2d":[2,2]}]}', [4, 8]),
    ('{"coordinates":[{"box_2d":[0,2]}]}', [2]),
    ('{"coordinates":[{"box_2d":[1,1]},{"box_2d":[1,1]}]}', [4]),
    ('{"cells":[1,2],"coordinate_system":"zero_based"}', [1, 2]),
    ('{"coordinates":[{"box_2d":[0,2]}]', []),
])
def test_grid_json_is_not_flat_tile_list(text, expected):
    assert A._hcaptcha_parse_cells(text) == expected


@pytest.mark.parametrize("prompt,count", [
    ("Please click on the TWO shapes that are different from the others", 2),
    ("Choose 2 arrows that are outliers", 2),
    ("Click three objects", 3),
    ("Select animals looking away from you", None),
    ("Коснитесь предмета, который можно взять внутрь этого места", 1),
    ("Tap the object that belongs in this place", 1),
    ("Tap the thing you can take inside the place", 1),
    ("Выберите предметы, которые можно взять внутрь этого места", None),
])
def test_explicit_selection_count(prompt, count):
    assert A._hcaptcha_exact_count(prompt) == count


def test_live_move_prompt_is_not_empty_when_try_again_is_present():
    assert A._hcaptcha_prompt_from_text('Move the correct vial into the empty slot matching its shape Please try again. ⚠️ Skip EN') == 'Move the correct vial into the empty slot matching its shape ⚠️'
    assert A._hcaptcha_is_drag('Move the correct vial into the empty slot matching its shape')
    assert not A._hcaptcha_is_drag('Select animals that move quickly')


def test_loading_canvas_is_not_sent_to_vision():
    img = np.full((400, 400, 3), 230, np.uint8)
    cv2.ellipse(img, (200, 240), (15, 15), 0, 30, 330, (35, 35, 35), 4)
    assert not A._hcaptcha_image_ready(cv2.imencode('.png', img)[1].tobytes())
    assert A._hcaptcha_image_ready((Path(__file__).parent / 'fixtures/captcha-animals.png').read_bytes())


def test_two_objects_requires_two_unique_points():
    box = {"x": 0, "y": 0, "width": 300, "height": 300}
    assert A._hcaptcha_parse_points('{"points":[{"x":50,"y":80}]}', box, 2) == []
    assert A._hcaptcha_parse_points('{"points":[{"x":50,"y":80},{"x":50,"y":80}]}', box, 2) == []


def test_partial_selection_is_not_verified(monkeypatch):
    verified = []
    monkeypatch.setattr(A, '_hcaptcha_tile_boxes', lambda f: [])
    def spatial(page, frame, prompt, want, exact=True):
        assert want == 2 and exact is True
        return 1
    monkeypatch.setattr(A, '_hcaptcha_click_spatial', spatial)
    monkeypatch.setattr(A, '_hcaptcha_submit_button', lambda *a: verified.append(True))
    assert A._hcaptcha_vision_fallback(None, None, "Click TWO shapes that are different") is False
    assert not verified


def test_real_browser_grid_selection_and_hidden_frame_token(monkeypatch, tmp_path):
    """Replay a real provider response in a real browser, with an isolated challenge DOM."""
    png = (Path(__file__).parent / 'fixtures/captcha-animals.png').read_bytes()
    image = base64.b64encode(png).decode()
    cells = ''.join(f'<div class="task-image" data-i="{i}" aria-pressed="{str(i == 0).lower()}" style="position:absolute;left:{10 + (i % 3) * 125}px;top:{120 + (i // 3) * 130}px;width:115px;height:115px"></div>' for i in range(9))
    challenge = f'''<html><body style="margin:0"><div class="challenge-view" style="position:relative;width:380px;height:500px;background-image:url(data:image/png;base64,{image})">
        <div class="prompt-text" style="color:transparent">Select animals looking away from you</div>{cells}</div><button class="button-submit">Verify</button>
        <script>document.querySelectorAll('.task-image').forEach(e=>e.onclick=()=>e.setAttribute('aria-pressed',e.getAttribute('aria-pressed')==='true'?'false':'true'));
        document.querySelector('button').onclick=()=>{{const selected=[...document.querySelectorAll('.task-image')].filter(e=>e.getAttribute('aria-pressed')==='true').map(e=>Number(e.dataset.i));if(JSON.stringify(selected)==='[0,2]')parent.postMessage('isolated-solved','*')}};</script></body></html>'''
    parent = '<textarea name="h-captcha-response"></textarea><iframe id="challenge" src="https://newassets.hcaptcha.com/isolated#frame=challenge" style="width:600px;height:650px"></iframe><script>addEventListener("message",e=>{if(e.data==="isolated-solved"){document.querySelector("textarea").value="isolated-confirmed-response-token";document.querySelector("#challenge").style.display="none"}})</script>'
    monkeypatch.setattr(A, '_HCAPTCHA_CACHE', tmp_path)
    monkeypatch.setattr(A, 'CONTROL', tmp_path / 'control.json')
    monkeypatch.setattr(A, '_QUIT_RUN', {"v": False})
    monkeypatch.setattr(A, '_hcaptcha_alias', lambda: {})
    monkeypatch.setattr(A, 'llm_vision', lambda *a, **kw: '{"coordinates":[{"box_2d":[0,0]},{"box_2d":[0,2]}]}')
    with A.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('https://captcha.test/**', lambda route: route.fulfill(body=parent, content_type='text/html'))
        page.route('https://newassets.hcaptcha.com/**', lambda route: route.fulfill(body=challenge, content_type='text/html'))
        page.goto('https://captcha.test/')
        page.wait_for_timeout(200)
        assert A._captcha_challenge_open(page)
        assert not A._hcaptcha_verified(page)
        assert A._solve_hcaptcha_local(page)
        assert not A._captcha_challenge_open(page)  # hidden iframe still exists
        assert A._hcaptcha_verified(page)
        page.locator('textarea').fill('')
        assert not A._hcaptcha_verified(page)  # hiding alone cannot report success
        browser.close()
