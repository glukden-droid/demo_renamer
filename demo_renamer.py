# -*- coding: utf-8 -*-
import bf2
import host
import os
import struct
import time
import zlib

DEMO_DIR = "mods/pr/demos/"
PLAY_DELAY = 245.0   # demo appears ~4 minutes after Playing
RETRY_WAIT = 10.0
RETRIES = 12

_round_started = 0.0
_rtimer = None


def _log(message):
    try:
        host.rcon_invoke('echo "DemoRenamer: %s"' % message)
    except Exception:
        pass


def _read_info(path):
    """Return 'YYYY_MM_DD_HH_MM_SS_map_gamemode_layer' from the demo, or None."""
    f = open(path, 'rb')
    try:
        data = zlib.decompressobj().decompress(f.read(65536))
    finally:
        f.close()

    # Header: u32 id, u32 len + server name, u32 len + start time
    size = struct.unpack('<I', data[4:8])[0]
    pos = 8 + size
    size = struct.unpack('<I', data[pos:pos + 4])[0]
    stamp = data[pos + 4:pos + 4 + size]
    stamp = time.strftime("%Y_%m_%d_%H_%M_%S", time.strptime(stamp.decode('latin-1'), "%Y-%m-%d %H:%M:%S"))

    # Current round: <gamemode> Levels/<map> u32 layer flag (layer * 2)
    mark = data.find(b"\x07\x00Levels/")
    mode = data[data.rfind(b"gpm_", 0, mark):mark]
    pos = mark + 9
    size = struct.unpack('<H', data[pos:pos + 2])[0]
    mapname = data[pos + 2:pos + 2 + size]
    layer = struct.unpack('<I', data[pos + 2 + size:pos + 6 + size])[0] // 2

    return "%s_%s_%s_%d" % (stamp, mapname.decode('latin-1').lower(), mode.decode('latin-1'), layer)


def _newest_demo():
    names = [n for n in os.listdir(DEMO_DIR) if n.startswith("auto_") and n.endswith(".bf2demo")]
    if not names:
        return None
    return max([os.path.join(DEMO_DIR, n) for n in names], key=os.path.getmtime)


def _rename_step(data):
    try:
        started, attempt = data
        if started != _round_started:
            return
        try:
            demo = _newest_demo()
            target = os.path.join(DEMO_DIR, "demo_%s.bf2demo" % _read_info(demo))
            os.rename(demo, target)
            _log("renamed to " + os.path.basename(target))
        except Exception as e:
            if attempt + 1 < RETRIES:
                _rtimer.fireOnce(_rename_step, RETRY_WAIT, (started, attempt + 1))
            else:
                _log("failed: " + str(e))
    except Exception as e:
        _log("error: " + str(e))


def onGameStatusChanged(status):
    global _round_started
    try:
        if status == bf2.GameStatus.Playing:
            _round_started = time.time()
            _rtimer.fireOnce(_rename_step, PLAY_DELAY, (_round_started, 0))
    except Exception as e:
        _log("error: " + str(e))


def init():
    global _rtimer
    try:
        import game.realitytimer as rtimer
        _rtimer = rtimer
        host.registerGameStatusHandler(onGameStatusChanged)
        _log("initialized")
    except Exception as e:
        _log("init failed: " + str(e))


def deinit():
    try:
        host.unregisterGameStatusHandler(onGameStatusChanged)
    except Exception as e:
        _log("deinit failed: " + str(e))
