# TDT on any Windows PC (M5 Atom over USB)

This project already supports automatic serial detection for the M5 Atom device.  
You do not need a fixed `COM5` port.

## 1) Flash firmware to M5 Atom (once per firmware update)

From this project folder:

```bat
pio run -t upload
```

## 2) Run with one GUI window (recommended)

Double-click:

- `run_tdt_gui.bat`

The GUI lets you:

- start a new session,
- watch run logs,
- browse previous sessions,
- view summary and trial table,
- open HTML report for selected session.

All runtime files (venv/cache/build helpers) are now stored in:

- `.runtime\...`

## 3) Build standalone GUI executable

Double-click:

- `build_tdt_gui_exe.bat`

After build:

- runtime executable is in `.runtime\build_gui\pyinstaller_dist\TDTConsole.exe`
- installer payload is in `installer\TDTStudio\TDTConsole.exe`

## 4) Build one-file installer (`Setup.exe`)

Double-click:

- `build_tdt_setup.bat`

This creates:

- `installer\output\TDTStudio_Setup.exe`

The setup supports:

- installation path selection,
- optional desktop shortcut,
- normal Windows uninstall entry.
- session output saved in `<InstallFolder>\data\sessions`.

## 5) Install / uninstall

Install:

- run `TDTStudio_Setup.exe`
- choose install path in installer

Uninstall:

- Windows Settings -> Apps -> Installed apps -> `TDT Studio` -> Uninstall
- or run `unins*.exe` from the installation folder

## 6) Distribute to another PC

On the target PC:

1. Plug in M5 Atom via USB.
2. Install and run `TDTStudio_Setup.exe`.
3. The app auto-detects the correct COM port.
4. If detection fails, use the built-in GUI diagnostics (`List Ports`).

## Notes

- Session outputs are saved in `<AppRoot>\data\sessions`.
- If Excel export dependency is missing, session still runs and logs data.
- If notebook updates are unavailable, HTML report export still works.
- Citation and source references are in `SOURCES_AND_CITATION.md`.
