# Python module guide / Python 文件用途

The root contains only the public Python launcher. Runtime code lives in `cplus_cleaner/`; feature engines and their UI modules are kept together. Package `__init__.py` files mark package boundaries and contain no feature implementation.

根目录仅保留 Python 启动入口。运行代码位于 `cplus_cleaner/`；每项功能的扫描、操作和界面放在同一功能目录中。`__init__.py` 只用于声明包。

| Entry | Purpose / 用途 |
| --- | --- |
| [`app.py`](../app.py) | 桌面、后台和只读组件分析启动分派 / Desktop, background and read-only analysis dispatch |
| [`cplus_cleaner/__main__.py`](../cplus_cleaner/__main__.py) | `python -m cplus_cleaner` 桌面启动入口 / Package launch entry |

## cplus_cleaner/application

| File | Purpose / 用途 |
| --- | --- |
| [`single_instance.py`](../cplus_cleaner/application/single_instance.py) | 启动互斥、已有窗口唤起和后台启动 / Single-instance launch and activation |
| [`preferences.py`](../cplus_cleaner/application/preferences.py) | 设置持久化、开机启动和定时清理 / Settings, autostart and scheduling |
| [`feedback.py`](../cplus_cleaner/application/feedback.py) | 问题反馈窗口与 Issue 链接 / Feedback dialog and issue link |
| [`tray.py`](../cplus_cleaner/application/tray.py) | Windows 托盘图标与菜单 / Windows tray icon and menu |

## cplus_cleaner/common

| File | Purpose / 用途 |
| --- | --- |
| [`core.py`](../cplus_cleaner/common/core.py) | 通用扫描、文件校验、安全删除、重复检测与操作记录 / Shared scanning, file checks, deletion, duplicate detection and history |
| [`image_opacity.py`](../cplus_cleaner/common/image_opacity.py) | PNG 透明度处理及缓存 / PNG opacity processing and caching |

## cplus_cleaner/ui

| File | Purpose / 用途 |
| --- | --- |
| [`desktop.py`](../cplus_cleaner/ui/desktop.py) | 主窗口、导航、各功能装配和设置窗口 / Main window, navigation, feature composition and settings dialog |
| [`desktop_panels.py`](../cplus_cleaner/ui/desktop_panels.py) | 共享结果面板与操作记录窗口 / Shared result panels and activity log dialog |
| [`app_menu.py`](../cplus_cleaner/ui/app_menu.py) | 右上角菜单 / Application dropdown menu |
| [`ui_tasks.py`](../cplus_cleaner/ui/ui_tasks.py) | 后台任务与界面线程之间的进度、结果传递 / Background task progress and UI delivery |
| [`ui_widgets.py`](../cplus_cleaner/ui/ui_widgets.py) | 共用按钮、选择框、滚动和图标组件 / Shared buttons, selection, scrolling and icons |

## cplus_cleaner/localization

| File | Purpose / 用途 |
| --- | --- |
| [`i18n.py`](../cplus_cleaner/localization/i18n.py) | 语言选择、词典和动态文案翻译 / Language selection, catalogs and dynamic text |
| [`localized_tk.py`](../cplus_cleaner/localization/localized_tk.py) | Tk 控件文本的语言适配 / Tk text localization adapters |

## cplus_cleaner/platforms

| File | Purpose / 用途 |
| --- | --- |
| [`software_windows.py`](../cplus_cleaner/platforms/software_windows.py) | Windows 已安装软件索引、安装路径及图标 / Windows installed software, locations and icons |

## cplus_cleaner/features/drive_cleanup

| File | Purpose / 用途 |
| --- | --- |
| [`cdrive.py`](../cplus_cleaner/features/drive_cleanup/cdrive.py) | 按规则和时间范围汇总 C 盘清理类别 / Rule-based drive cleanup categories and time filters |
| [`cleanup_model.py`](../cplus_cleaner/features/drive_cleanup/cleanup_model.py) | 清理结果分类、勾选状态和合计模型 / Cleanup categories, selection and totals |

## cplus_cleaner/features/system

| File | Purpose / 用途 |
| --- | --- |
| [`cdrive_system.py`](../cplus_cleaner/features/system/cdrive_system.py) | 回收站、休眠、虚拟内存及组件存储接口 / Recycle bin, hibernation, pagefile and component-store interfaces |
| [`system_actions_ui.py`](../cplus_cleaner/features/system/system_actions_ui.py) | 系统维护操作的界面交互 / System maintenance action UI |
| [`system_analysis.py`](../cplus_cleaner/features/system/system_analysis.py) | 组件分析、进度解析及只读提权任务 / Component analysis, progress and elevated read-only jobs |
| [`system_analysis_helper.py`](../cplus_cleaner/features/system/system_analysis_helper.py) | 只读组件分析辅助进程入口 / Read-only component-analysis helper entry |
| [`system_management_ui.py`](../cplus_cleaner/features/system/system_management_ui.py) | 系统管理页面 / System Management page |

## cplus_cleaner/features/home

| File | Purpose / 用途 |
| --- | --- |
| [`home_core.py`](../cplus_cleaner/features/home/home_core.py) | 首页功能目录、磁盘数据和结果汇总 / Home feature catalog, disk data and totals |
| [`home_results.py`](../cplus_cleaner/features/home/home_results.py) | 首页结果到各功能页的传递 / Home scan result handoff |
| [`home_scan.py`](../cplus_cleaner/features/home/home_scan.py) | 首页扫描顺序和进度协调 / Home scan sequence and progress |
| [`home_ui.py`](../cplus_cleaner/features/home/home_ui.py) | 首页卡片、插图和扫描状态 / Home cards, illustration and scan state |

