# Release validation

The release source is checked from an independent Git clone. Required checks are formatting, Clippy with warnings denied, workspace regression tests, a locked release build, and executable smoke validation through `package.ps1`.

Tests use temporary fixtures. Some Windows cases require NTFS hard links, junctions, sparse/compressed files or suitable permissions. Do not substitute tests against user documents or installed applications.

`dist/build-validation.json` and checksums are generated outputs, never source inputs. GitHub Actions repeats the checks on a Windows runner and uploads the EXE, ZIP and SHA-256 checksums. A green workflow verifies compilation and automated checks; it does not establish compatibility with every installed application or authorize destructive operations.

Local historical measurements and machine-specific scan reports are excluded from the published source.