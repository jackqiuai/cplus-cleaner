# CPlus Cleaner — Windows Disk Cleaner & App Mover

**Free up disk space and manage installed applications.**

**English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [Report an issue](https://github.com/jackqiuai/cplus-cleaner/issues) · [Contributing](CONTRIBUTING.md) · [MIT License](LICENSE)

CPlus Cleaner is a Windows desktop utility for disk cleanup, application migration, NTFS compression and privacy cleanup. It combines chat cache cleanup, large-file management, duplicate detection, software uninstall and rule-based popup blocking in a Simplified Chinese, Traditional Chinese and English interface.

**Portable, no installation required.** Run the downloaded EXE directly, or extract the portable ZIP and launch `CPlusCleaner.exe`. No separate Python installation is needed.

**Project status:** Complete Python source, images, rules and language resources are included. Windows x64 EXE builds are configured in [GitHub Actions](https://github.com/jackqiuai/cplus-cleaner/actions/workflows/build-windows.yml). Successful runs provide executable artifacts; published GitHub Releases receive the EXE, portable ZIP and checksums. See [Build instructions](docs/BUILDING.md).

## Introduction video

https://github.com/user-attachments/assets/2d3ed710-2246-4981-836c-c8fb233a772b

**[Download the original English video (MP4)](https://github.com/jackqiuai/cplus-cleaner/raw/refs/heads/main/docs/videos/CPlus-Cleaner-intro-en.mp4)**

*80 seconds · 1080p · English interface screenshots, English synthesized narration and subtitles. Covers scanning, Drive Cleanup, App Mover, Large File Mover, App Compression and more. Personal paths are hidden; scan results vary by computer.*

## Screenshot

![CPlus Cleaner Home screen in English after a scan](docs/images/home-en.png)

*Home dashboard after a scan. Results vary with the computer and installed applications.*

## Features

| Feature | What it does |
| --- | --- |
| Home scan | Scan selected features, show progress and reuse results when opening a feature page. |
| Drive Cleanup | Summarize supported temporary files, caches, logs and application leftovers by category and size. Scan all time or the last 7, 30 or 180 days. |
| Chat Cleanup | Find supported cache and attachment locations for WeChat, QQ, WhatsApp, Telegram, Discord, Slack, Microsoft Teams and Signal. Coverage depends on the installed client and its data layout. |
| Large File Mover | Group files larger than 30 MiB by type. Expand a category to select, move or delete individual files or the category's files. |
| App Mover | Move eligible applications to another NTFS volume, verify copied contents and preserve the original path through a Windows directory junction. Applications containing system services are excluded. |
| App Compression | Compress eligible installations using Windows transparent compression and restore supported files from compression history. Protected installations are excluded. |
| Duplicate Cleanup | Match full file contents with SHA-256 and keep at least one copy in each group. |
| Privacy Cleanup | Review supported Windows usage traces, application traces, Chrome/Edge history and derived privacy categories. |
| Software Uninstall | Search installed applications, select individual or multiple entries and invoke their registered uninstallers. Review supported leftovers after uninstalling. |
| Popup Blocker | Detect supported applications and manage group and individual popup rules. Unsupported rules are omitted from the list. |
| System Management | Inspect system files and access recycle-bin, hibernation, virtual-memory and Windows component-store actions. |

Additional settings include daily scheduled cleanup, startup with Windows, close-to-tray behavior and a persistent language choice. Operation history opens in a separate window.

## Download the Windows EXE

Download `CPlusCleaner.exe` or `CPlusCleaner-windows-x64.zip` from [Releases](https://github.com/jackqiuai/cplus-cleaner/releases). Both are **portable Windows x64 editions that require no installation**: run the EXE directly, or extract the ZIP and run `CPlusCleaner.exe`. Python is included. For the latest commit build, open a successful [Actions run](https://github.com/jackqiuai/cplus-cleaner/actions/workflows/build-windows.yml) and download `CPlusCleaner-windows-x64`.

| Release file | Choose it when | How to use it |
| --- | --- | --- |
| `CPlusCleaner.exe` | You want to use the application directly. | Download and run it; no installation or separate Python runtime is required. |
| `CPlusCleaner-windows-x64.zip` | You prefer the portable bundle with its license and usage notes. | Extract it into a folder, then run the included `CPlusCleaner.exe`. |
| `Source code (zip)` / `Source code (tar.gz)` | You want to inspect, modify or run the Python source. | These are source archives; follow [Run from source](#run-from-source). |
| `SHA256SUMS.txt` | You want to verify the downloaded EXE or portable ZIP. | Compare the file's SHA-256 digest with the corresponding entry. |

## Requirements

- Windows desktop environment; intended for Windows 10 and Windows 11.
- When running from source: Python 3.10 or newer with Tkinter. Automated EXE builds and regression tests use Python 3.13.
- No third-party Python packages are required at runtime.
- Application migration requires different source/destination NTFS volumes; compression requires NTFS support.
- Some protected locations and Windows maintenance actions require administrator permission. Read-only component analysis can request Windows UAC approval.

## Run from source

Keep the complete source tree together, including `app.py`, `start.bat`, `cplus_cleaner/`, `assets/`, `rules/` and `locales/`. See [Source layout](docs/SOURCE_LAYOUT.md) for resource paths and publication checks.

The source folder can be named `CPLUSCLEANER`. The launcher and bundled resources resolve paths relative to the project, so renaming this folder does not require code changes. User settings, logs and recovery records are stored in `%LOCALAPPDATA%\CPLUSCLEANER\`.

1. Install Python with **Tcl/Tk and IDLE** enabled.
2. Clone this repository or download its source ZIP, then open the directory containing `app.py`.
3. Double-click `start.bat`, or run:

```powershell
python app.py
```

Repeated launches restore the existing window instead of opening another instance. After replacing source files, fully exit the old process before starting the updated application.

To check the source files and resource inventory without scanning or changing user files:

```powershell
python tools/verify_source_tree.py
```


Python code is organized under `cplus_cleaner/` by feature and shared responsibility. The root keeps only `app.py` as its Python launcher. See the [module purpose guide](docs/MODULES.md) and [image/rule resource guide](assets/README.md).

## Typical workflow

1. On **Home**, select features and click **Scan**, or open an individual feature page.
2. Review category sizes, record counts and selected items.
3. Uncheck items you want to retain, then choose **Delete**, **Move**, **Compress** or **Uninstall** as appropriate.
4. Review the outcome and operation history. Migration and compression maintain separate recovery records.

Drive and chat cleanup summarize categories rather than listing every file. Large File Mover and Duplicate Cleanup expose individual file selections.

## App Compression: practical experience

In one usage test, an application's disk usage decreased from **13.2GB** to **9.1GB**, saving about **4.1GB**. This is one observed result, not a guaranteed saving. Larger installations may offer more absolute savings, but their compression ratio is not necessarily higher; results depend on the files they contain.

In that test, application startup became noticeably slower after compression. **App Compression is generally not recommended for routine use**, especially for frequently used applications or when launch speed matters.

**If another partition has free space, prefer App Mover before considering App Compression.** Moving an application frees space on its original partition without reducing its total usage across all drives. Compression trades reduced disk usage against possible performance costs. To undo compression, use **App Compression → Compression history → Restore**.

Windows may leave some files uncompressed; these files are skipped without automatic retries. Such skips alone still count as a completed compression attempt, with **Restore** available in compression history.

## Frequently asked questions

<details>
<summary>Why does clicking × leave the application running?</summary>

When **Settings → Minimize to the system tray when closing** is enabled, × hides the main window while the application continues running. To fully quit, choose **Exit** from the top-right menu or the tray icon's right-click menu. Wait for active operations to finish first.

</details>

<details>
<summary>Why are some applications missing or unavailable in App Mover or App Compression?</summary>

App Mover considers eligible installations on the Windows system drive and excludes applications containing system services. System directories, shared components and unsupported linked or unreadable paths are also excluded. App Compression excludes protected installations and requires NTFS. Running applications or insufficient permissions can make an operation unavailable; review the status shown for the entry.

</details>

<details>
<summary>Why is the original application folder still visible after moving it?</summary>

App Mover creates a Windows directory junction at the original path, pointing to the destination folder. This keeps the original path accessible. Use **App Mover → Move history** to restore a supported move; retain the destination files while the application uses them.

</details>

<details>
<summary>Why does the space actually freed differ from the scan total?</summary>

Cleanup and large-file totals mainly describe logical file sizes. Actual disk space depends on allocation and which files can be processed. Changed, locked or inaccessible files may be skipped. App migration and compression candidates are reported separately from ordinary cleanup totals.

</details>

<details>
<summary>Does scheduled cleanup run after I exit the application?</summary>

The application must remain open or in the system tray. At the scheduled time, it runs a fresh all-time Drive Cleanup scan and deletes the default ordinary categories; it does not run every feature. A busy application defers the task, and fully exiting the process stops scheduling.

</details>

## Behavior to understand before cleaning

- **Delete permanently removes selected files; it does not send them to the Recycle Bin.** Scan and review selections before deleting.
- Chat Cleanup automatically selects supported categories with non-zero size after scanning. Some WeChat/QQ categories include received files and media, not just disposable caches.
- Privacy Cleanup can remove selected history records. It does not delete the corresponding downloaded documents or claim to erase all traces of activity.
- Scheduled cleanup first runs a fresh all-time Drive Cleanup scan, then deletes the default ordinary categories. It runs only while the application is open or in the tray; closing the process stops scheduling.
- Virtual memory, hibernation, the Recycle Bin and the component store are managed separately from ordinary cleanup totals.
- File operations recheck supported paths and file identities. Changes, access errors, active applications and unsupported data layouts can cause items to be skipped.
- File-size totals are logical sizes; they are not a guarantee of physical space reclaimed. Migration capacity and compression candidates are not added to the ordinary garbage total.
- Recovery history is not a backup of file contents. Applications may update or modify files after an operation.

## Restore moved or compressed applications

Close the affected application before restoring it and ensure the drive has enough free space for the restored data.

1. **App Mover:** Open **App Mover → Move history**, select the relevant record and click **Restore selected application**. Restoration copies and verifies the destination's current data back to the original location, replaces the junction with the restored folder and retains the destination copy. A valid local record and an unchanged junction/destination relationship are required.
2. **App Compression:** Open **App Compression → Compression history** and click **Restore** for the relevant entry. Restoration decompresses recorded files that still pass the path and file-identity checks. Replaced, changed or inaccessible files may be skipped; review the result and retain the records if restoration is incomplete.

Keep `%LOCALAPPDATA%\CPLUSCLEANER\`, including `software-migrations.jsonl` and the complete `compression/` directory, for as long as you need these recovery features. Keep the actual migrated files too. Recovery records describe operations and file identities; they do not contain a backup of application contents or recover permanently deleted files. Clearing **Activity Log** removes audit entries without restoring files or deleting the separate migration/compression recovery records.

## Language and local data

On first launch, Traditional Chinese Windows display languages (Taiwan, Hong Kong and Macao) select Traditional Chinese; other Chinese variants select Simplified Chinese, and other languages select English. Change the language in **Settings → Language**, choosing **简体中文**, **繁體中文** or **English**. Your choice is saved and takes effect immediately, preserving scan results and selections. Missing translations fall back to English. Software names, file paths and Windows-returned output retain their original values where appropriate.

The program stores settings, history and recovery manifests under:

```text
%LOCALAPPDATA%\CPLUSCLEANER\
```

Cleaning works locally without an account or cloud service. Opening a feedback link uses your browser; no diagnostic report is uploaded automatically. File-operation logs may contain local paths.

## Update or remove the portable application

The EXE and ZIP need no installation. Settings, logs and recovery records are stored in the Windows user-data directory above, rather than beside the EXE; moving the EXE alone does not transfer that data to another Windows account or computer.

### Update

Open the top-right **≡ → Check for updates** menu. The dialog compares the running version with the latest published stable [GitHub Release](https://github.com/jackqiuai/cplus-cleaner/releases/latest) and reports whether a newer version is available. Checks run only when requested and keep the interface responsive. They require access to GitHub, with no account or API key needed. Downloading and replacing the EXE remain manual steps; the checker does not overwrite a running program.

1. Let active operations finish. If you will change the EXE's location, first turn off **Settings → Launch at startup** and click **Save settings** in the old copy.
2. Fully quit using **Exit** from the application menu or tray menu. Download the new release and replace the old EXE, or extract the new portable ZIP into your chosen application folder.
3. Preserve the existing `%LOCALAPPDATA%\CPLUSCLEANER\` directory and migrated application folders. Back up the data directory if you need to retain settings and recovery records, then launch the new version. If its location changed, re-enable **Launch at startup** from the new copy when desired.

### Remove

1. In **Settings**, turn off **Launch at startup** and **Scheduled cleanup**, then click **Save settings**. Let active operations finish and fully quit using **Exit**.
2. Delete CPlus Cleaner's own EXE and the files supplied with its portable bundle. Keep any unrelated files and migrated application folders.
3. You may remove `%LOCALAPPDATA%\CPLUSCLEANER\` once you no longer need its settings, logs or recovery records. If you still need supported restoration, complete it first or retain both the recovery data and the actual application files.

Removing CPlus Cleaner does not undo previous application moves or compression, and it does not recover deleted files.

## Feedback and contributions

Use [GitHub Issues](https://github.com/jackqiuai/cplus-cleaner/issues) for reproducible problems and feature requests. Include the application version, Windows version, affected feature, reproduction steps, expected behavior and actual result. Redact personal paths and account information from screenshots and logs.

For development and test expectations, see [CONTRIBUTING.md](CONTRIBUTING.md). Run the included Windows regression suite with `python -m unittest discover -s tests -v`.

## Donate

Open the top-right menu and choose **Donate**. The Simplified Chinese interface displays a WeChat Pay QR code; the Traditional Chinese and English interfaces provide a [Buy me a coffee](https://ko-fi.com/qiujack) button. You can switch between these donation methods in the dialog. Donations are entirely optional and support ongoing project maintenance.

The same menu includes **About Cplus Cleaner**, with the application logo, current version and a **Give me a star** link to the project.

## Project status

The current application version is **1.1.5**. Download the Windows x64 EXE or portable ZIP from [Releases](https://github.com/jackqiuai/cplus-cleaner/releases/latest). GitHub Actions runs tests, builds the application and attaches the verified files during release publication. No installation is required.

## Code signing policy

Trusted signing is being prepared through the free SignPath Foundation program, subject to eligibility review. **Signing is not active, and published EXEs are currently unsigned.** See the [Code signing policy](docs/CODE_SIGNING.md) for the current status, third-party resource prerequisites and release verification steps.

## License

The original project code and documentation are licensed under the [MIT License](LICENSE). You may use, modify and distribute them free of charge, including for commercial purposes, provided that the copyright and permission notices are retained. The software is provided "AS IS", without warranty; see the license for the complete terms.

Third-party artwork, rule data and software names retain their applicable rights and are not automatically licensed under MIT by this project. Preserve their provenance and follow their original distribution terms; see the [resource guide](assets/README.md) and accompanying source records. The project is independent and is not affiliated with Microsoft, Tencent, Huorong or the other software vendors named above.

<!-- Purpose: Project overview, features and source launch instructions -->
