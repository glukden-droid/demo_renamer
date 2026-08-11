# -*- coding: utf-8 -*-
import bf2
import host
import os
import re
import time
import threading

# Configuration (Python 2.7.12 / pylib compatible)
DEMO_DIR = "mods/pr/demos/"
# RealityTracker incomplete (temp) + finished (demos); mod and game-root paths
TRACKER_SEARCH_DIRS = "temp/"
DEMO_PREFIX = "demo"
INITIAL_DELAY = 3.0
LOCK_RETRIES = 5
LOCK_WAIT = 1.0
FIND_RETRIES = 3
FIND_WAIT = 1.0
RENAME_RETRIES = 5
RENAME_WAIT = 1.0

# tracker_2026_08_11_19_04_35_adak_gpm_cq_64.PRdemo.incomplete
TRACKER_NAME_RE = re.compile(
    r'^tracker_'
    r'(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})_'
    r'(.+?)_'
    r'(gpm_[a-z0-9_]+)_'
    r'(\d+)'
    r'\.PRdemo(?:\.incomplete)?$',
    re.IGNORECASE
)

# Global variables
map_start_time = "0000_00_00_00_00_00"
_main_thread_id = None
_pending_jobs = []
_worker_running = False
_state_lock = threading.Lock()
_log_queue = []


def consoleMessage(msg):
    # RCon is only safe on the main/game thread.
    # Worker threads enqueue messages; drain_log_queue() flushes them later.
    text = str(msg)
    try:
        if _main_thread_id is not None and threading.current_thread().ident != _main_thread_id:
            with _state_lock:
                _log_queue.append(text)
            return
        _echo(text)
    except Exception:
        pass


def _echo(msg):
    try:
        safe_msg = str(msg).replace('\\', '\\\\').replace('"', "'")
        host.rcon_invoke('echo "%s"' % safe_msg)
    except Exception:
        pass


def drain_log_queue():
    with _state_lock:
        messages = list(_log_queue)
        del _log_queue[:]
    for msg in messages:
        _echo(msg)


def sanitize_filename_part(value, fallback="unknown"):
    value = str(value).strip().lower()
    for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
        value = value.replace(ch, '')
    value = value.replace(' ', '_')
    while '__' in value:
        value = value.replace('__', '_')
    value = value.strip('._')
    if not value:
        return fallback
    return value


def parse_tracker_filename(filename):
    """
    Parse RealityTracker name:
    tracker_2026_08_11_19_04_35_adak_gpm_cq_64.PRdemo.incomplete
    -> datetime, mapname, gamemode, layer
    """
    name = os.path.basename(str(filename))
    match = TRACKER_NAME_RE.match(name)
    if not match:
        return None
    return {
        "datetime": match.group(1),
        "mapname": match.group(2),
        "gamemode": match.group(3),
        "layer": match.group(4),
        "filename": name,
    }


def _list_tracker_files(folder):
    if not os.path.isdir(folder):
        return []
    results = []
    for name in os.listdir(folder):
        lower = name.lower()
        if not name.startswith("tracker_"):
            continue
        if not (lower.endswith(".prdemo.incomplete") or lower.endswith(".prdemo")):
            continue
        info = parse_tracker_filename(name)
        if not info:
            continue
        path = os.path.join(folder, name)
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            mtime = 0
        info["path"] = path
        info["mtime"] = mtime
        results.append(info)
    return results


def find_tracker_info(preferred_datetime=None):
    """
    Find tracker file in temp/ (incomplete) or demos/ (finished).
    Prefer datetime match to round start; otherwise newest by mtime.
    """
    try:
        candidates = []
        seen = set()
        for folder in TRACKER_SEARCH_DIRS:
            for info in _list_tracker_files(folder):
                key = info["filename"].lower()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(info)

        if not candidates:
            consoleMessage("Demo Renamer: no tracker files in temp/demos.")
            return None

        if preferred_datetime:
            matched = [c for c in candidates if c["datetime"] == preferred_datetime]
            if matched:
                best = max(matched, key=lambda c: c["mtime"])
                consoleMessage(
                    "Demo Renamer: tracker date match -> %s" % best["filename"]
                )
                return best
            consoleMessage(
                "Demo Renamer: no tracker with date %s, falling back to latest." %
                preferred_datetime
            )

        best = max(candidates, key=lambda c: c["mtime"])
        consoleMessage("Demo Renamer: latest tracker -> %s" % best["filename"])
        return best
    except Exception as e:
        consoleMessage("Demo Renamer find_tracker_info error: " + str(e))
        return None


