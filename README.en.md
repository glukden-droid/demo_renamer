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

1. When a round ends (EndGame) or the next one starts loading (PreGame), the
   plugin scans the demo folder. No demo is being written at that moment.
2. For each `auto_*.bf2demo` it reads the time, map, game mode and layer from
   the file and renames it.
3. If a file can't be read, it stays `auto_*`, the log shows `skip ...`, and
   the plugin tries again on the next status change.

A demo gets its new name after its round ends. The time in the name is when
the demo recording started, about 4 minutes after the round started.

The demo folder is set by `DEMO_DIR` at the top of `demo_renamer.py`
(default `mods/pr/demos/`).

## Log

All messages go to the server console with the `DemoRenamer:` prefix.

| Message              | Meaning                                         |
|----------------------|-------------------------------------------------|
| `initialized`        | plugin loaded                                   |
| `renamed to <name>`  | demo renamed                                    |
| `skip <file>: ...`   | file could not be read, stays `auto_*`          |
| `init failed: ...`   | plugin did not start                            |
| `error: ...`         | unexpected error; the server keeps running      |

Plugin errors never crash the server — they are only written to the log.
