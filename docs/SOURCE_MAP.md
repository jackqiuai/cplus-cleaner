# Source file and directory map

Every published directory and file is listed below. Paths stay unchanged to preserve Rust and embedded-resource references. Descriptions document purpose without adding comments to JSON or binary assets. 本表逐项说明发布工程的目录和文件用途，保留原路径以免破坏引用。

| Path | Purpose / 用途 |
| --- | --- |
| `.cargo/` | Workspace Cargo configuration |
| `.cargo/config.toml` | Static MSVC runtime and development build settings |
| `.gitattributes` | Text line endings and binary asset handling |
| `.github/` | GitHub automation configuration |
| `.github/workflows/` | GitHub automation configuration |
| `.github/workflows/build-windows.yml` | Windows CI, executable build, optional signing and release upload |
| `.gitignore` | Exclude generated output, runtime data and credentials |
| `build.ps1` | Run regression tests, compile release executable and package |
| `Cargo.lock` | Locked dependency versions and checksums |
| `Cargo.toml` | Rust workspace members, package metadata and shared dependencies |
| `crates/` | Rust workspace crates |
| `crates/core/` | Scan engines, operations, validation and tests |
| `crates/core/build.rs` | Rebuild core when embedded resources change |
| `crates/core/Cargo.toml` | core crate dependencies and package configuration |
| `crates/core/examples/` | Scan engines, operations, validation and tests |
| `crates/core/examples/application_scan.rs` | Read-only diagnostic example: application scan |
| `crates/core/examples/inspect.rs` | Read-only diagnostic example: inspect |
| `crates/core/examples/large_file_scan.rs` | Read-only diagnostic example: large file scan |
| `crates/core/src/` | Scan engines, operations, validation and tests |
| `crates/core/src/common/` | Core common support and shared utilities |
| `crates/core/src/common/mod.rs` | Core common support and shared utilities |
| `crates/core/src/common/rule_scan.rs` | Batch directory enumeration for cleanup rules |
| `crates/core/src/features/` | Core features support and shared utilities |
| `crates/core/src/features/app_compression/` | Application compression and restoration implementation |
| `crates/core/src/features/app_compression/mod.rs` | Application compression and restoration — engine and public operations |
| `crates/core/src/features/app_mover/` | Application migration and restoration implementation |
| `crates/core/src/features/app_mover/mod.rs` | Application migration and restoration — engine and public operations |
| `crates/core/src/features/chat_cleanup/` | Chat cleanup implementation |
| `crates/core/src/features/chat_cleanup/mod.rs` | Chat cleanup — engine and public operations |
| `crates/core/src/features/chat_cleanup/scanner.rs` | Chat cleanup — rule-based scan engine |
| `crates/core/src/features/drive_cleanup/` | C-drive cleanup implementation |
| `crates/core/src/features/drive_cleanup/mod.rs` | C-drive cleanup — engine and public operations |
| `crates/core/src/features/duplicates/` | Duplicate file cleanup implementation |
| `crates/core/src/features/duplicates/deletion.rs` | Duplicate file cleanup — validated duplicate deletion |
| `crates/core/src/features/duplicates/hashing.rs` | Duplicate file cleanup — staged content hashing |
| `crates/core/src/features/duplicates/mod.rs` | Duplicate file cleanup — engine and public operations |
| `crates/core/src/features/duplicates/scan.rs` | Duplicate file cleanup — scan orchestration |
| `crates/core/src/features/file_scan.rs` | Shared large-file and duplicate file selection policy |
| `crates/core/src/features/home/` | Combined Home scan implementation |
| `crates/core/src/features/home/mod.rs` | Combined Home scan — engine and public operations |
| `crates/core/src/features/large_files/` | Large file migration implementation |
| `crates/core/src/features/large_files/mod.rs` | Large file migration — engine and public operations |
| `crates/core/src/features/large_files/scan.rs` | Large file migration — scan orchestration |
| `crates/core/src/features/mod.rs` | Feature dispatch — engine and public operations |
| `crates/core/src/features/popup_blocker/` | Popup discovery and interception implementation |
| `crates/core/src/features/popup_blocker/mod.rs` | Popup discovery and interception — engine and public operations |
| `crates/core/src/features/privacy/` | Privacy cleanup implementation |
| `crates/core/src/features/privacy/mod.rs` | Privacy cleanup — engine and public operations |
| `crates/core/src/features/system_slim/` | Windows system slimming implementation |
| `crates/core/src/features/system_slim/mod.rs` | Windows system slimming — engine and public operations |
| `crates/core/src/features/uninstall/` | Installed application discovery, uninstall and residue handling implementation |
| `crates/core/src/features/uninstall/inventory.rs` | Installed application discovery, uninstall and residue handling — registry and MSI application inventory |
| `crates/core/src/features/uninstall/mod.rs` | Installed application discovery, uninstall and residue handling — engine and public operations |
| `crates/core/src/features/uninstall/residue/` | Installed application discovery, uninstall and residue handling implementation |
| `crates/core/src/features/uninstall/residue.rs` | Installed application discovery, uninstall and residue handling — post-uninstall residue discovery |
| `crates/core/src/features/uninstall/residue/registry_residue.rs` | Installed application discovery, uninstall and residue handling — scoped registry residue checks |
| `crates/core/src/features/uninstall/scan_measurement.rs` | Installed application discovery, uninstall and residue handling — directory size and allocation measurement |
| `crates/core/src/jobs/` | Core jobs support and shared utilities |
| `crates/core/src/jobs/mod.rs` | Core jobs support and shared utilities |
| `crates/core/src/lib.rs` | core crate public exports and initialization |
| `crates/core/src/models.rs` | Shared feature, scan, application and operation data types |
| `crates/core/src/storage/` | Core storage support and shared utilities |
| `crates/core/src/storage/mod.rs` | Core storage support and shared utilities |
| `crates/core/src/updates.rs` | GitHub release update checks |
| `crates/core/src/validation.rs` | Executable smoke validation of embedded assets and runtime bundle |
| `crates/core/tests/` | Scan engines, operations, validation and tests |
| `crates/core/tests/application_scan.rs` | Regression tests: application scan |
| `crates/core/tests/duplicate_scan.rs` | Regression tests: duplicate scan |
| `crates/core/tests/large_file_scan.rs` | Regression tests: large file scan |
| `crates/core/tests/windows_behaviour.rs` | Regression tests: windows behaviour |
| `crates/desktop/` | Iced desktop application source |
| `crates/desktop/app.manifest` | Windows compatibility, DPI and execution manifest |
| `crates/desktop/build.rs` | Embed Windows icon, manifest and executable metadata |
| `crates/desktop/Cargo.toml` | desktop crate dependencies and package configuration |
| `crates/desktop/examples/` | Iced desktop application source |
| `crates/desktop/src/` | Iced desktop application source |
| `crates/desktop/src/app.rs` | Application state, messages, jobs, navigation and user actions |
| `crates/desktop/src/i18n/` | Language selection, lookup and translation formatting |
| `crates/desktop/src/i18n/mod.rs` | Language selection, lookup and translation formatting |
| `crates/desktop/src/integrations/` | Desktop integration, tray and native event bridge |
| `crates/desktop/src/integrations/mod.rs` | Desktop integration, tray and native event bridge |
| `crates/desktop/src/main.rs` | Desktop application entry point and command-line modes |
| `crates/desktop/src/monitor.rs` | Shared asynchronous system-status model, live counters and temperature sampling |
| `crates/desktop/src/ui/` | Iced desktop application source |
| `crates/desktop/src/ui/dialogs/` | Iced UI components: dialogs |
| `crates/desktop/src/ui/dialogs/activity.rs` | Iced UI implementation: activity |
| `crates/desktop/src/ui/dialogs/feedback.rs` | Iced UI implementation: feedback |
| `crates/desktop/src/ui/dialogs/mod.rs` | Iced UI implementation: mod |
| `crates/desktop/src/ui/dialogs/settings.rs` | Iced UI implementation: settings |
| `crates/desktop/src/ui/fonts.rs` | Iced UI implementation: fonts |
| `crates/desktop/src/ui/mod.rs` | Iced UI implementation: mod |
| `crates/desktop/src/ui/monitor.rs` | Iced UI implementation: monitor |
| `crates/desktop/src/ui/pages/` | Iced UI components: pages |
| `crates/desktop/src/ui/pages/applications.rs` | Iced UI implementation: applications |
| `crates/desktop/src/ui/pages/cleanup.rs` | Iced UI implementation: cleanup |
| `crates/desktop/src/ui/pages/duplicates.rs` | Iced UI implementation: duplicates |
| `crates/desktop/src/ui/pages/files.rs` | Iced UI implementation: files |
| `crates/desktop/src/ui/pages/home.rs` | Iced UI implementation: home |
| `crates/desktop/src/ui/pages/mod.rs` | Iced UI implementation: mod |
| `crates/desktop/src/ui/pages/popup.rs` | Iced UI implementation: popup |
| `crates/desktop/src/ui/pages/system.rs` | Iced UI implementation: system |
| `crates/desktop/src/ui/theme.rs` | Iced UI implementation: theme |
| `crates/desktop/src/ui/tray.rs` | Iced UI implementation: tray |
| `crates/desktop/src/ui/widgets.rs` | Iced UI implementation: widgets |
| `crates/platform_windows/` | Windows native API layer |
| `crates/platform_windows/Cargo.toml` | platform_windows crate dependencies and package configuration |
| `crates/platform_windows/src/` | Windows native API layer |
| `crates/platform_windows/src/filesystem/` | Native Windows filesystem API wrappers |
| `crates/platform_windows/src/filesystem/content.rs` | Read and validate file content using native handles |
| `crates/platform_windows/src/filesystem/directory.rs` | Windows batch directory information API |
| `crates/platform_windows/src/filesystem/large_scan/` | Native Windows filesystem API wrappers |
| `crates/platform_windows/src/filesystem/large_scan.rs` | Fast NTFS scan orchestration and enumeration fallback |
| `crates/platform_windows/src/filesystem/large_scan/layout/` | Native Windows filesystem API wrappers |
| `crates/platform_windows/src/filesystem/large_scan/layout.rs` | NTFS volume layout enumeration |
| `crates/platform_windows/src/filesystem/large_scan/layout/tests.rs` | Regression tests: tests |
| `crates/platform_windows/src/filesystem/mod.rs` | Native Windows filesystem API wrappers |
| `crates/platform_windows/src/lib.rs` | platform_windows crate public exports and initialization |
| `crates/platform_windows/src/monitor/` | Native Windows monitor API wrappers |
| `crates/platform_windows/src/monitor.rs` | Native Windows monitor.rs API wrappers |
| `crates/platform_windows/src/monitor/cpu_sensor.rs` | Validated fixed CPU temperature reads using signed PawnIO modules |
| `crates/platform_windows/src/monitor/cpu_service.rs` | Protected temperature service installation, sampling, update recovery and removal |
| `crates/platform_windows/src/monitor/taskbar.rs` | Native Windows monitor API wrappers |
| `crates/platform_windows/src/monitor/temperature.ps1` | Read system-disk temperature using Windows storage counters and SMART |
| `crates/platform_windows/src/monitor/temperature.rs` | Bounded asynchronous disk temperature probe and value validation |
| `crates/platform_windows/src/processes/` | Native Windows processes API wrappers |
| `crates/platform_windows/src/processes/mod.rs` | Native Windows processes API wrappers |
| `crates/platform_windows/src/registry/` | Native Windows registry API wrappers |
| `crates/platform_windows/src/registry/mod.rs` | Native Windows registry API wrappers |
| `crates/platform_windows/src/shell/` | Native Windows shell API wrappers |
| `crates/platform_windows/src/shell/mod.rs` | Native Windows shell API wrappers |
| `crates/platform_windows/src/windows/` | Native Windows windows API wrappers |
| `crates/platform_windows/src/windows/mod.rs` | Native Windows windows API wrappers |
| `crates/platform_windows/vendors/` | Third-party components retained for reproducible offline builds |
| `crates/platform_windows/vendors/pawnio/` | Official PawnIO installer, signed modules, corresponding sources and notices |
| `crates/platform_windows/vendors/pawnio/AMDFamily17.bin` | Signed AMD Zen temperature module embedded at compile time |
| `crates/platform_windows/vendors/pawnio/AMDFamily17.p` | Corresponding upstream source for the signed AMD module |
| `crates/platform_windows/vendors/pawnio/COPYING` | Full upstream LGPL module license text |
| `crates/platform_windows/vendors/pawnio/include/` | Headers required by the corresponding PawnIO module sources |
| `crates/platform_windows/vendors/pawnio/include/pawnio.inc` | Unmodified upstream PawnIO module API declarations |
| `crates/platform_windows/vendors/pawnio/IntelMSR.bin` | Signed Intel temperature module embedded at compile time |
| `crates/platform_windows/vendors/pawnio/IntelMSR.p` | Corresponding upstream source for the signed Intel module |
| `crates/platform_windows/vendors/pawnio/NOTICE.md` | Component versions, licensing terms, source links and pinned digest provenance |
| `crates/platform_windows/vendors/pawnio/PawnIO_setup.exe` | Unmodified official PawnIO 2.2.0 installer embedded for optional CPU setup |
| `docs/` | Build, source, validation and signing documentation |
| `docs/BUILDING.md` | Clean-clone build requirements, commands, CI and release guide |
| `docs/CODE_SIGNING.md` | Optional signing configuration without credentials |
| `docs/CPU_TEMPERATURE.md` | Optional PawnIO CPU temperature setup, protected service and removal guide |
| `docs/images/` | Localized application screenshots Build, source, validation and signing documentation |
| `docs/images/home-en.png` | English application Home screenshot |
| `docs/images/home-zh-CN.png` | Simplified Chinese application Home screenshot |
| `docs/images/home-zh-TW.png` | Traditional Chinese application Home screenshot |
| `docs/MIGRATION.md` | Current Rust architecture and runtime data boundaries |
| `docs/SOURCE_MAP.md` | Complete directory and file purpose index |
| `docs/VALIDATION.md` | Release validation procedure and limits |
| `LICENSE` | MIT terms for project code |
| `package.ps1` | Smoke-check EXE, package documentation and generate SHA-256 sums |
| `README.md` | Default English project introduction and instructions |
| `README.zh-CN.md` | Simplified Chinese project introduction and instructions |
| `README.zh-TW.md` | Traditional Chinese project introduction and instructions |
| `resources/` | Complete embedded runtime resources and provenance |
| `resources/assets/` | Complete embedded runtime resources and provenance |
| `resources/assets/app-icon.png` | Application brand, window or tray icon |
| `resources/assets/asset-sources.json` | Artwork provenance and historical source hashes |
| `resources/assets/brand/` | Retained UI resource: brand |
| `resources/assets/brand/HERO-ARTWORK.md` | Resource purpose, provenance and usage documentation |
| `resources/assets/brand/hero-assets.json` | Artwork provenance, preparation metadata or design reference |
| `resources/assets/brand/hero-opacity-prompt.json` | Artwork provenance, preparation metadata or design reference |
| `resources/assets/brand/hero-prompts.json` | Artwork provenance, preparation metadata or design reference |
| `resources/assets/brand/logo.svg` | Application brand, window or tray icon |
| `resources/assets/brand/README.md` | Resource purpose, provenance and usage documentation |
| `resources/assets/brand/sidebar.png` | Application brand, window or tray icon |
| `resources/assets/categories/` | Retained UI resource: categories |
| `resources/assets/categories/100.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/101.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/102.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/103.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/104.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/105.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/106.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/107.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/109.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/200.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/202.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/203.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/204.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/205.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/208.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/209.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/211.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/212.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/214.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/216.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/217.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/218.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40000.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40002.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40003.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40004.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40005.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40006.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40007.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40008.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40009.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40010.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40011.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40012.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40013.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40014.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40015.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40016.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40017.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40018.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40019.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40020.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40021.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40022.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40023.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40024.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/40029.png` | Drive Cleanup category and system item icons |
| `resources/assets/categories/README.md` | Resource purpose, provenance and usage documentation |
| `resources/assets/category-icons.json` | Category identifiers mapped to bundled icon files |
| `resources/assets/chat-rules.json` | Chat application cleanup definitions |
| `resources/assets/check-mixed.png` | Shared selection controls |
| `resources/assets/check-off.png` | Shared selection controls |
| `resources/assets/check-on.png` | Shared selection controls |
| `resources/assets/donation/` | Retained UI resource: donation |
| `resources/assets/donation/sources.json` | Artwork provenance, preparation metadata or design reference |
| `resources/assets/donation/wechat.png` | Owner-supplied WeChat donation QR image |
| `resources/assets/hero-clean.png` | Cleanup or large-file illustration |
| `resources/assets/hero-large.png` | Cleanup or large-file illustration |
| `resources/assets/home/` | Retained UI resource: home |
| `resources/assets/home/background.png` | Home background and scan mark |
| `resources/assets/home/prepared-manifest.json` | Artwork provenance, preparation metadata or design reference |
| `resources/assets/home/README.md` | Resource purpose, provenance and usage documentation |
| `resources/assets/home/search.png` | Home background and scan mark |
| `resources/assets/home/search.svg` | Home background and scan mark |
| `resources/assets/home/source-manifest.json` | Artwork provenance, preparation metadata or design reference |
| `resources/assets/icon-cache.png` | Application and category icon fallback |
| `resources/assets/icon-chrome.png` | Application and category icon fallback |
| `resources/assets/icon-edge.png` | Application and category icon fallback |
| `resources/assets/icon-logs.png` | Application and category icon fallback |
| `resources/assets/icon-qq.png` | Application and category icon fallback |
| `resources/assets/icon-recycle.png` | Application and category icon fallback |
| `resources/assets/icon-system.png` | Application and category icon fallback |
| `resources/assets/icon-temp.png` | Application and category icon fallback |
| `resources/assets/icon-wechat.png` | Application and category icon fallback |
| `resources/assets/icon-wps.png` | Application and category icon fallback |
| `resources/assets/large-c-azb.png` | Large File Mover type icons |
| `resources/assets/large-c-bgwd.png` | Large File Mover type icons |
| `resources/assets/large-c-file.png` | Large File Mover type icons |
| `resources/assets/large-c-play.png` | Large File Mover type icons |
| `resources/assets/large-c-ysb.png` | Large File Mover type icons |
| `resources/assets/nav-cdrive-active.png` | Sidebar navigation |
| `resources/assets/nav-cdrive.png` | Sidebar navigation |
| `resources/assets/nav-compress-active.png` | Sidebar navigation |
| `resources/assets/nav-compress.png` | Sidebar navigation |
| `resources/assets/nav-duplicates-active.png` | Sidebar navigation |
| `resources/assets/nav-duplicates.png` | Sidebar navigation |
| `resources/assets/nav-home-active.png` | Sidebar navigation |
| `resources/assets/nav-home.png` | Sidebar navigation |
| `resources/assets/nav-large-active.png` | Sidebar navigation |
| `resources/assets/nav-large.png` | Sidebar navigation |
| `resources/assets/nav-migrate-active.png` | Sidebar navigation |
| `resources/assets/nav-migrate.png` | Sidebar navigation |
| `resources/assets/nav-popup-active.png` | Sidebar navigation |
| `resources/assets/nav-popup.png` | Sidebar navigation |
| `resources/assets/nav-privacy-active.png` | Sidebar navigation |
| `resources/assets/nav-privacy.png` | Sidebar navigation |
| `resources/assets/nav-system-active.png` | Sidebar navigation |
| `resources/assets/nav-system-active.svg` | Sidebar navigation |
| `resources/assets/nav-system.png` | Sidebar navigation |
| `resources/assets/nav-system.svg` | Sidebar navigation |
| `resources/assets/nav-uninstall-active.png` | Sidebar navigation |
| `resources/assets/nav-uninstall.png` | Sidebar navigation |
| `resources/assets/nav-wechat-active.png` | Sidebar navigation |
| `resources/assets/nav-wechat.png` | Sidebar navigation |
| `resources/assets/popup-huorong.json` | Popup matching definitions |
| `resources/assets/privacy/` | Retained UI resource: privacy |
| `resources/assets/privacy/app_switched.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/browsers.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/chrome_downloads.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/chrome_history.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/edge_downloads.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/edge_history.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/extensions.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/README.md` | Resource purpose, provenance and usage documentation |
| `resources/assets/privacy/recent_docs.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/regedit.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/risk.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/run.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/sensitive.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/shopping.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/software.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/sources.json` | Artwork provenance, preparation metadata or design reference |
| `resources/assets/privacy/start_recent.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/thunder.png` | Privacy Cleanup cards and groups |
| `resources/assets/privacy/windows.png` | Privacy Cleanup cards and groups |
| `resources/assets/README.md` | Resource purpose, provenance and usage documentation |
| `resources/assets/runtime-assets.json` | Asset purpose and runtime/editable inventory |
| `resources/assets/software-compression/` | Retained UI resource: software compression |
| `resources/assets/software-compression/buttons/` | Retained UI resource: buttons |
| `resources/assets/status-warning.png` | Scan-result warning indicator |
| `resources/assets/tray.ico` | Application brand, window or tray icon |
| `resources/assets/ui/` | Retained UI resource: ui |
| `resources/assets/ui/application.svg` | Neutral application fallback when no registered or executable icon exists |
| `resources/assets/ui/components.svg` | Mint System Slimming Windows components icon |
| `resources/assets/ui/empty-file.svg` | Gray empty-file illustration for empty scan results |
| `resources/assets/ui/hibernation.svg` | Mint System Slimming hibernation icon |
| `resources/assets/ui/home-folder.svg` | Approved translucent mint Home hero folder illustration |
| `resources/assets/ui/home-hero-waves.svg` | Rounded Home hero mint wave background |
| `resources/assets/ui/pagefile.svg` | Mint System Slimming virtual memory icon |
| `resources/assets/ui/recycle_bin.svg` | Mint System Slimming recycle bin icon |
| `resources/assets/ui/result-check-off.svg` | Retained UI resource: result check off |
| `resources/assets/ui/result-check-on.svg` | Retained UI resource: result check on |
| `resources/assets/ui/result-check-partial.svg` | Retained UI resource: result check partial |
| `resources/assets/ui/result-copy.svg` | Retained UI resource: result copy |
| `resources/assets/ui/result-file.svg` | Retained UI resource: result file |
| `resources/assets/ui/result-folder.svg` | Retained UI resource: result folder |
| `resources/assets/ui/result-open-folder.svg` | Retained UI resource: result open folder |
| `resources/assets/ui/swapfile.svg` | Mint System Slimming swap file icon |
| `resources/locales/` | Embedded translations and localization notes Complete embedded runtime resources and provenance |
| `resources/locales/en.json` | English translations |
| `resources/locales/README.md` | Directory-specific usage and resource documentation |
| `resources/locales/rust-errors.json` | Localized operation and error messages |
| `resources/locales/rust-ui.json` | Shared UI translation dictionary and Simplified Chinese text |
| `resources/locales/zh-TW.json` | Traditional Chinese translations |
| `resources/rules/` | Embedded cleanup rule definitions Complete embedded runtime resources and provenance |
| `resources/rules/cdrive-private.json` | Bundled cleanup rule definitions and provenance, not user credentials |
| `resources/rules/drive-reviewed.json` | Reviewed C-drive cleanup categories and default selections |
| `resources/rules/README.md` | Directory-specific usage and resource documentation |
| `resources/windows/` | Embedded Windows system-helper scripts Complete embedded runtime resources and provenance |
| `resources/windows/disk-cleanup.ps1` | Allowlisted Windows cleanup-handler measurement and cleanup |
| `rust-toolchain.toml` | Pinned Rust compiler, tools and Windows target |
| `start.bat` | Console launcher for the locally built Rust EXE |
| `start.vbs` | Windowless launcher for the locally built Rust EXE |