def find_latest_auto_demo():
    try:
        if not os.path.isdir(DEMO_DIR):
            consoleMessage("Demo Renamer: DEMO_DIR missing -> %s" % DEMO_DIR)
            return None
        demo_files = [
            f for f in os.listdir(DEMO_DIR)
            if f.startswith("auto_") and f.endswith(".bf2demo")
        ]
        if not demo_files:
            return None
        return max(
            [os.path.join(DEMO_DIR, f) for f in demo_files],
            key=os.path.getmtime
        )
    except Exception as e:
        consoleMessage("Demo Renamer find_latest_auto_demo error: " + str(e))
        return None


def resolve_unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for i in range(1, 100):
        candidate = "%s_%d%s" % (base, i, ext)
        if not os.path.exists(candidate):
            return candidate
    return None


def wait_for_file_unlock(file_path):
    for attempt in range(LOCK_RETRIES):
        try:
            with open(file_path, 'r+'):
                pass
            return True
        except IOError:
            consoleMessage(
                "Demo Renamer: File is locked by engine. Waiting %ds... (%d/%d)" %
                (LOCK_WAIT, attempt + 1, LOCK_RETRIES)
            )
            time.sleep(LOCK_WAIT)
        except Exception as lock_err:
            consoleMessage("Demo Renamer Lock Check Warning: " + str(lock_err))
            time.sleep(LOCK_WAIT)
    return False


def rename_with_retries(source_path, dest_path):
    last_err = None
    for attempt in range(RENAME_RETRIES):
        try:
            if not os.path.exists(source_path):
                return False, "source missing"
            os.rename(source_path, dest_path)
            return True, None
        except Exception as err:
            last_err = err
            consoleMessage(
                "Demo Renamer: rename failed (%d/%d): %s" %
                (attempt + 1, RENAME_RETRIES, str(err))
            )
            time.sleep(RENAME_WAIT)
    return False, last_err


def init():
    global _main_thread_id
    try:
        _main_thread_id = threading.current_thread().ident
        host.registerGameStatusHandler(onGameStatusChanged)
        consoleMessage("Demo Renamer: Plugin successfully initialized (Native Threading Mode).")
    except Exception as init_err:
        consoleMessage("Demo Renamer Critical Init Error: " + str(init_err))


def deinit():
    try:
        host.unregisterGameStatusHandler(onGameStatusChanged)
        consoleMessage("Demo Renamer: Plugin successfully deinitialized.")
    except Exception:
        pass


def onGameStatusChanged(status):
    global map_start_time

    drain_log_queue()

    if status == bf2.GameStatus.Playing:
        map_start_time = time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime())
        consoleMessage("Demo Renamer: Map start time captured -> %s" % map_start_time)

    elif status == bf2.GameStatus.EndGame:
        try:
            mapname = "unknown_map"
            gamemode = "gpm_cq"
            layer = "64"
            round_time = map_start_time

            consoleMessage("Safe map data collection via RCon")
            try:
                maplist_raw = host.rcon_invoke("maplist.list").strip().split('\n')
                maplist = [line.strip() for line in maplist_raw if line.strip()]

                currentlevel_str = host.rcon_invoke("admin.currentlevel").strip()
                consoleMessage("Demo Renamer: currentlevel_str -> %s" % currentlevel_str)
                if currentlevel_str.isdigit():
                    currentlevel = int(currentlevel_str)

                    if currentlevel < len(maplist):
                        map_data = maplist[currentlevel].split()
                        if len(map_data) >= 4:
                            mapname = map_data[1]
                            gamemode = map_data[2]
                            layer = map_data[3]
                            consoleMessage("Demo Renamer: mapname -> %s" % mapname)
                        else:
                            consoleMessage(
                                "Demo Renamer: unexpected maplist line -> %s" %
                                maplist[currentlevel]
                            )
                    else:
                        consoleMessage(
                            "Demo Renamer: currentlevel %d out of range (maplist size %d)" %
                            (currentlevel, len(maplist))
                        )
            except Exception as rcon_err:
                consoleMessage("Demo Renamer RCon Parsing Warning: " + str(rcon_err))

            # Prefer RealityTracker temp/demos filename date + map meta
            tracker = find_tracker_info(preferred_datetime=round_time)
            if tracker:
                if tracker["datetime"] != round_time:
                    consoleMessage(
                        "Demo Renamer: round time %s != tracker date %s; using tracker." %
                        (round_time, tracker["datetime"])
                    )
                round_time = tracker["datetime"]
                mapname = tracker["mapname"]
                gamemode = tracker["gamemode"]
                layer = tracker["layer"]
                consoleMessage(
                    "Demo Renamer: using tracker meta -> %s / %s / %s / %s" %
                    (round_time, mapname, gamemode, layer)
                )
            else:
                consoleMessage(
                    "Demo Renamer: tracker not found, using Playing time + RCon meta."
                )

            # gpm_ prefix is preserved intentionally (e.g. gpm_cq)
            localized_gamemode = sanitize_filename_part(gamemode, "gpm_cq")
            localized_layer = sanitize_filename_part(layer, "64")
            localized_mapname = sanitize_filename_part(mapname, "unknown_map")
            consoleMessage("Demo Renamer: localized_gamemode -> %s" % localized_gamemode)
            consoleMessage("Demo Renamer: localized_mapname -> %s" % localized_mapname)

            generated_name = "%s_%s_%s_%s_%s.bf2demo" % (
                DEMO_PREFIX,
                round_time,
                localized_mapname,
                localized_gamemode,
                localized_layer,
            )
            consoleMessage("Demo Renamer: generated_name -> %s" % generated_name)

            source_path = find_latest_auto_demo()
            if source_path:
                consoleMessage("Demo Renamer: source_path captured at EndGame -> %s" % source_path)
            else:
                consoleMessage("Demo Renamer: auto_ demo not found at EndGame, will retry in thread.")

            enqueue_rename_job(generated_name, source_path)

            consoleMessage("Demo Renamer: onGameStatusChanged block execution finished.")
        except Exception as e:
            consoleMessage("Demo Renamer Status Change Error: " + str(e))


