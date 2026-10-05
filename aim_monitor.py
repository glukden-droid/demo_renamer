# -*- coding: utf-8 -*-
"""
Server-side aim monitor.

Samples every player's view angles and, on each kill, measures:
  - snap: peak turn speed (deg/s) shortly before the kill
  - err:  angle between the killer's view and the line to the victim

A kill with a fast snap that ends exactly on target at range is marked
suspicious. Per-player counts are reported at round end. Nothing is
kicked or banned: output is for admins to review (log + demo).
"""
import bf2
import host
import math
import os
import time

LOG_DIR = "mods/pr/logs/"
LOG_NAME = "aim_monitor_%s.csv"

SAMPLE_INTERVAL = 0.1      # seconds between view samples
HISTORY = 1.5              # seconds of samples kept per player
SNAP_WINDOW = 0.6          # seconds before the kill checked for a snap

SNAP_SPEED = 500.0         # deg/s considered a snap
ON_TARGET_ERR = 2.0        # deg from victim's centre counted as locked
MIN_DISTANCE = 25.0        # metres; close kills are too noisy
FLAG_KILLS = 3             # suspicious kills per round to alert admins

# BF2 yaw convention, verify on a test server (see README_aim_monitor)
YAW_SIGN = 1.0
EYE_HEIGHT = 1.6

_timer = None
_history = {}              # player index -> [(t, yaw, pitch), ...]
_stats = {}                # player index -> [name, kills, suspicious]
_log_file = None


def _log(message):
    try:
        host.rcon_invoke('echo "AimMonitor: %s"' % str(message).replace('"', "'"))
    except Exception:
        pass


def _object(player):
    try:
        vehicle = player.getVehicle()
        if vehicle:
            return vehicle
    except Exception:
        pass
    try:
        return player.getDefaultVehicle()
    except Exception:
        return None


def _angle_diff(a, b):
    diff = (a - b) % 360.0
    if diff > 180.0:
        diff -= 360.0
    return diff


def _sample(data=None):
    now = time.time()
    try:
        players = bf2.playerManager.getPlayers()
    except Exception:
        return

    for player in players:
        try:
            if not player.isAlive():
                continue
            obj = _object(player)
            if not obj:
                continue
            rot = obj.getRotation()
            index = player.index
        except Exception:
            continue

        samples = _history.setdefault(index, [])
        samples.append((now, rot[0], rot[1]))
        while samples and samples[0][0] < now - HISTORY:
            samples.pop(0)


def _snap_speed(samples, until):
    best = 0.0
    prev = None
    for sample in samples:
        if sample[0] < until - SNAP_WINDOW or sample[0] > until:
            continue
        if prev:
            dt = sample[0] - prev[0]
            if dt > 0:
                turn = math.hypot(_angle_diff(sample[1], prev[1]),
                                  _angle_diff(sample[2], prev[2]))
                best = max(best, turn / dt)
        prev = sample
    return best


def _aim_error(attacker_obj, victim_obj):
    a = attacker_obj.getPosition()
    v = victim_obj.getPosition()
    rot = attacker_obj.getRotation()

    dx = v[0] - a[0]
    dy = v[1] - a[1]
    dz = v[2] - a[2]
    flat = math.hypot(dx, dz)
    distance = math.sqrt(flat * flat + dy * dy)

    want_yaw = YAW_SIGN * math.degrees(math.atan2(dx, dz))
    want_pitch = -math.degrees(math.atan2(dy, flat))
    err = math.hypot(_angle_diff(rot[0], want_yaw), _angle_diff(rot[1], want_pitch))
    return err, distance


def _write(row):
    global _log_file
    try:
        if _log_file is None:
            if not os.path.isdir(LOG_DIR):
                os.makedirs(LOG_DIR)
            path = os.path.join(LOG_DIR, LOG_NAME % time.strftime("%Y_%m_%d"))
            new = not os.path.exists(path)
            _log_file = open(path, "a")
            if new:
                _log_file.write("time,attacker,victim,weapon,distance,snap,err,suspicious\n")
        _log_file.write(",".join([str(x) for x in row]) + "\n")
        _log_file.flush()
    except Exception as e:
        _log("log write failed: " + str(e))


def onPlayerKilled(victim, attacker, weapon, assists, victim_soldier):
    try:
        if not attacker or not victim or attacker == victim:
            return
        if attacker.getTeam() == victim.getTeam():
            return
        attacker_obj = _object(attacker)
        if not attacker_obj or not victim_soldier:
            return
        err, distance = _aim_error(attacker_obj, victim_soldier)
        snap = _snap_speed(_history.get(attacker.index, []), time.time())
        try:
            weapon_name = weapon.templateName
        except Exception:
            weapon_name = "unknown"
        name = attacker.getName()
    except Exception:
        return

    suspicious = (distance >= MIN_DISTANCE and snap >= SNAP_SPEED
                  and err <= ON_TARGET_ERR)

    entry = _stats.setdefault(attacker.index, [name, 0, 0])
    entry[0] = name
    entry[1] += 1
    if suspicious:
        entry[2] += 1
        if entry[2] == FLAG_KILLS:
            _log("check %s: %d snap kills this round" % (name, entry[2]))

    _write([time.strftime("%H:%M:%S"), name.replace(",", " "),
            victim.getName().replace(",", " "), weapon_name,
            "%.1f" % distance, "%.0f" % snap, "%.2f" % err, int(suspicious)])


def _report():
    for index in _stats:
        name, kills, suspicious = _stats[index]
        if suspicious:
            _log("round: %s %d/%d snap kills" % (name, suspicious, kills))
    _stats.clear()
    _history.clear()


def onGameStatusChanged(status):
    if status == bf2.GameStatus.EndGame:
        _report()
    elif status == bf2.GameStatus.Playing:
        _stats.clear()
        _history.clear()


def onPlayerDisconnect(player):
    try:
        _history.pop(player.index, None)
    except Exception:
        pass


def init():
    global _timer
    try:
        _timer = bf2.Timer(_sample, SAMPLE_INTERVAL, 1)
        _timer.setRecurring(SAMPLE_INTERVAL)
        host.registerHandler("PlayerKilled", onPlayerKilled)
        host.registerHandler("PlayerDisconnect", onPlayerDisconnect)
        host.registerGameStatusHandler(onGameStatusChanged)
        _log("initialized")
    except Exception as e:
        _log("init failed: " + str(e))


def deinit():
    global _timer, _log_file
    try:
        if _timer:
            _timer.destroy()
        _timer = None
        host.unregisterGameStatusHandler(onGameStatusChanged)
    except Exception:
        pass
    try:
        if _log_file:
            _log_file.close()
    except Exception:
        pass
    _log_file = None
