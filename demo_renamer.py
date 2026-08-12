# -*- coding: utf-8 -*-
import bf2
import host
import os
import time

DEMO_DIR = "mods/pr/demos/"
TEMP_DIR = "temp/"
TRACKER_PREFIX = "tracker_"
TRACKER_SUFFIX = ".prdemo.incomplete"
AUTO_PREFIX = "auto_"
DEMO_SUFFIX = ".bf2demo"
DATE_FMT = "%Y_%m_%d_%H_%M_%S"

# Demo file appears ~4 minutes after Playing on this server
PLAY_DELAY = 245.0
RETRY_WAIT = 10.0
RETRIES = 5
TRACKER_SKEW = 5.0

_round_started = 0.0
_rtimer = None


def _log(message):
    try:
        host.rcon_invoke('echo "DemoRenamer: %s"' % str(message))
    except Exception:
        pass


def _safe(value, fallback):
    value = str(value).strip().lower()
    for ch in '/\\:*?"<>|':
        value = value.replace(ch, '')
    value = '_'.join(value.replace(' ', '_').split('_')).strip('._')
    return value or fallback


def _parse_tracker(name):
    name = os.path.basename(str(name)).strip()
    lower = name.lower()
    if not lower.startswith(TRACKER_PREFIX) or not lower.endswith(TRACKER_SUFFIX):
        return None

    body = name[len(TRACKER_PREFIX):len(name) - len(TRACKER_SUFFIX)]
    parts = body.split("_")
    if len(parts) < 10 or not all(part.isdigit() for part in parts[:6]) or not parts[-1].isdigit():
        return None

    middle = parts[6:-1]
    if "gpm" not in middle:
        return None

    gpm_index = middle.index("gpm")
    if gpm_index == 0:
        return None

    mapname = "_".join(middle[:gpm_index])
    gamemode = "gpm"
    if gpm_index + 1 < len(middle):
        gamemode = "gpm_" + middle[gpm_index + 1]

    return {
        "datetime": "_".join(parts[:6]),
        "mapname": mapname,
        "gamemode": gamemode,
        "layer": parts[-1],
    }


def _to_epoch(text):
    try:
        return time.mktime(time.strptime(str(text), DATE_FMT))
    except Exception:
        return None


def _find_tracker(started):
    if not os.path.isdir(TEMP_DIR):
        _log("temp dir missing")
        return None

    best = None
    best_mtime = 0.0

    try:
        names = os.listdir(TEMP_DIR)
    except Exception:
        _log("temp dir unreadable")
        return None

    for name in names:
        info = _parse_tracker(name)
        if not info:
            continue

        path = os.path.join(TEMP_DIR, name)
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            continue

        epoch = _to_epoch(info["datetime"])
        if epoch is not None and abs(epoch - started) > TRACKER_SKEW and mtime < started - TRACKER_SKEW:
            continue

        if best is None or mtime > best_mtime:
            best = info
            best_mtime = mtime

    return best


def _find_demo(started):
    try:
        files = [
            os.path.join(DEMO_DIR, name) for name in os.listdir(DEMO_DIR)
            if name.startswith(AUTO_PREFIX) and name.endswith(DEMO_SUFFIX)
        ]
    except Exception:
        return None

    candidates = []
    for path in files:
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            continue
        if mtime >= started - 10.0:
            candidates.append(path)

    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for index in range(1, 100):
        candidate = "%s_%d%s" % (base, index, ext)
        if not os.path.exists(candidate):
            return candidate
    return None


def _schedule_rename():
    global _round_started
    if not _rtimer:
        _log("rtimer missing")
        return
    _round_started = time.time()
    _log("trigger Playing")
    _rtimer.fireOnce(_rename_step, PLAY_DELAY, (_round_started, 0))


def _rename_step(data=None):
    if not isinstance(data, tuple) or len(data) != 2:
        return

    started, attempt = data
    if started != _round_started:
        return

    tracker = _find_tracker(started)
    demo = _find_demo(started)
    if not tracker or not demo:
        if not tracker:
            _log("tracker not found")
        if not demo:
            _log("demo not found")
        if _rtimer and attempt + 1 < RETRIES:
            _rtimer.fireOnce(_rename_step, RETRY_WAIT, (started, attempt + 1))
        return

    target_name = "demo_%s_%s_%s_%s.bf2demo" % (
        tracker["datetime"],
        _safe(tracker["mapname"], "unknown_map"),
        _safe(tracker["gamemode"], "gpm_cq"),
        _safe(tracker["layer"], "64"),
    )
    target_path = _unique_path(os.path.join(DEMO_DIR, target_name))
    if not target_path:
        return

    try:
        with open(demo, 'r+'):
            pass
        os.rename(demo, target_path)
        _log("renamed to " + os.path.basename(target_path))
    except Exception:
        _log("rename retry")
        if _rtimer and attempt + 1 < RETRIES and started == _round_started:
            _rtimer.fireOnce(_rename_step, RETRY_WAIT, (started, attempt + 1))


def onGameStatusChanged(status):
    if status == bf2.GameStatus.Playing:
        _schedule_rename()


def init():
    global _rtimer
    try:
        import game.realitytimer as rtimer
        _rtimer = rtimer
    except Exception as e:
        _log("rtimer import failed: " + str(e))
        return

    try:
        host.registerGameStatusHandler(onGameStatusChanged)
        _log("initialized")
    except Exception as e:
        _log("register failed: " + str(e))


def deinit():
    try:
        host.unregisterGameStatusHandler(onGameStatusChanged)
    except Exception:
        pass