def enqueue_rename_job(generated_name, source_path):
    """Queue rename work so rapid EndGame events are not dropped."""
    global _worker_running
    start_worker = False

    with _state_lock:
        _pending_jobs.append((generated_name, source_path))
        pending_count = len(_pending_jobs)
        if not _worker_running:
            _worker_running = True
            start_worker = True

    consoleMessage("Demo Renamer: Queued rename job (%d pending)." % pending_count)

    if start_worker:
        worker_thread = threading.Thread(target=_rename_worker)
        worker_thread.daemon = True
        worker_thread.start()
        consoleMessage("Demo Renamer: Launching rename worker thread...")
    else:
        consoleMessage("Demo Renamer: Worker already running; job will be processed from queue.")


def _rename_worker():
    global _worker_running
    try:
        while True:
            with _state_lock:
                if not _pending_jobs:
                    _worker_running = False
                    break
                generated_name, source_path = _pending_jobs.pop(0)

            onEndGame(generated_name, source_path)
    except Exception as worker_err:
        consoleMessage("Demo Renamer Worker Error: " + str(worker_err))
        with _state_lock:
            _worker_running = False


def onEndGame(generated_name=None, source_path=None):
    consoleMessage("Demo Renamer: onEndGame background job started")
    try:
        time.sleep(INITIAL_DELAY)

        if not generated_name:
            consoleMessage("Demo Renamer Error: No valid generated_name received in onEndGame.")
            return

        consoleMessage("Demo Renamer final state -> generated_name: " + str(generated_name))
        new_path = os.path.join(DEMO_DIR, generated_name)

        # Prefer the path captured at EndGame; only discover if it was missing then.
        # Never replace a known path with a newer auto_ file (avoids renaming next round).
        pinned_source = source_path
        target_source_file = pinned_source

        for attempt in range(FIND_RETRIES):
            if target_source_file and os.path.exists(target_source_file):
                break
            consoleMessage(
                "Demo Renamer: Waiting for auto_ demo file... (%d/%d)" %
                (attempt + 1, FIND_RETRIES)
            )
            time.sleep(FIND_WAIT)
            if pinned_source:
                continue
            target_source_file = find_latest_auto_demo()

        if not target_source_file or not os.path.exists(target_source_file):
            consoleMessage("Demo Renamer: auto_ demo file not found after retries.")
            return

        consoleMessage("Demo Renamer: Selected file for renaming -> %s" % target_source_file)

        if not wait_for_file_unlock(target_source_file):
            consoleMessage(
                "Demo Renamer Error: File %s remains locked. Rename aborted." %
                target_source_file
            )
            return

        unique_path = resolve_unique_path(new_path)
        if not unique_path:
            consoleMessage("Demo Renamer Error: Could not resolve unique filename.")
            return

        if unique_path != new_path:
            consoleMessage("Demo Renamer: Target exists, using -> %s" % os.path.basename(unique_path))

        ok, err = rename_with_retries(target_source_file, unique_path)
        if not ok:
            consoleMessage("Demo Renamer Error: Rename aborted after retries: %s" % str(err))
            return

        consoleMessage(
            "Demo Renamer: File successfully saved as: %s" %
            os.path.basename(unique_path)
        )

    except Exception as e:
        consoleMessage("Demo Renamer Execution Error: " + str(e))
