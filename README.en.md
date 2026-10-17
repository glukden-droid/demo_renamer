[Русский](README.md) · **English**

# Demo Renamer

A plugin for Project Reality (BF2) servers. It renames automatic demos
`auto_*.bf2demo` to readable names:

```
demo_<date_time>_<map>_<gamemode>_<layer>.bf2demo
demo_2026_09_01_19_45_00_assault_on_grozny_gpm_cq_128.bf2demo
```

Everything is read from the demo itself: recording start time, map, game mode
and layer.

## Requirements

- Automatic demo recording is enabled on the server (`sv.autoRecord 1` in
  `serversettings.con`).
- Demos are written to `mods/pr/demos/`.
- The `game.realitytimer` module is available (ships with PR).

## Installation

1. Copy `demo_renamer.py` to `mods/pr/python/game/`.
2. Load it where the server's other Python modules are loaded, for example in
   `mods/pr/python/game/__init__.py`:

   ```python
   import demo_renamer
   demo_renamer.init()
   ```

3. Restart the server.

On a successful start the server log shows `DemoRenamer: initialized`.

## How it works

1. When a round ends (EndGame), the plugin starts a check every 10 seconds.
   The server stops recording a little after EndGame.
2. An `auto_*.bf2demo` is renamed once its size stops changing between two
   checks, which means recording has finished.
3. The time, map, game mode and layer are read from the file.
4. Checks run for up to 5 minutes. If a file still can't be read, it stays
   `auto_*`, the log shows `skip ...`, and the plugin tries again after the
   next round.

The time in the name is when the demo recording started, about 4 minutes
after the round started.

## Settings

At the top of `demo_renamer.py`:

| Setting      | Default           | Meaning                                |
|--------------|-------------------|----------------------------------------|
| `DEMO_DIR`   | `mods/pr/demos/`  | demo folder                            |
| `CHECK_WAIT` | `10.0`            | pause between checks, seconds          |
| `CHECKS`     | `30`              | number of checks after EndGame         |

## Log

All messages go to the server console with the `DemoRenamer:` prefix.

| Message              | Meaning                                         |
|----------------------|-------------------------------------------------|
| `initialized`        | plugin loaded                                   |
| `renamed to <name>`  | demo renamed                                    |
| `skip <file>: ...`   | file could not be read, stays `auto_*`          |
| `init failed: ...`   | plugin did not start (no `realitytimer`, etc.)  |
| `error: ...`         | unexpected error; the server keeps running      |

Plugin errors never crash the server — they are only written to the log.
