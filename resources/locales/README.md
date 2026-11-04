# Interface languages

| Stored code | Native name | Resource |
| --- | --- | --- |
| `zh` | 简体中文 | Source captions |
| `zh-TW` | 繁體中文 | `zh-TW.json` |
| `en` | English | `en.json` |

On first launch, Windows display languages for Taiwan, Hong Kong and Macao select Traditional Chinese. Other Chinese variants select Simplified Chinese; other languages or a failed language lookup select English. This uses the Windows UI language, not the keyboard or regional format.

Settings → Language saves the choice in `settings.json`. An empty value in older settings means automatic detection. Saving switches existing windows immediately while retaining scan results, filter values and selected items.

Catalogs contain complete messages and dynamic templates. Source captions remain resource keys. English templates use the existing `english` field; other catalogs use `translation`. Placeholders and source `parts` must stay aligned with the English catalog. Missing target messages and templates fall back to English.

`localized_tk.py` renders translated labels, dialogs, menus, tables and counters without changing model data. `MetricLabel` translates complete sentences before styling their numbers. Use `i18n.original(value)` for names, paths and system output that must retain their spelling. `i18n.display_name(value)` only uses complete known aliases in English; software names remain unchanged in Chinese interfaces.

Traditional Chinese uses reviewed interface terms such as 軟體, 檔案, 快取 and 資料夾. The shipped JSON is self-contained: the application needs no conversion library, online translation service, additional images or bundled fonts.

When adding captions, update both catalogs and their templates. Run `python tools/verify_source_tree.py` and `python -m unittest discover -s tests -p test_i18n.py -v` to check coverage, placeholders, fallback behavior and language switching.

<!-- Purpose: Interface language resources -->
