# -*- coding: utf-8 -*-
import bf2
import host
import os
import struct
import time
import zlib

DEMO_DIR = "mods/pr/demos/"
AUTO_PREFIX = "auto_"
DEMO_SUFFIX = ".bf2demo"

# Demo file appears ~4 minutes after Playing on this server
PLAY_DELAY = 245.0
RETRY_WAIT = 10.0
RETRIES = 12

# Map/mode block sits ~14 KB into the decompressed stream
READ_CHUNK = 65536
MAX_DECOMPRESSED = 4 * 1024 * 1024
LEVELS_MARK = b"\x07\x00Levels/"

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


def _is_name(text):
    if not text:
        return False
    for ch in text:
        if not (ch.isalnum() or ch == '_'):
            return False
    return True


def _read_str32(data, pos):
    if pos + 4 > len(data):
        return None, pos
    size = struct.unpack('<I', data[pos:pos + 4])[0]
    if size > 256 or pos + 4 + size > len(data):
        return None, pos
    return data[pos + 4:pos + 4 + size].decode('latin-1'), pos + 4 + size


def _parse_header(data):
    """
    Decompressed demo starts with:
    u32 ?, str32 server name, str32 'YYYY-MM-DD HH:MM:SS', str32 map
    """
    server, pos = _read_str32(data, 4)
    stamp, pos = _read_str32(data, pos)
    if server is None or stamp is None:
        return None
    try:
        parsed = time.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    return time.strftime("%Y_%m_%d_%H_%M_%S", parsed)


def _parse_level(data):
    """
    Current round block: u16 len + gamemode, u16 len + 'Levels/',
    u16 len + map, u32 layer flag (0x20 = layer 16, 0x80 = layer 64).
    """
    index = data.find(LEVELS_MARK)
    while index != -1:
        start = index + len(LEVELS_MARK)
        found = _level_at(data, index, start)
        if found:
            return found
        index = data.find(LEVELS_MARK, start)
    return None


def _level_at(data, index, start):
    if start + 2 > len(data):
        return None
    size = struct.unpack('<H', data[start:start + 2])[0]
    map_end = start + 2 + size
    if size == 0 or map_end + 4 > len(data):
        return None
    mapname = data[start + 2:map_end].decode('latin-1')
    flag = struct.unpack('<I', data[map_end:map_end + 4])[0]

    gamemode = None
    for mode_size in range(4, 40):
        mode_start = index - mode_size
        if mode_start < 2:
            break
        if struct.unpack('<H', data[mode_start - 2:mode_start])[0] != mode_size:
            continue
        text = data[mode_start:index].decode('latin-1')
        if text.startswith("gpm_") and _is_name(text):
            gamemode = text
            break

    if not _is_name(mapname) or not gamemode:
        return None
    if flag < 2 or flag & (flag - 1):
        _log("unexpected layer flag %d" % flag)
        return None
    return {"mapname": mapname, "gamemode": gamemode, "layer": str(flag // 2)}


def _read_demo_info(path):
    try:
        handle = open(path, 'rb')
    except Exception:
        return None

    decoder = zlib.decompressobj()
    data = b""
    info = None
    try:
        while len(data) < MAX_DECOMPRESSED:
            chunk = handle.read(READ_CHUNK)
            if not chunk:
                break
            data += decoder.decompress(chunk)
            info = _parse_level(data)
            if info:
                break
    except Exception:
        info = None
    handle.close()

    if not info:
        return None
    stamp = _parse_header(data)
    if not stamp:
        return None
    info["datetime"] = stamp
    return info


def _find_demo(started):
    try:
        names = os.listdir(DEMO_DIR)
    except Exception:
        return None

    best = None
    best_mtime = 0.0
    for name in names:
        if not name.startswith(AUTO_PREFIX) or not name.endswith(DEMO_SUFFIX):
            continue
        path = os.path.join(DEMO_DIR, name)
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            continue
        if mtime >= started - 10.0 and (best is None or mtime > best_mtime):
            best = path
            best_mtime = mtime
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


def _schedule_rename():
    global _round_started
    if not _rtimer:
        _log("rtimer missing")
        return
    _round_started = time.time()
    _log("trigger Playing")
    _rtimer.fireOnce(_rename_step, PLAY_DELAY, (_round_started, 0))


def _retry(started, attempt):
    if _rtimer and attempt + 1 < RETRIES and started == _round_started:
        _rtimer.fireOnce(_rename_step, RETRY_WAIT, (started, attempt + 1))
    else:
        _log("giving up")


def _rename_step(data=None):
    if not isinstance(data, tuple) or len(data) != 2:
        return

    started, attempt = data
    if started != _round_started:
        return

    demo = _find_demo(started)
    if not demo:
        _log("demo not found")
        _retry(started, attempt)
        return

    info = _read_demo_info(demo)
    if not info:
        _log("map info not in demo yet")
        _retry(started, attempt)
        return

    target_name = "demo_%s_%s_%s_%s.bf2demo" % (
        info["datetime"],
        _safe(info["mapname"], "unknown_map"),
        _safe(info["gamemode"], "gpm_cq"),
        _safe(info["layer"], "64"),
    )
    target_path = _unique_path(os.path.join(DEMO_DIR, target_name))
    if not target_path:
        _log("no free name for " + target_name)
        return

    try:
        os.rename(demo, target_path)
        _log("renamed to " + os.path.basename(target_path))
    except Exception:
        _log("rename retry")
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
