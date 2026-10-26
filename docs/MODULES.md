# Python module guide

The root contains only the public Python launcher. Runtime code lives in `cplus_cleaner/`; feature engines and their UI modules are kept together. Package `__init__.py` files mark package boundaries and contain no feature implementation.

| Entry | Purpose |
| --- | --- |
| [`app.py`](../app.py) | Desktop, background and read-only analysis dispatch |
| [`cplus_cleaner/__main__.py`](../cplus_cleaner/__main__.py) | Desktop launch through `python -m cplus_cleaner` |

## cplus_cleaner/application

| File | Purpose |
| --- | --- |
| [`single_instance.py`](../cplus_cleaner/application/single_instance.py) | Single-instance launch and activation |
| [`preferences.py`](../cplus_cleaner/application/preferences.py) | Settings, autostart and scheduling |
| [`feedback.py`](../cplus_cleaner/application/feedback.py) | Feedback dialog and issue link |
| [`tray.py`](../cplus_cleaner/application/tray.py) | Windows tray icon and menu |

## cplus_cleaner/common

| File | Purpose |
| --- | --- |
| [`core.py`](../cplus_cleaner/common/core.py) | Shared scanning, file checks, deletion, duplicate detection and history |
| [`image_opacity.py`](../cplus_cleaner/common/image_opacity.py) | PNG opacity processing and caching |

## cplus_cleaner/ui

| File | Purpose |
| --- | --- |
| [`desktop.py`](../cplus_cleaner/ui/desktop.py) | Main window, navigation, feature composition and settings dialog |
| [`desktop_panels.py`](../cplus_cleaner/ui/desktop_panels.py) | Shared result panels and activity log dialog |
| [`app_menu.py`](../cplus_cleaner/ui/app_menu.py) | Application dropdown menu |
| [`ui_tasks.py`](../cplus_cleaner/ui/ui_tasks.py) | Background task progress and UI delivery |
| [`ui_widgets.py`](../cplus_cleaner/ui/ui_widgets.py) | Shared buttons, selection, scrolling and icons |

## cplus_cleaner/localization

| File | Purpose |
| --- | --- |
| [`i18n.py`](../cplus_cleaner/localization/i18n.py) | Language selection, catalogs and dynamic text |
| [`localized_tk.py`](../cplus_cleaner/localization/localized_tk.py) | Tk text localization adapters |

## cplus_cleaner/platforms

| File | Purpose |
| --- | --- |
| [`software_windows.py`](../cplus_cleaner/platforms/software_windows.py) | Windows installed software, locations and icons |

## cplus_cleaner/features/drive_cleanup

| File | Purpose |
| --- | --- |
| [`cdrive.py`](../cplus_cleaner/features/drive_cleanup/cdrive.py) | Rule-based drive cleanup categories and time filters |
| [`cleanup_model.py`](../cplus_cleaner/features/drive_cleanup/cleanup_model.py) | Cleanup categories, selection and totals |

## cplus_cleaner/features/system

| File | Purpose |
| --- | --- |
| [`cdrive_system.py`](../cplus_cleaner/features/system/cdrive_system.py) | Recycle bin, hibernation, pagefile and component-store interfaces |
| [`system_actions_ui.py`](../cplus_cleaner/features/system/system_actions_ui.py) | System maintenance action UI |
| [`system_analysis.py`](../cplus_cleaner/features/system/system_analysis.py) | Component analysis, progress and elevated read-only jobs |
| [`system_analysis_helper.py`](../cplus_cleaner/features/system/system_analysis_helper.py) | Read-only component-analysis helper entry |
| [`system_management_ui.py`](../cplus_cleaner/features/system/system_management_ui.py) | System Management page |

## cplus_cleaner/features/home

| File | Purpose |
| --- | --- |
| [`home_core.py`](../cplus_cleaner/features/home/home_core.py) | Home feature catalog, disk data and totals |
| [`home_results.py`](../cplus_cleaner/features/home/home_results.py) | Home scan result handoff |
| [`home_scan.py`](../cplus_cleaner/features/home/home_scan.py) | Home scan sequence and progress |
| [`home_ui.py`](../cplus_cleaner/features/home/home_ui.py) | Home cards, illustration and scan state |

## cplus_cleaner/features/chat

