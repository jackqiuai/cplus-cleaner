# Portable source export and verification tools

Source exports include the root `LICENSE` file. Run the verifier after exporting to check the source, resources and license inventory.

| Path | Purpose |
| --- | --- |
| [prepare_source_release.py](prepare_source_release.py) | Allowlisted export, integrity manifest and managed obsolete-file pruning |
| [verify_source_tree.py](verify_source_tree.py) | Read-only import, rule, asset and manifest validation |