## cplus_cleaner/features/chat

| File | Purpose / 用途 |
| --- | --- |
| [`chat_cleanup.py`](../cplus_cleaner/features/chat/chat_cleanup.py) | 聊天客户端目录识别、分类扫描和清理 / Chat data discovery, category scan and cleanup |
| [`chat_ui.py`](../cplus_cleaner/features/chat/chat_ui.py) | 聊天清理页面 / Chat Cleanup page |

## cplus_cleaner/features/large_files

| File | Purpose / 用途 |
| --- | --- |
| [`large_files.py`](../cplus_cleaner/features/large_files/large_files.py) | 大于 30 MiB 文件分类、搬家及恢复记录 / Large-file classification, moving and recovery records |
| [`large_files_ui.py`](../cplus_cleaner/features/large_files/large_files_ui.py) | 大文件类别和文件列表界面 / Large-file groups and file list UI |

## cplus_cleaner/features/privacy

| File | Purpose / 用途 |
| --- | --- |
| [`privacy.py`](../cplus_cleaner/features/privacy/privacy.py) | 隐私类别编排、扫描和删除 / Privacy category orchestration, scanning and deletion |
| [`privacy_browser.py`](../cplus_cleaner/features/privacy/privacy_browser.py) | 浏览器历史和下载记录处理 / Browser history and download records |
| [`privacy_windows.py`](../cplus_cleaner/features/privacy/privacy_windows.py) | Windows 及软件使用痕迹 / Windows and application usage traces |
| [`privacy_ui.py`](../cplus_cleaner/features/privacy/privacy_ui.py) | 隐私清理页面 / Privacy Cleanup page |

## cplus_cleaner/features/app_mover

| File | Purpose / 用途 |
| --- | --- |
| [`software_migration.py`](../cplus_cleaner/features/app_mover/software_migration.py) | 软件搬家资格、复制校验、联接及还原 / App eligibility, verified copying, junctions and restore |
| [`software_migration_ui.py`](../cplus_cleaner/features/app_mover/software_migration_ui.py) | 软件搬家页面和记录 / App Mover page and history |

## cplus_cleaner/features/app_compression

| File | Purpose / 用途 |
| --- | --- |
| [`software_compression.py`](../cplus_cleaner/features/app_compression/software_compression.py) | 透明压缩、还原、资格检查和记录 / Transparent compression, restore, eligibility and history |
| [`software_compression_ui.py`](../cplus_cleaner/features/app_compression/software_compression_ui.py) | 软件压缩页面和进度 / App Compression page and progress |

## cplus_cleaner/features/uninstall

| File | Purpose / 用途 |
| --- | --- |
| [`software_uninstall.py`](../cplus_cleaner/features/uninstall/software_uninstall.py) | 卸载候选、批处理流程和结果 / Uninstall candidates, batch flow and results |
| [`software_uninstall_ui.py`](../cplus_cleaner/features/uninstall/software_uninstall_ui.py) | 软件卸载页面、搜索和选择 / App Uninstaller page, search and selection |
| [`uninstall_residue.py`](../cplus_cleaner/features/uninstall/uninstall_residue.py) | 卸载后的已支持残留检查 / Supported post-uninstall residue checks |
| [`uninstall_windows.py`](../cplus_cleaner/features/uninstall/uninstall_windows.py) | Windows 卸载程序、进程及提权调用 / Windows uninstallers, processes and elevation |

## cplus_cleaner/features/popup

| File | Purpose / 用途 |
| --- | --- |
| [`popup.py`](../cplus_cleaner/features/popup/popup.py) | 弹窗扫描和拦截协调 / Popup scan and blocking orchestration |
| [`popup_huorong.py`](../cplus_cleaner/features/popup/popup_huorong.py) | 火绒规则解析与支持性检查 / Huorong rule parsing and capability checks |
| [`popup_inventory.py`](../cplus_cleaner/features/popup/popup_inventory.py) | 规则关联的软件索引和安装路径匹配 / Popup software inventory and path matching |
| [`popup_models.py`](../cplus_cleaner/features/popup/popup_models.py) | 弹窗组、单条规则和状态数据 / Popup group, rule and state models |
| [`popup_native.py`](../cplus_cleaner/features/popup/popup_native.py) | 原生弹窗匹配和拦截适配 / Native popup matching and blocking adapters |
| [`popup_ui.py`](../cplus_cleaner/features/popup/popup_ui.py) | 弹窗分组、展开、开关和拦截记录 / Popup groups, expansion, switches and history |
| [`popup_windows.py`](../cplus_cleaner/features/popup/popup_windows.py) | Windows 窗口枚举、进程识别和窗口操作 / Windows window enumeration, process identity and actions |

## cplus_cleaner/features/duplicates

| File | Purpose / 用途 |
| --- | --- |
| [`duplicates_ui.py`](../cplus_cleaner/features/duplicates/duplicates_ui.py) | 重复文件扫描、保留和删除界面 / Duplicate scan, retention and deletion UI |

## Development files / 开发文件

| Directory | Purpose / 用途 |
| --- | --- |
| `tests/` | 本项目的隔离回归测试，按被测功能命名 / Project regression tests named after the feature under test |
| `tools/prepare_source_release.py` | 白名单导出、完整性清单及受管旧文件清理 / Allowlisted export, integrity manifest and managed obsolete-file pruning |
| `tools/verify_source_tree.py` | 只读校验导入、规则、图片和发布清单 / Read-only import, rule, asset and manifest validation |
| `dev/`, `previews/`, `reports/`, `publication/` | 仅本地研究、预览、归档和生成输出；不上传 / Local research, previews, archives and generated output; excluded from commits |
