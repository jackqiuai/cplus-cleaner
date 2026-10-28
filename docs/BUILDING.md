# Windows executable builds

The [Build Windows EXE workflow](https://github.com/jackqiuai/cplus-cleaner/actions/workflows/build-windows.yml) produces a Windows x64 single-file application using Python 3.13 and PyInstaller. Python and Tcl/Tk are included; end users do not need to install Python.

| Event | Result |
| --- | --- |
| Push to `main` | Run Windows tests, build the EXE and upload Actions artifacts. |
| Push a `v*` tag | Build tagged source and upload Actions artifacts. |
| Pull request targeting `main` | Test and build without permission to publish a release. |
| Publish a GitHub Release | Build tagged source and attach the verified EXE, portable ZIP and checksums to that release. |
| Actions → Run workflow | Build on demand without creating a release. |

Publish releases from tags containing the build configuration. Update `preferences.VERSION` before tagging a new application version. A tag push does not itself create a GitHub Release. No personal access token is required: the release job uses the temporary `GITHUB_TOKEN` with `contents: write`; build jobs have read-only repository access.

## Download

- For commit builds, open a successful workflow run and download `CPlusCleaner-windows-x64`. GitHub may require sign-in for Actions artifact downloads. Artifacts are retained for 30 days.
- For published versions, download `CPlusCleaner.exe` or `CPlusCleaner-windows-x64.zip` from [Releases](https://github.com/jackqiuai/cplus-cleaner/releases).
- `SHA256SUMS.txt` contains digests for the EXE and portable ZIP. The ZIP includes the executable, MIT license and usage/provenance notes.

The workflow does not create an installer. Builds remain unsigned until the optional SignPath integration has been approved and explicitly enabled. See the [Code signing policy](CODE_SIGNING.md) for eligibility, activation, verification and the current pending status. Once enabled, release signing must succeed before signed artifacts can be uploaded; the ZIP and checksums are regenerated after signing. Original code and documentation use MIT; third-party artwork and rules retain their applicable rights and provenance.

## Build locally

Use a complete checkout on Windows with 64-bit Python 3.13:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-build.txt
.venv/Scripts/python.exe tools/verify_source_tree.py
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe tools/build_windows.py
```

Outputs are `dist/CPlusCleaner.exe`, `dist/CPlusCleaner-windows-x64.zip` and `dist/SHA256SUMS.txt`. Build dependencies are separate from the standard-library-only source runtime requirements. Generated binaries and build directories are ignored by Git.

## Bundle verification

The spec includes the `cplus_cleaner` modules, `assets/`, `rules/`, `locales/`, Tcl/Tk and `LICENSE`. The build checks PE architecture and the GUI subsystem, then runs the actual EXE with `--smoke-test` to validate resources, all feature pages and Chinese/English display. Checks use isolated settings and disable background scanning and file operations. The report is saved to `build/smoke-test.json`; build diagnostics are uploaded separately for 14 days.

Frozen launches use the executable itself for background startup and the read-only component-analysis helper. Resources load from the extraction directory; settings and history remain in the user's existing application-data directory. Self-protection excludes the running EXE without excluding unrelated files in its parent directory.

The native compression round-trip test uses a dedicated workspace on the CI system drive and checks that its real filesystem is NTFS before verifying compression, restoration and unchanged file hashes. For local tests on another filesystem, set `CPLUS_NATIVE_TEST_ROOT` to an existing, owned NTFS test directory.

Component-analysis tests validate command construction without running Windows servicing or requesting UAC on CI. Actual maintenance actions retain the source application's user interaction and permission requirements.

<!-- Purpose: Automated and local Windows executable build instructions -->
