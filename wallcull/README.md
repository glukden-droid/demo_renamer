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

## Settings (environment)

| Variable | Default | Meaning |
|---|---|---|
| `WALLCULL_NEAR` | 20 | metres; enemies closer are always sent (footsteps) |
| `WALLCULL_HOLD` | 1.5 | seconds to keep sending after a clear ray |
| `WALLCULL_RECHECK` | 0.2 | seconds between checks for one viewer/target pair |
| `WALLCULL_LEAD` | 0.25 | seconds of target movement prediction |
| `WALLCULL_RAY_BUDGET` | 40000 | rays per second; above it targets count as visible |
| `WALLCULL_LOG` | 60 | seconds between stats lines on stderr, 0 = off |

## Gameplay trade-offs

- Sounds of a culled enemy are not played on the client: footsteps, reloads,
  and gunfire beyond `WALLCULL_NEAR` from enemies behind cover. PR relies on
  sound. Raise `WALLCULL_NEAR` (e.g. 50) if this matters more than ESP at range.
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
3. Full server, one round: watch the `rays` rate and `over_budget` in the log
   and the server tick time. Lower `WALLCULL_RECHECK` cost by raising it
   if CPU is tight.
