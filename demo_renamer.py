# -*- coding: utf-8 -*-
import bf2
import host
import os
import re
import time
import threading

DEMO_DIR = "mods/pr/demos/"
TEMP_DIRS = ("temp/", "mods/pr/temp/")
DATE_SKEW = 5.0
PLAY_DELAY = 5.0
RETRIES = 5
RETRY_WAIT = 1.0

TRACKER_RE = re.compile(
    r'^tracker_(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})_(.+?)_(gpm_[a-z0-9_]+)_(\d+)\.PRdemo(?:\.incomplete)?$',
    re.I
)

_lock = threading.Lock()
_round_started = 0.0


def _safe(name, fallback="unknown"):
    name = str(name).strip().lower()
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, '')
    name = '_'.join(name.replace(' ', '_').split('_')).strip('._')
    return name or fallback


def _parse_tracker(filename):
    m = TRACKER_RE.match(os.path.basename(str(filename)))
    if not m:
        return None
    return {
        "datetime": m.group(1),
        "mapname": m.group(2),
        "gamemode": m.group(3),
        "layer": m.group(4),
    }


def _demo_name(info):
    return "demo_%s_%s_%s_%s.bf2demo" % (
        info["datetime"],
        _safe(info["mapname"], "unknown_map"),
        _safe(info["gamemode"], "gpm_cq"),
        _safe(info["layer"], "64"),
    )


def _to_epoch(dt):
    try:
        return time.mktime(time.strptime(str(dt), "%Y_%m_%d_%H_%M_%S"))
    except Exception:
        return None


def _list_trackers():
    out = []
    for folder in TEMP_DIRS:
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except Exception:
            continue
        for name in names:
            if not (name.startswith("tracker_") and name.lower().endswith(".prdemo.incomplete")):
                continue
            info = _parse_tracker(name)
            if not info:
                continue
            path = os.path.join(folder, name)
            try:
                info["mtime"] = os.path.getmtime(path)
            except Exception:
                continue
            info["epoch"] = _to_epoch(info["datetime"])
            out.append(info)
    return out


def _find_tracker(since=0.0):
    items = _list_trackers()
    if since:
        matched = []
        for c in items:
            epoch = c.get("epoch")
            if epoch is not None and abs(epoch - since) <= DATE_SKEW:
                matched.append(c)
            elif c["mtime"] >= since - DATE_SKEW:
                matched.append(c)
        items = matched
    if not items:
        return None
    return max(items, key=lambda c: c["mtime"])


def _latest_auto_demo():
    try:
        files = [
            os.path.join(DEMO_DIR, f) for f in os.listdir(DEMO_DIR)
            if f.startswith("auto_") and f.endswith(".bf2demo")
        ]
        return max(files, key=os.path.getmtime) if files else None
    except Exception:
        return None


def _unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for i in range(1, 100):
        cand = "%s_%d%s" % (base, i, ext)
        if not os.path.exists(cand):
            return cand
    return None


def _retry_rename(src, dst):
    for _ in range(RETRIES):
        try:
            with open(src, 'r+'):
                pass
            os.rename(src, dst)
            return True
        except Exception:
            time.sleep(RETRY_WAIT)
    return False


def _rename_after_play(started):
    time.sleep(PLAY_DELAY)

    with _lock:
        if _round_started != started:
            return

    tracker = _find_tracker(since=started) or _find_tracker()
    if not tracker:
        return

    name = _demo_name(tracker)
    src = None
    for _ in range(RETRIES):
        src = _latest_auto_demo()
        if src and os.path.exists(src):
            break
        time.sleep(RETRY_WAIT)

    if not src:
        return

    with _lock:
        if _round_started != started:
            return

    dst = _unique_path(os.path.join(DEMO_DIR, name))
    if dst:
        _retry_rename(src, dst)


def init():
    try:
        host.registerGameStatusHandler(onGameStatusChanged)
    except Exception:
        pass


def deinit():
    try:
        host.unregisterGameStatusHandler(onGameStatusChanged)
    except Exception:
        pass


def onGameStatusChanged(status):
    global _round_started

    if status == bf2.GameStatus.Playing:
        started = time.time()
        with _lock:
            _round_started = started
        t = threading.Thread(target=_rename_after_play, args=(started,))
        t.daemon = True
        t.start()
