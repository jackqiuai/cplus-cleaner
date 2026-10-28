# Source layout

The repository contains the complete runtime source tree and its checked-in resources. It supports source execution and automated Windows x64 EXE builds.

| Path | Purpose |
| --- | --- |
| `app.py`, `start.bat` | Public launching entry points. |
| `cplus_cleaner/application/` | Single-instance handling, preferences, feedback and tray. |
| `cplus_cleaner/common/`, `ui/`, `localization/`, `platforms/` | Shared file operations, interface, language and Windows software integration. |
| `cplus_cleaner/features/` | Home, drive/chat/privacy cleanup, large files, app moving/compression/uninstall, popup blocking, duplicates and system management. |
| `cplus_cleaner/features/system/system_analysis_helper.py` | Read-only analysis helper dispatched through `app.py --component-analysis-job`; Windows UAC is requested only for that analysis. |
| `assets/` | Icons, illustrations, selection controls, image mappings, chat rules and popup definitions. |
| `rules/cdrive-private.json` | Reviewed drive-cleanup rule catalog. |
| `locales/en.json` | English display strings and dynamic templates; Chinese source strings remain in the program. |
| `tests/` | Isolated backend tests and real Tk interface workflows. |
| `.github/ISSUE_TEMPLATE/` | Problem reports and feature requests. |
| `docs/images/home-en.png` | English Home screenshot shown in README. |
| `tools/verify_source_tree.py` | Read-only source/resource and manifest validation. |
| `tools/prepare_source_release.py` | Export helper with explicit inclusion rules. |
| `SOURCE-MANIFEST.json` | Exported file inventory with normalized-text/binary SHA-256 digests. |
| `LICENSE` | MIT license for original project code and documentation. |
| `CPlusCleaner.spec`, `requirements-build.txt` | Windows single-file bundle and pinned build dependencies. |
| `.github/workflows/build-windows.yml` | Windows tests, EXE builds, optional approved signing and release attachments. |
| `.github/signpath-artifact.xml`, `docs/CODE_SIGNING.md` | Signing artifact restrictions, pending policy and activation instructions. |

See [the complete module guide](MODULES.md) for each Python file and [the resource guide](../assets/README.md) for image and rule purposes.

Keep the default English project overview in the root `README.md`. The `.github/` directory guide is named `PURPOSE.md`: a README in that directory would take priority over the root overview on GitHub and hide its screenshot from the repository landing page. The source verifier checks for this conflict.

## Run and verify

Install Python 3.10+ with Tcl/Tk. No third-party runtime packages are required.

```powershell
python tools/verify_source_tree.py
python app.py
# Alternative desktop entry:
python -m cplus_cleaner
```

Alternatively, double-click `start.bat`. Paths resolve relative to the source files rather than the current working directory. Keep the resource folders beside `app.py` and `cplus_cleaner/` when moving the source tree.

```powershell
python -m unittest discover -s tests -v
```

Tests run serially and use temporary workspace fixtures. Windows-dependent tests need Windows; some NTFS operations need filesystem permissions unavailable in restricted sandboxes.

After deliberately editing source/resources, refresh the inventory with `python tools/prepare_source_release.py --manifest-only`, then verify it again. Hashes detect missing or changed files; the inventory is not a signed authenticity certificate.

## Assets and rule provenance

The original project code and documentation are licensed under [MIT](../LICENSE). The C+ brand has editable SVG sources under `assets/brand/`. The static drive-cleanup and large-file illustrations are described in `assets/brand/HERO-ARTWORK.md`. Some category icons, layout artwork and rule definitions were recovered from third-party software during local research. Source archives, members and recorded hashes are retained in the accompanying JSON metadata; this does not claim those materials as original project assets or assign them the project's MIT license.

Unused artwork is archived locally under ignored `dev/archive/assets/`; it is absent from the published tree. The 116 retained images/vectors are listed in `assets/runtime-assets.json`. Historical extraction records can reference earlier source material. Current exported bytes are covered by `SOURCE-MANIFEST.json`. References prefixed with `source-analysis/` identify historical, external research inputs; they are not runtime dependencies and are not included in this source tree.

Do not include local operation logs, personal settings, browser data, credentials, raw research directories, test previews, generated EXEs or temporary recovery copies in a source commit. The export allowlist and `.gitignore` exclude those categories.

## EXE packaging

[Build instructions](BUILDING.md) describe the Windows workflow, portable artifacts, resource bundling and safe executable checks. The build includes all runtime resources; frozen startup and component analysis call the EXE directly. Settings remain outside the temporary resource directory.

<!-- Purpose: Source layout, resource paths and publication checks -->
