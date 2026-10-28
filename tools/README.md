# Portable source export and verification tools

Source exports include the root `LICENSE` file. Run the verifier after exporting to check the source, resources and license inventory.

The verifier also rejects a `.github/README` that would override the root project overview and screenshot on GitHub. Use `.github/PURPOSE.md` for the directory guide.

| Path | Purpose |
| --- | --- |
| [build_windows.py](build_windows.py) | Windows builds, signature verification, isolated smoke checks and distribution archives |
| [prepare_source_release.py](prepare_source_release.py) | Allowlisted export, integrity manifest and managed obsolete-file pruning |
| [verify_source_tree.py](verify_source_tree.py) | Read-only import, rule, asset and manifest validation |