| File | Purpose |
| --- | --- |
| [`chat_cleanup.py`](../cplus_cleaner/features/chat/chat_cleanup.py) | Chat data discovery, category scan and cleanup |
| [`chat_ui.py`](../cplus_cleaner/features/chat/chat_ui.py) | Chat Cleanup page |

## cplus_cleaner/features/large_files

| File | Purpose |
| --- | --- |
| [`large_files.py`](../cplus_cleaner/features/large_files/large_files.py) | Classification of files larger than 30 MiB, moving and recovery records |
| [`large_files_ui.py`](../cplus_cleaner/features/large_files/large_files_ui.py) | Large-file groups and file list UI |

## cplus_cleaner/features/privacy

| File | Purpose |
| --- | --- |
| [`privacy.py`](../cplus_cleaner/features/privacy/privacy.py) | Privacy category orchestration, scanning and deletion |
| [`privacy_browser.py`](../cplus_cleaner/features/privacy/privacy_browser.py) | Browser history and download records |
| [`privacy_windows.py`](../cplus_cleaner/features/privacy/privacy_windows.py) | Windows and application usage traces |
| [`privacy_ui.py`](../cplus_cleaner/features/privacy/privacy_ui.py) | Privacy Cleanup page |

## cplus_cleaner/features/app_mover

| File | Purpose |
| --- | --- |
| [`software_migration.py`](../cplus_cleaner/features/app_mover/software_migration.py) | App eligibility, verified copying, junctions and restore |
| [`software_migration_ui.py`](../cplus_cleaner/features/app_mover/software_migration_ui.py) | App Mover page and history |

## cplus_cleaner/features/app_compression

| File | Purpose |
| --- | --- |
| [`software_compression.py`](../cplus_cleaner/features/app_compression/software_compression.py) | Transparent compression, restore, eligibility and history |
| [`software_compression_ui.py`](../cplus_cleaner/features/app_compression/software_compression_ui.py) | App Compression page and progress |

## cplus_cleaner/features/uninstall

| File | Purpose |
| --- | --- |
| [`software_uninstall.py`](../cplus_cleaner/features/uninstall/software_uninstall.py) | Uninstall candidates, batch flow and results |
| [`software_uninstall_ui.py`](../cplus_cleaner/features/uninstall/software_uninstall_ui.py) | App Uninstaller page, search and selection |
| [`uninstall_residue.py`](../cplus_cleaner/features/uninstall/uninstall_residue.py) | Supported post-uninstall residue checks |
| [`uninstall_windows.py`](../cplus_cleaner/features/uninstall/uninstall_windows.py) | Windows uninstallers, processes and elevation |

## cplus_cleaner/features/popup

| File | Purpose |
| --- | --- |
| [`popup.py`](../cplus_cleaner/features/popup/popup.py) | Popup scan and blocking orchestration |
| [`popup_huorong.py`](../cplus_cleaner/features/popup/popup_huorong.py) | Huorong rule parsing and capability checks |
| [`popup_inventory.py`](../cplus_cleaner/features/popup/popup_inventory.py) | Popup software inventory and path matching |
| [`popup_models.py`](../cplus_cleaner/features/popup/popup_models.py) | Popup group, rule and state models |
| [`popup_native.py`](../cplus_cleaner/features/popup/popup_native.py) | Native popup matching and blocking adapters |
| [`popup_ui.py`](../cplus_cleaner/features/popup/popup_ui.py) | Popup groups, expansion, switches and history |
| [`popup_windows.py`](../cplus_cleaner/features/popup/popup_windows.py) | Windows window enumeration, process identity and actions |

## cplus_cleaner/features/duplicates

| File | Purpose |
| --- | --- |
| [`duplicates_ui.py`](../cplus_cleaner/features/duplicates/duplicates_ui.py) | Duplicate scan, retention and deletion UI |

## Development files

| Directory | Purpose |
| --- | --- |
| `tests/` | Project regression tests named after the feature under test |
| `tools/prepare_source_release.py` | Allowlisted export, integrity manifest and managed obsolete-file pruning |
| `tools/verify_source_tree.py` | Read-only import, rule, asset and manifest validation |
| `dev/`, `previews/`, `reports/`, `publication/` | Local research, previews, archives and generated output; excluded from commits |
