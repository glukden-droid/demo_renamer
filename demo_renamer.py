# -*- coding: utf-8 -*-
import bf2
import host
import os
import struct
import time
import zlib

DEMO_DIR = "mods/pr/demos/"


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


def _rename_all():
    """Rename every finished auto_*.bf2demo in DEMO_DIR."""
    for name in os.listdir(DEMO_DIR):
        if not name.startswith("auto_") or not name.endswith(".bf2demo"):
            continue
        demo = os.path.join(DEMO_DIR, name)
        try:
            target = os.path.join(DEMO_DIR, "demo_%s.bf2demo" % _read_info(demo))
            os.rename(demo, target)
            _log("renamed to " + os.path.basename(target))
        except Exception as e:
            _log("skip %s: %s" % (name, e))


def onGameStatusChanged(status):
    # Demos are not being written between rounds
    try:
        # PreGame is missing on some servers, so look the names up safely
        for name in ("EndGame", "PreGame"):
            if hasattr(bf2.GameStatus, name) and status == getattr(bf2.GameStatus, name):
                _rename_all()
                return
    except Exception as e:
        _log("error: " + str(e))


def init():
    try:
        host.registerGameStatusHandler(onGameStatusChanged)
        _log("initialized")
    except Exception as e:
        _log("init failed: " + str(e))


def deinit():
    try:
        host.unregisterGameStatusHandler(onGameStatusChanged)
    except Exception as e:
        _log("deinit failed: " + str(e))
