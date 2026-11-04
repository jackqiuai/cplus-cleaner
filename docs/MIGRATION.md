# Rust architecture

The workspace contains three crates: `desktop` owns the Iced interface and desktop integration, `core` owns scan and operation logic, and `platform_windows` wraps native filesystem, registry, process and shell APIs.

`resources/` is embedded at build time by `include_dir`; keep the complete directory when cloning or packaging source. The desktop build script also embeds the tray icon and Windows application manifest. No absolute development-machine paths are required for compilation. Historical paths inside artwork provenance describe source material, not build dependencies.

Scans and destructive operations are separate. Before modifying files, engines validate the selected objects again. Migration uses verified copies and recovery journals; duplicate cleanup preserves a verified survivor. Compression reports actual allocated space. Ordinary C-drive cleanup file totals use logical size.

Runtime settings, history and recovery data belong in `%LOCALAPPDATA%\CPLUSCLEANER\`, outside this source tree. Preview profiles and validation fixtures must stay outside version control.

See [SOURCE_MAP.md](SOURCE_MAP.md) for every published file and directory, and [BUILDING.md](BUILDING.md) for clean-clone builds and release automation.