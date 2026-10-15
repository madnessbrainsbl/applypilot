"""Stealth Mouse & Human Interaction Engine for Playwright.

Implements cubic Bézier curve trajectories, Fitts's Law acceleration profiles,
randomized target offsets, and micro-delays to evade anti-bot detection
(Cloudflare Turnstile, DataDome, Ashby Anti-Abuse).
"""
import math
import random
import time

_CURRENT_POS = {"x": 100.0, "y": 100.0}

def _bezier_point(p0, p1, p2, p3, t):
    """Calculates a point on a cubic Bézier curve at parameter t in [0, 1]."""
    u = 1.0 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t
    x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
    return x, y

def generate_bezier_trajectory(start_x, start_y, end_x, end_y, steps=25):
    """Generates a human-like curved path from (start_x, start_y) to (end_x, end_y)."""
    dx = end_x - start_x
    dy = end_y - start_y
    dist = math.hypot(dx, dy)
    if dist < 5:
        return [(end_x, end_y)]

    # Normalized perpendicular vector
    nx = -dy / dist
    ny = dx / dist

    # Random arc magnitude (proportional to distance)
    arc = random.uniform(-0.25, 0.25) * dist

    # Control points P1 and P2
    p0 = (start_x, start_y)
    p1 = (start_x + dx * 0.3 + nx * arc + random.uniform(-5, 5),
          start_y + dy * 0.3 + ny * arc + random.uniform(-5, 5))
    p2 = (start_x + dx * 0.7 + nx * arc * 0.8 + random.uniform(-5, 5),
          start_y + dy * 0.7 + ny * arc * 0.8 + random.uniform(-5, 5))
    p3 = (end_x, end_y)

    points = []
    for i in range(1, steps + 1):
        # Ease-in-out cosine easing (Fitts's Law approximation)
        linear_t = i / steps
        eased_t = (1.0 - math.cos(linear_t * math.pi)) / 2.0
        points.append(_bezier_point(p0, p1, p2, p3, eased_t))
    return points

def human_move(page, target_x, target_y, steps=None):
    """Moves mouse along a natural Bézier curve to target coordinates."""
    global _CURRENT_POS
    start_x = _CURRENT_POS["x"]
    start_y = _CURRENT_POS["y"]

    dist = math.hypot(target_x - start_x, target_y - start_y)
    if steps is None:
        steps = max(12, min(40, int(dist / 20)))

    trajectory = generate_bezier_trajectory(start_x, start_y, target_x, target_y, steps=steps)
    for x, y in trajectory:
        try:
            page.mouse.move(x, y)
        except Exception:
            break
        # Small micro-delay for realistic frame rendering
        time.sleep(random.uniform(0.005, 0.015))

    _CURRENT_POS["x"] = target_x
    _CURRENT_POS["y"] = target_y

def human_click(page, locator_or_element, *, cancelled=None):
    """Moves to element via Bézier curve, pauses naturally, and clicks."""
    if cancelled and cancelled():
        return False
    try:
        locator_or_element.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    try:
        box = locator_or_element.bounding_box()
    except Exception:
        box = None

    if not box:
        if cancelled and cancelled():
            return False
        try:
            locator_or_element.click()
        except Exception:
            pass
        return

    # Random offset within element (avoid exact geometric center)
    offset_x = random.uniform(0.3, 0.7) * box["width"]
    offset_y = random.uniform(0.3, 0.7) * box["height"]
    target_x = box["x"] + offset_x
    target_y = box["y"] + offset_y

    human_move(page, target_x, target_y)
    # Human hesitation before click
    time.sleep(random.uniform(0.06, 0.15))
    if cancelled:
        if cancelled():
            return False
        page.mouse.click(target_x, target_y)
        return True
    try:
        page.mouse.down()
        time.sleep(random.uniform(0.04, 0.09))
        page.mouse.up()
    except Exception:
        try:
            locator_or_element.click(timeout=2000)
        except Exception:
            pass

def human_click_box(page, box):
    """Moves mouse to a bounding box along a natural Bézier curve, hesitates, and clicks."""
    target_x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
    target_y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    human_move(page, target_x, target_y)
    time.sleep(random.uniform(0.12, 0.28))
    try:
        page.mouse.down()
        time.sleep(random.uniform(0.05, 0.11))
        page.mouse.up()
    except Exception:
        pass

def human_type(element, text, min_delay=0.015, max_delay=0.045):
    """Types text character-by-character with natural human typing jitter."""
    try:
        cur_val = (element.input_value() or "").strip()
        if cur_val == text.strip():
            return
    except Exception:
        pass
    try:
        element.click(timeout=1500)
    except Exception:
        pass
    try:
        element.fill("")
    except Exception:
        pass
    try:
        avg_delay = int(random.uniform(min_delay, max_delay) * 1000)
        element.press_sequentially(text, delay=avg_delay)
        return
    except Exception:
        pass
    for char in text:
        try:
            element.type(char)
        except Exception:
            try:
                element.fill(text)
                return
            except Exception:
                return
        delay = random.uniform(min_delay, max_delay)
        if char in (" ", ".", ",", "-", "\n", "@"):
            delay += random.uniform(0.02, 0.05)
        time.sleep(delay)
