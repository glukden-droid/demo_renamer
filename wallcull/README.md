# wallcull

Server-side anti-wallhack for `prbf2_l64ded` (Linux, x86-64), loaded through `LD_PRELOAD`.

The server already decides which objects each client receives, in
`ServerConnection::calculateObjectPriority` (`0x43c220`). If that returns 0,
the object is not sent and its ghost is removed on the client. wallcull wraps
that function. For enemy soldiers on foot that the client cannot see, it returns 0.
ESP has nothing to read for them: boxes, skeleton, health and "behind wall" all disappear.

Visibility uses the engine's own `ObjectManager::intersectLine`, with the same
predicator that `GameLogic::findBestTargetObject` uses for target lock LOS.

## What is culled

| Object | Behavior |
|---|---|
| Enemy soldier on foot, farther than `WALLCULL_NEAR`, no clear line of sight | not sent |
| Enemy soldier, line of sight clear in the last `WALLCULL_HOLD` s | sent |
| Enemy soldier that fired in the last `WALLCULL_SHOT_HOLD` s, within `WALLCULL_SHOT_RANGE` (`WALLCULL_QUIET_RANGE` if suppressed) | sent, so the client plays the gunfire |
| Teammates, vehicles, soldiers inside vehicles, projectiles, items | unchanged |
| Spectators (team not 1/2), demo recording connection | unchanged |

A line of sight check casts rays from two eye points (camera, camera +0.5 m)
to the head, chest and prone heights of the target. It does this at the
target's current position and at its position `WALLCULL_LEAD` seconds ahead.
Any clear ray makes the target visible.

## Build and install

```sh
gcc -O2 -fPIC -shared -o libwallcull.so wallcull.c
LD_PRELOAD=/path/to/libwallcull.so ./prbf2_l64ded ...
```

Build it on the server or on any glibc >= 2.17 system. If `LD_PRELOAD` is
removed or `WALLCULL_DISABLE=1` is set, the server runs unchanged.

The hook is installed only if the first bytes of `calculateObjectPriority`
match the build it was written for
(sha256 `dc63f03a72d52402f6a621742cb377809fcaa9bef31b11c6f013b65053f5f819`).
After a PR update, stderr shows `unknown server build` and nothing is patched.
The addresses then need to be found again.

## Settings

All distances are in metres and all times in seconds. Settings come from
`wallcull.cfg` in the server's working directory (another path:
`WALLCULL_CONFIG=/path/to/file`). The server re-reads the file within about 5 s
after it changes, no restart needed. stderr then shows `settings reloaded:`
with every value. A bad or out-of-range value is reported and the old one is
kept. Environment variables set the values at startup, and the file overrides them.

| Key in `wallcull.cfg` | Env variable | Default | Allowed | Meaning |
|---|---|---|---|---|
| `near` | `WALLCULL_NEAR` | 20 | 0-500 | enemies closer are always sent (footsteps) |
| `hold` | `WALLCULL_HOLD` | 1.5 | 0-10 | keep sending after the enemy was last visible |
| `lead` | `WALLCULL_LEAD` | 0.25 | 0-2 | predict enemy movement this far ahead |
| `recheck` | `WALLCULL_RECHECK` | 0.2 | 0.02-5 | seconds between checks for one viewer/enemy pair |
| `shot_range` | `WALLCULL_SHOT_RANGE` | 150 | 0-5000 | a firing enemy is sent within this distance |
| `quiet_range` | `WALLCULL_QUIET_RANGE` | 40 | 0-5000 | same, for suppressed weapons (`getNoisy() == 0`) |
| `shot_hold` | `WALLCULL_SHOT_HOLD` | 1.5 | 0-30 | keep sending after the last shot |
| `ray_budget` | `WALLCULL_RAY_BUDGET` | 40000 | 0-10000000 | rays per second; above it enemies count as visible |
| `log` | `WALLCULL_LOG` | 60 | 0-86400 | seconds between stats lines on stderr, 0 = off |

`wallcull.cfg` in this folder is a commented example with the defaults.

Firing is read the way `Player::updateGhostFiringState` does it: weapons in
slots 0-2 of the soldier's `IPlayerControlObject`, then `IWeaponObject::isFiring()`
and `getNoisy()`. A shooter becomes visible to the cheat only while the gunfire
is audible, which a player hears anyway.

## Gameplay trade-offs

- Gunfire of hidden enemies is kept within the shot ranges above. Other
  sounds of a culled enemy beyond `WALLCULL_NEAR` are not played: footsteps,
  reloads, voice. Footsteps are short range, so `WALLCULL_NEAR` covers most of them.
- Shot ranges are rough. Match them to how far PR weapons are audible; a
  larger range is more realistic sound, a smaller one hides shooters better.
- Bushes and smoke usually have no collision, so enemies in them stay visible.
  This errs toward sending too much, not too little.
- When an enemy steps out, the client needs to re-create the ghost. `WALLCULL_LEAD`
  and the +0.5 m eye point cover this for normal pings. Raise them if players
  report enemies popping in.

## Test plan (test server first)

Nothing here has been run against a live server yet.

1. Start with `LD_PRELOAD`. stderr must show `wallcull: active ...`.
2. Two clients on opposite teams, one with `WALLCULL_LOG=5`:
   - In open view at 50+ m: the enemy must stay visible, without flicker.
     If enemies vanish while in plain sight, the predicator does not ignore
     the target. Stop and report.
   - Behind a building at 50+ m: the `culled` counter grows. An ESP overlay
     (or `debugShowActiveGhosts` on the client) no longer shows the enemy.
   - Step out from cover: the enemy appears without visible delay.
   - Enemy behind cover at 80 m fires: the shots are heard, the `shots`
     counter grows, the enemy disappears again ~1.5 s after the last shot.
     With a suppressed weapon at 80 m: not sent.
3. Full server, one round: watch the `rays` rate and `over_budget` in the log
   and the server tick time. Lower `WALLCULL_RECHECK` cost by raising it
   if CPU is tight.
