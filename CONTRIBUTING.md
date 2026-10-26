# Contributing to CPlus Cleaner

English and Chinese issues are welcome. The repository includes runtime source, resources and isolated tests; a packaged EXE is not available yet.

## Report a problem

Search [existing issues](https://github.com/jackqiuai/cplus-cleaner/issues) first. Include:

- Application version and Windows version.
- Affected feature and selected interface language.
- Minimal reproduction steps.
- Expected behavior and actual result.
- A redacted screenshot or a short relevant log excerpt, if useful.

Do not post browser history, account data, access tokens or unredacted personal paths. For a potential security issue, avoid publishing exploit details in a public issue; use GitHub's private vulnerability-reporting option if the repository offers it.

## Propose a feature

Describe the user problem, desired behavior and an example workflow. For a new cleanup target, specify the application, client version, exact cache layout and files that must be preserved. A folder being large is not enough evidence that it is disposable.

## Work with the source

The runtime uses Python's standard library and Tkinter. Keep authoring-only dependencies out of runtime modules. Use isolated workspace fixtures for filesystem tests; mock operations that would affect real installed applications, registry settings or other windows.

Use `cplus_cleaner/features/<feature>/` for feature engines and UI modules; shared responsibilities belong in `application/`, `common/`, `ui/`, `localization/` or `platforms/`. See [MODULES.md](docs/MODULES.md). Keep only the public `app.py` Python launcher at the root.

Keep project regression tests in `tests/`. Local experiments, screenshots, logs and private fixture data belong in ignored `dev/`, `previews/`, `tests/manual/` or `tests/fixtures/private/`; they are not publication inputs. Add runtime images to [the asset inventory](assets/runtime-assets.json) with a purpose.

After intentional edits, refresh and verify the source inventory:

```powershell
python tools/prepare_source_release.py --manifest-only
python tools/verify_source_tree.py
```

The release exporter uses an explicit inclusion list. Its optional `--prune-managed` removes only unchanged obsolete files listed in a destination's previous manifest; it refuses to remove modified obsolete files. Unknown destination files are untouched.

From a complete source checkout, run:

```powershell
python -m unittest discover -s tests -v
```

The Windows test suite includes real Tk workflows and fixture-only NTFS operations. Some tests require Windows features or permissions unavailable in restricted sandboxes. Run GUI tests serially.

## Change expectations

- Keep changes focused and explain the resulting behavior.
- Add a meaningful regression test for a reproduced bug.
- Preserve path checks, file-identity checks and the separation between scanning and file operations.
- Add Chinese/English display strings together; do not translate paths, rule IDs or application identities used for matching.
- Keep rule and asset provenance. Check distribution terms before adding third-party content.
- Describe validation and known limitations in the pull request.

## 中文说明

欢迎使用中文提交问题或建议。请提供版本、功能、复现步骤和实际结果，先遮盖截图与日志中的个人信息。新增清理规则需说明明确的缓存范围和必须保留的数据；测试只能使用隔离样本，不应处理真实用户文件或已安装软件。修改界面文案时请同步维护中英文资源。
