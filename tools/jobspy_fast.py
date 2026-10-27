"""Plumbing for a fast `discover --source jobspy`: disk caches with a TTL, per-site
concurrency caps with 429 back-off, and a market rotation kept in autopilot/cache.
No project imports, so it tests without network (tests/test_jobspy_fast.py)."""
import json
import logging
import random
import re
import threading
import time
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "autopilot" / "cache"
_FILE_LOCK = threading.Lock()


class DiskCache:
    """{key: value} in one json file, each entry with its own expiry. Thread-safe;
    flush() writes only when something changed."""

    def __init__(self, path, ttl_s):
        self.path, self.ttl_s, self.lock, self.dirty = Path(path), ttl_s, threading.Lock(), False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        now = time.time()
        self.data = {k: e for k, e in raw.items() if isinstance(e, list) and len(e) == 2 and e[0] > now}

    def get(self, key, default=None):
        with self.lock:
            e = self.data.get(key)
        return e[1] if e and e[0] > time.time() else default

    def put(self, key, value, ttl_s=None):
        with self.lock:
            self.data[key] = [time.time() + (self.ttl_s if ttl_s is None else ttl_s), value]
            self.dirty = True

    def flush(self):
        with self.lock:
            if not self.dirty:
                return
            snapshot = json.dumps(self.data, ensure_ascii=False)
            self.dirty = False
        with _FILE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(snapshot, encoding="utf-8")
            tmp.replace(self.path)


class SiteGate:
    """At most caps[site] scrapes of one site at a time; after a 429 the site cools off
    (20 s, 40 s, ... capped at 2 min) and is switched off for the run after `max_hits`."""

    def __init__(self, caps=None, default_cap=3, max_hits=3, base_cool_s=20.0):
        self.caps, self.default_cap, self.max_hits, self.base_cool_s = caps or {}, default_cap, max_hits, base_cool_s
        self.sems, self.hits, self.cool_until, self.lock = {}, {}, {}, threading.Lock()

    def sem(self, site):
        with self.lock:
            if site not in self.sems:
                self.sems[site] = threading.BoundedSemaphore(max(1, int(self.caps.get(site, self.default_cap))))
            return self.sems[site]

    def paused(self, site):
        return self.hits.get(site, 0) >= self.max_hits

    def pause(self, site):
        self.hits[site] = self.max_hits

    def hit(self, site):
        """A 429 was seen on `site`. Returns the seconds the caller should wait before retrying.
        Tasks that ran side by side see the same burst: it counts once while the site cools."""
        with self.lock:
            left = self.cool_until.get(site, 0.0) - time.time()
            if left > 0:
                return left
            n = self.hits[site] = self.hits.get(site, 0) + 1
            wait = min(self.base_cool_s * 2 ** (n - 1), 120.0)
            self.cool_until[site] = max(self.cool_until.get(site, 0.0), time.time() + wait)
        return wait

    def wait_cool(self, site):
        delay = self.cool_until.get(site, 0.0) - time.time()
        if delay > 0:
            time.sleep(delay + random.uniform(0, 2))


class RateLimitWatch(logging.Handler):
    """jobspy's scrapers swallow HTTP errors and just log them ("429 Response", "too many
    429 error responses"), so a blocked site returns an empty frame. Counting those log
    lines per site is the only way to tell "no postings" from "blocked"."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.counts, self._lock = {}, threading.Lock()

    def emit(self, record):
        if "429" in record.getMessage():
            site = record.name.split(":")[-1].lower()
            with self._lock:
                self.counts[site] = self.counts.get(site, 0) + 1

    def count(self, site):
        return self.counts.get(site, 0)

    def install(self):
        for name in list(logging.root.manager.loggerDict):
            if name.startswith("JobSpy:") and self not in logging.getLogger(name).handlers:
                logging.getLogger(name).addHandler(self)
        return self


def pick_markets(locations, n, persona, path=None, legacy=None):
    """The next `n` markets after the saved cursor, wrapping around, plus commit() that
    stores the new cursor. Two runs in a row never cover the same markets (unless there
    are no more than `n`). `legacy` is the old per-persona cursor file, read once."""
    path = Path(path or CACHE_DIR / "jobspy_markets.json")
    total = len(locations)
    if not total or n <= 0:
        return [], lambda: None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    cursor = (state.get(persona) or {}).get("cursor")
    if cursor is None and legacy:
        try:
            cursor = json.loads(Path(legacy).read_text(encoding="utf-8"))["cursor"]
        except (OSError, ValueError, KeyError, TypeError):
            cursor = 0
    cursor = int(cursor or 0) % total
    count = min(total, n)
    window = [locations[(cursor + i) % total] for i in range(count)]

    def commit():
        with _FILE_LOCK:
            try:
                fresh = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                fresh = {}
            fresh[persona] = {"cursor": (cursor + count) % total, "total": total, "updated": int(time.time())}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(fresh, ensure_ascii=False, indent=1), encoding="utf-8")

    return window, commit


def window_hours(last_ok_ts, base_hours, now=None, floor_h=24, slack=1.5, pad_h=12):
    """hours_old for a re-scan: only what appeared since the last complete scan of the same
    (site, market, query), padded; the full `base_hours` when it was never scanned."""
    if not last_ok_ts:
        return base_hours
    since_h = ((now or time.time()) - last_ok_ts) / 3600
    return int(min(base_hours, max(floor_h, since_h * slack + pad_h)))


def interleave(groups):
    """Round-robin over lists, so a pool never queues one site's tasks back to back."""
    out, groups = [], [list(g) for g in groups]
    while any(groups):
        for g in groups:
            if g:
                out.append(g.pop(0))
    return out


_LI_ID = re.compile(r"/jobs/view/(?:[^/?]*-)?(\d{6,})")


def linkedin_id(url):
    m = _LI_ID.search(url or "")
    return m.group(1) if m else None
