"""Offline checks for the free local hCaptcha solver. No network, no API key."""
import inspect
import autopilot as A


class _Frame:
    def __init__(self, url):
        self.url = url


class _Page:
    def __init__(self, url, frames=None, placed=1):
        self.url = url
        self.frames = frames or []
        self.placed = placed
        self.calls = []

    def evaluate(self, script, arg=None):
        self.calls.append(arg)
        if arg is None:
            return ""
        return self.placed


def demo():
    assert A._captcha_kind_from_src("https://newassets.hcaptcha.com/captcha/v1/x") == "hcaptcha"
    assert A._captcha_kind_from_src("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile") == "turnstile"
    assert A._captcha_kind_from_src("https://www.google.com/recaptcha/api2/anchor") == "recaptcha"
    assert A._captcha_kind_from_src("https://jobs.lever.co/acme") == ""

    lever = (
        "https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html"
        "#frame=challenge&id=0&host=jobs.lever.co&sitekey=11111111-2222-3333-4444-555555555555"
        "&rqdata=rq-from-hash"
    )
    parsed = A._captcha_from_url(lever)
    assert parsed["kind"] == "hcaptcha"
    assert parsed["sitekey"] == "11111111-2222-3333-4444-555555555555"
    assert parsed["rqdata"] == "rq-from-hash"

    rec = A._captcha_from_url("https://www.google.com/recaptcha/api2/anchor?k=site-key-1&size=normal")
    assert rec["kind"] == "recaptcha" and rec["sitekey"] == "site-key-1"

    turn = A._captcha_from_url(
        "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/0x4AAAAAAAAAAAAAAA/light")
    assert turn["kind"] == "turnstile" and turn["sitekey"].startswith("0x4AAA")
    assert A._captcha_from_url("https://jobs.lever.co/acme") is None

    class OpenPage:
        frames = [_Frame(
            "https://newassets.hcaptcha.com/captcha/v1/a/static/hcaptcha.html#frame=challenge&sitekey=abc")]

    assert A._captcha_challenge_open(OpenPage())
    assert not A._captcha_challenge_open(_Page("https://jobs.lever.co/acme"))

    alias = A._hcaptcha_alias_from_objects({
        "label_alias": {
            "boat2310": {"en": ["boat"]},
            "off_road_vehicle2310": {"en": ["off-road vehicle"]},
        }
    })
    assert alias["boat"] == "boat2310"
    assert A._hcaptcha_model_name("Please click each image containing a boat", alias) == "boat2310"
    assert A._hcaptcha_model_name("Select all images with an off-road vehicle", alias) == "off_road_vehicle2310"
    assert A._hcaptcha_model_name("Please click each image containing a dragon made of cheese", alias) == ""
    assert A._hcaptcha_prompt_label("Please click each image containing а boat") == "boat"

    class _Bare:
        frames = []

    assert A._solve_hcaptcha_local(_Bare()) is False

    class _Tiles:
        def __init__(self, n):
            self.n = n

        def count(self):
            return self.n

        def nth(self, i):
            return self

        def bounding_box(self):
            return {"x": 1, "y": 1, "width": 8, "height": 8}

        def screenshot(self, type="png"):
            return b""

    class _Prompt:
        def __init__(self, text):
            self.text = text

        @property
        def first(self):
            return self

        def count(self):
            return 1

        def inner_text(self, timeout=0):
            return self.text

    class _Button:
        def __init__(self, page):
            self.page = page

        @property
        def first(self):
            return self

        def count(self):
            return 1

        def bounding_box(self):
            return {"x": 2, "y": 2, "width": 8, "height": 8}

        def click(self, timeout=0):
            self.page.frames = []

    class _Challenge:
        def __init__(self, page, prompt, urls):
            self.page = page
            self.url = (
                "https://newassets.hcaptcha.com/captcha/v1/a/static/hcaptcha.html"
                "#frame=challenge&sitekey=abc"
            )
            self.prompt = prompt
            self.urls = urls
            self.tiles = _Tiles(len(urls))
            self.button = _Button(page)

        def locator(self, sel):
            if "prompt" in sel:
                return _Prompt(self.prompt)
            if "button-submit" in sel:
                return self.button
            return self.tiles

        def evaluate(self, script):
            return list(self.urls)

    class _GridPage:
        def __init__(self, prompt, urls):
            self.frames = [_Challenge(self, prompt, urls)]

        def evaluate(self, script, arg=None):
            return not self.frames  # simulated response token after the test callback

    nets = []
    clicks = []
    orig_alias = A._hcaptcha_alias
    orig_net = A._hcaptcha_net
    orig_yes = A._resnet_yes
    orig_dl = A._hcaptcha_download_bytes
    orig_click = A._hcaptcha_click
    orig_sleep = A.time.sleep
    orig_ready = A._hcaptcha_wait_ready
    A._hcaptcha_wait_ready = lambda *a, **kw: True
    A._hcaptcha_alias = lambda: alias
    A._hcaptcha_net = lambda stem: nets.append(stem) or object()
    A._resnet_yes = lambda net, blob: blob == b"yes"
    A._hcaptcha_download_bytes = lambda url: b"yes" if url.endswith("/yes") else b"no"
    def _click(page, loc):
        clicks.append(loc)
        click = getattr(loc, "click", None)
        if click:
            click()

    A._hcaptcha_click = _click
    A.time.sleep = lambda *args, **kwargs: None
    try:
        solved = _GridPage("Please click each image containing a boat", ["https://img/yes", "https://img/no"])
        assert A._solve_hcaptcha_local(solved) is True
        assert nets == ["boat2310"]
        assert solved.frames == []
        assert len(clicks) == 2
        missed = _GridPage("Please click each image containing a dragon made of cheese", ["https://img/yes"])
        assert A._solve_hcaptcha_local(missed) is False
        assert nets == ["boat2310"]
    finally:
        A._hcaptcha_alias = orig_alias
        A._hcaptcha_net = orig_net
        A._resnet_yes = orig_yes
        A._hcaptcha_download_bytes = orig_dl
        A._hcaptcha_click = orig_click
        A.time.sleep = orig_sleep
        A._hcaptcha_wait_ready = orig_ready

    src = inspect.getsource(A.try_solve_turnstile_or_captcha)
    assert "_solve_with_2captcha" not in src
    assert "_solve_hcaptcha_local" in src

    assert A._hcaptcha_is_outlier("Choose 2 arrows that are outliers in this circular pattern")
    assert not A._hcaptcha_is_outlier("Please click each image containing a boat")
    assert A._hcaptcha_choose_count("Choose 2 arrows that are outliers") == 2
    assert A._hcaptcha_prompt_from_text("Skip\nChoose 2 arrows that are outliers\nVerify") == (
        "Choose 2 arrows that are outliers")
    assert A._hcaptcha_prompt_from_text("Please try again\nPlease select an image to report.") == ""
    assert A._hcaptcha_prompt_from_text("Please select an image to report.\nSelect the tool used in a garden") == (
        "Select the tool used in a garden")
    assert A._hcaptcha_prompt_from_text(
        "Drag one of the icons to complete the circle\nPlease try again.") == (
        "Drag one of the icons to complete the circle")
    assert A._hcaptcha_prompt_from_text(
        "Drag one of the icons to complete the circle Please try again. Skip EN") == (
        "Drag one of the icons to complete the circle")
    assert A._hcaptcha_is_drag("Drag one of the icons to complete the circle")

    import math
    import cv2
    import numpy as np

    def paint_arrow(img, cx, cy, angle, length=46, width=8):
        tip = (cx + math.cos(angle) * length, cy + math.sin(angle) * length)
        back = (cx - math.cos(angle) * length * 0.7, cy - math.sin(angle) * length * 0.7)
        px, py = -math.sin(angle), math.cos(angle)
        pts = np.array([
            tip,
            (back[0] + px * width, back[1] + py * width),
            (back[0] - px * width, back[1] - py * width),
        ], np.int32)
        cv2.fillPoly(img, [pts], (20, 20, 20))

    def ring(directions):
        canvas = np.full((480, 480, 3), 245, np.uint8)
        centers = []
        for i, angle in enumerate(directions):
            theta = -math.pi / 2 + i * (2 * math.pi / len(directions))
            cx = 240 + math.cos(theta) * 150
            cy = 240 + math.sin(theta) * 150
            paint_arrow(canvas, cx, cy, angle)
            centers.append((cx, cy))
        return canvas, centers

    def nearest(pt, centers):
        return min(math.hypot(pt[0] - c[0], pt[1] - c[1]) for c in centers)

    same, same_centers = ring([0.0] * 6 + [math.pi / 2, math.pi / 2])
    same_hits = A._hcaptcha_outlier_points(same, 2)
    assert len(same_hits) == 2, same_hits
    for pt in same_hits:
        assert nearest(pt, same_centers[6:]) < 28, pt
        assert nearest(pt, same_centers[:6]) > 50, pt

    tangent = []
    for i in range(8):
        theta = -math.pi / 2 + i * (2 * math.pi / 8)
        tangent.append(theta + math.pi / 2)
    tangent[3] += math.pi
    tangent[6] += math.pi
    spun, spun_centers = ring(tangent)
    spun_hits = A._hcaptcha_outlier_points(spun, 2)
    assert len(spun_hits) == 2, spun_hits
    for pt in spun_hits:
        assert nearest(pt, [spun_centers[3], spun_centers[6]]) < 28, pt
        assert nearest(pt, [c for i, c in enumerate(spun_centers) if i not in (3, 6)]) > 50, pt

    box = {"x": 100, "y": 200, "width": 50, "height": 40}
    assert A._hcaptcha_parse_points(
        '{"challenge_prompt":"x","points":[{"x":110,"y":210},{"x":140,"y":230}]}', box, 2
    ) == [(110, 210), (140, 230)]
    assert A._hcaptcha_parse_points(
        '{"points":[{"x":110,"y":210}]}', box, 2) == []
    assert A._hcaptcha_parse_points(
        '{"points":[{"x":110,"y":210}]}', box, 6, exact=False) == [(110, 210)]
    same = {"x": 0, "y": 0, "width": 40, "height": 40}
    assert A._hcaptcha_is_regular_grid([dict(same) for _ in range(9)])
    scattered = [{"x": i * 30, "y": (i * 17) % 50, "width": 28, "height": 28} for i in range(7)]
    assert not A._hcaptcha_is_regular_grid(scattered)
    assert A._hcaptcha_parse_points(
        '{"points":[{"x":10,"y":210},{"x":140,"y":230}]}', box, 2) == []
    assert A._hcaptcha_parse_points("not json", box, 2) == []

    class _Tiles:
        def count(self):
            return 0

    class _Mouse:
        def __init__(self):
            self.clicks = []

        def click(self, x, y, delay=0):
            self.clicks.append((x, y, delay))

    class _Page2:
        def __init__(self):
            self.mouse = _Mouse()

    class _Frame2:
        def locator(self, sel):
            if sel == 'body':
                from types import SimpleNamespace
                return SimpleNamespace(screenshot=lambda **kw: b'context')
            return _Tiles()

    page = _Page2()
    orig_shot = A._hcaptcha_challenge_shot
    orig_spatial = A._hcaptcha_spatial_points
    orig_sleep = A.time.sleep
    orig_cache = A._HCAPTCHA_CACHE
    import tempfile
    from pathlib import Path
    A._HCAPTCHA_CACHE = Path(tempfile.mkdtemp())
    A._hcaptcha_challenge_shot = lambda frame: (b"\x89PNG", {"x": 400, "y": 500, "width": 200, "height": 180})
    A._hcaptcha_spatial_points = lambda png, shot_box, prompt, want, exact=True, context_png=None: [(410, 520), (550, 640)]
    A.time.sleep = lambda *a, **k: None
    try:
        clicked = A._hcaptcha_click_outliers(page, _Frame2(), "Choose 2 arrows that are outliers")
    finally:
        A._hcaptcha_challenge_shot = orig_shot
        A._hcaptcha_spatial_points = orig_spatial
        A.time.sleep = orig_sleep
        A._HCAPTCHA_CACHE = orig_cache
    assert clicked == 2
    assert page.mouse.clicks == [(410, 520, 180), (550, 640, 180)]
    assert "box[\"x\"] + x *" not in inspect.getsource(A._hcaptcha_click_outliers)

    # QIN2DIM ImageBinaryChallenge: box_2d is [row, col]. 100/900 bins to [0, 2].
    assert A._hcaptcha_parse_cells(
        '{"challenge_prompt":"garden","coordinates":[{"box_2d":[0,0]},{"box_2d":[2,2]}]}'
    ) == [0, 8]
    assert A._hcaptcha_parse_cells('{"coordinates":[{"box_2d":[100,900]}]}') == [2]
    assert A._hcaptcha_parse_cells('{"coordinates":[{"box_2d":[1,1]},{"box_2d":[1,1]}]}') == [4]
    assert A._hcaptcha_parse_cells("not json") == []
    assert A._hcaptcha_parse_cells('{"coordinates":[]}') == []
    drag_box = {"x": 10, "y": 20, "width": 100, "height": 80}
    assert A._hcaptcha_parse_paths(
        '{"paths":[{"start_point":{"x":20,"y":30},"end_point":{"x":80,"y":70}}]}', drag_box
    ) == [(20, 30, 80, 70)]
    assert A._hcaptcha_parse_paths(
        '{"paths":[{"start_point":{"x":1,"y":30},"end_point":{"x":80,"y":70}}]}', drag_box
    ) == []
    far = {"x": 700, "y": 200, "width": 500, "height": 400}
    assert A._hcaptcha_parse_paths(
        '{"paths":[{"start_point":{"x":0.8,"y":0.25},"end_point":{"x":0.3,"y":0.6}}]}', far
    ) == [(1100, 300, 850, 440)]
    assert A._hcaptcha_parse_paths(
        '{"drag_from":[80, 25],"drag_to":[30, 60]}', far
    ) == [(1100, 300, 850, 440)]

    class _Cell:
        def __init__(self, i):
            self.i = i

        def bounding_box(self):
            return {"x": 1, "y": 1, "width": 40, "height": 40}

    class _Nine:
        def count(self):
            return 9

        def nth(self, i):
            return _Cell(i)

    class _Submit:
        def __init__(self, page):
            self.page = page
            self.i = "submit"

        @property
        def first(self):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def bounding_box(self):
            return {"x": 2, "y": 2, "width": 40, "height": 40}

        def click(self, timeout=0):
            self.page.frames = []

    class _Frame9:
        def __init__(self, page, prompt):
            self.page = page
            self.url = (
                "https://newassets.hcaptcha.com/captcha/v1/a/static/hcaptcha.html"
                "#frame=challenge&sitekey=abc"
            )
            self.prompt = prompt
            self.tiles = _Nine()
            self.button = _Submit(page)

        def locator(self, sel):
            if "prompt" in sel:
                return _Prompt(self.prompt)
            if "button-submit" in sel:
                return self.button
            return self.tiles

        def evaluate(self, script):
            return {}

    class _Page9:
        def __init__(self, prompt):
            self.frames = [_Frame9(self, prompt)]

        def evaluate(self, script, arg=None):
            return not self.frames

    clicks = []

    def _click(page, loc):
        clicks.append(getattr(loc, "i", None))
        fn = getattr(loc, "click", None)
        if callable(fn):
            fn()

    orig_vision = A.llm_vision
    orig_shot = A._hcaptcha_challenge_shot
    orig_click = A._hcaptcha_click
    orig_sleep = A.time.sleep
    orig_alias = A._hcaptcha_alias
    orig_cache = A._HCAPTCHA_CACHE
    orig_ready = A._hcaptcha_wait_ready
    A._hcaptcha_wait_ready = lambda *a, **kw: True
    A._HCAPTCHA_CACHE = Path(tempfile.mkdtemp())
    A._hcaptcha_alias = lambda: {}
    A._hcaptcha_challenge_shot = lambda frame: (b"\x89PNG\r\n", {"x": 0, "y": 0, "width": 300, "height": 300})
    A._hcaptcha_click = _click
    A.time.sleep = lambda *a, **k: None
    A.llm_vision = lambda *a, **k: (
        '{"challenge_prompt":"garden","coordinates":[{"box_2d":[0,0]},{"box_2d":[2,2]}]}'
    )
    try:
        garden = _Page9("Select the tool used in a garden")
        assert A._solve_hcaptcha_local(garden) is True
        assert clicks == [0, 8, "submit"]
        assert garden.frames == []
        clicks.clear()
        A.llm_vision = lambda *a, **k: "not a grid"
        missed = _Page9("Select the tool used in a garden")
        assert A._solve_hcaptcha_local(missed) is False
        assert clicks == []
        assert missed.frames
    finally:
        A.llm_vision = orig_vision
        A._hcaptcha_challenge_shot = orig_shot
        A._hcaptcha_click = orig_click
        A.time.sleep = orig_sleep
        A._hcaptcha_alias = orig_alias
        A._HCAPTCHA_CACHE = orig_cache
        A._hcaptcha_wait_ready = orig_ready
    assert A._hcaptcha_is_pair_drag("Drag one of the icons to complete the circle")
    assert not A._hcaptcha_is_missing_piece("Drag one of the icons to complete the circle")
    assert A._hcaptcha_is_missing_piece("Please drag the missing piece")
    assert A._hcaptcha_is_missing_piece("Drag the piece to complete the image")
    assert not A._hcaptcha_is_missing_piece("Drag the element on the right to the shape that is most similar")

    import cv2
    import numpy as np
    slot = cv2.cvtColor(np.uint8([[[110, 200, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    synthetic = np.full((300, 400, 3), 245, np.uint8)
    cv2.ellipse(synthetic, (90, 150), (40, 28), 0, 0, 360, slot.tolist(), -1)
    cv2.ellipse(synthetic, (320, 160), (40, 28), 0, 0, 360, slot.tolist(), -1)
    hit = A._hcaptcha_missing_piece_px(synthetic)
    assert hit is not None, "cyan slot and piece should match"
    sx, sy, ex, ey = hit
    assert abs(sx - 320) < 30 and abs(sy - 160) < 30, hit
    assert abs(ex - 90) < 30 and abs(ey - 150) < 30, hit
    assert A._hcaptcha_missing_piece_px(np.full((80, 80, 3), 240, np.uint8)) is None
    saved = Path("autopilot/cache/hcaptcha/last-drag.png")
    if saved.exists():
        live = cv2.imread(str(saved))
        pair = A._hcaptcha_pair_drag_px(live)
        print("saved circle pair-drag:", pair)
        if pair and not A._hcaptcha_target_on_puzzle(live, pair[2], pair[3]):
            pair = None
        assert pair is None
    print("captcha solver self-check OK")


def test_turnstile_solver():
    class MockFrame:
        url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile"
        def locator(self, sel):
            class Loc:
                def count(self): return 1
                def is_visible(self): return True
                def bounding_box(self): return {"x": 10, "y": 10, "width": 100, "height": 30}
                def click(self, **kwargs): pass
            return Loc()

    class MockPage:
        frames = [MockFrame()]
        def locator(self, sel):
            class Loc:
                def count(self): return 1
                @property
                def first(self): return self
                def input_value(self): return "0.4AAABBBCCC111222333444555"
            return Loc()

    assert A.try_solve_turnstile(MockPage()) is True
    print("turnstile solver unit-test OK")

if __name__ == "__main__":
    test_turnstile_solver()
    demo()
