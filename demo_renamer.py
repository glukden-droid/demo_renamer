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
DEMO_WRITE_DELAY = 240.0
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

    datetime_text = "_".join(parts[:6])
    try:
        epoch = time.mktime(time.strptime(datetime_text, DATE_FMT))
    except Exception:
        return None

    return {
        "datetime": datetime_text,
        "mapname": "_".join(middle[:gpm_index]),
        "gamemode": "_".join(middle[gpm_index:]),
        "layer": parts[-1],
        "epoch": epoch,
        "filename": name,
    }


def _demo_name_from_tracker(tracker):
    return "demo_%s_%s_%s_%s.bf2demo" % (
        tracker["datetime"],
        _safe(tracker["mapname"], "unknown_map"),
        _safe(tracker["gamemode"], "gpm_cq"),
        _safe(tracker["layer"], "64"),
    )


def _find_tracker(started):
    if not os.path.isdir(TEMP_DIR):
        return None

    best = None
    best_mtime = 0.0

    try:
        names = os.listdir(TEMP_DIR)
    except Exception:
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

        # Keep tracker of this round: date near Playing, or fresh file
        if abs(info["epoch"] - started) > TRACKER_SKEW and mtime < started - TRACKER_SKEW:
            continue

        info["path"] = path
        info["mtime"] = mtime
        if best is None or mtime > best_mtime:
            best = info
            best_mtime = mtime

    return best


def _parse_demo_epoch(name):
    """
    Date from auto demo name, e.g.:
    auto_2026_08_11_19_04_35.bf2demo
    """
    name = os.path.basename(str(name)).strip()
    lower = name.lower()
    if not lower.startswith(AUTO_PREFIX) or not lower.endswith(DEMO_SUFFIX):
        return None

    body = name[len(AUTO_PREFIX):len(name) - len(DEMO_SUFFIX)]
    parts = body.split("_")
    if len(parts) < 6 or not all(part.isdigit() for part in parts[:6]):
        return None

    try:
        return time.mktime(time.strptime("_".join(parts[:6]), DATE_FMT))
    except Exception:
        return None


def _find_demo_for_tracker(tracker):
    """
    Pick auto_ demo whose filename date matches tracker date + write delay.
    |date_demo - date_tracker - DEMO_WRITE_DELAY| <= TRACKER_SKEW
    """
    best = None
    best_delta = None

    try:
        names = os.listdir(DEMO_DIR)
    except Exception:
        return None

    for name in names:
        if not (name.startswith(AUTO_PREFIX) and name.endswith(DEMO_SUFFIX)):
            continue
        demo_epoch = _parse_demo_epoch(name)
        if demo_epoch is None:
            continue
        delta = abs(demo_epoch - tracker["epoch"] - DEMO_WRITE_DELAY)
        if delta > TRACKER_SKEW:
            continue
        if best is None or delta < best_delta:
            best = os.path.join(DEMO_DIR, name)
            best_delta = delta

    return best


def _unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for index in range(1, 100):
        candidate = "%s_%d%s" % (base, index, ext)
        if not os.path.exists(candidate):
            return candidate
    return None


def _retry(started, attempt):
    if _rtimer and attempt + 1 < RETRIES and started == _round_started:
        _rtimer.fireOnce(_rename_step, RETRY_WAIT, (started, attempt + 1))


def _schedule_rename():
    global _round_started
    if not _rtimer:
        return
    _round_started = time.time()
    _rtimer.fireOnce(_rename_step, PLAY_DELAY, (_round_started, 0))


def _rename_step(data=None):
    if not isinstance(data, tuple) or len(data) != 2:
        return

    started, attempt = data
    if started != _round_started:
        return

    # 1) tracker from temp/
    tracker = _find_tracker(started)
    if not tracker:
        _retry(started, attempt)
        return

    # 2) name from tracker
    target_name = _demo_name_from_tracker(tracker)

    # 3) auto_*.bf2demo matching this tracker
    demo = _find_demo_for_tracker(tracker)
    if not demo:
        _retry(started, attempt)
        return

    # 4) rename
    target_path = _unique_path(os.path.join(DEMO_DIR, target_name))
    if not target_path:
        return

    try:
        with open(demo, 'r+'):
            pass
        os.rename(demo, target_path)
        _log("renamed to " + os.path.basename(target_path))
    except Exception:
        _retry(started, attempt)


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
