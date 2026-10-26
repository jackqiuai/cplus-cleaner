# Image and rule resources

[`runtime-assets.json`](runtime-assets.json) lists every retained image or editable vector with its purpose in English. It contains 116 entries. Runtime PNG/ICO files are distinguished from editable SVG sources. The documentation screenshot lives separately at `docs/images/home-en.png`.

| Path | Purpose |
| --- | --- |
| `app-icon.png`, `tray.ico`, `brand/sidebar.png` | Application, window, tray and sidebar brand |
| `brand/logo.svg` | Editable brand source |
| `hero-clean.png`, `hero-large.png` | Drive Cleanup and Large File Mover illustrations |
| `home/background.png`, `home/search.*` | Home background and scan icon |
| `nav-*.png`, `nav-system*.svg` | Navigation states and editable system icon sources |
| `check-*.png` | Shared checked, unchecked and mixed states |
| `categories/*.png`, `category-icons.json` | Cleanup category icons and mappings |
| `privacy/*.png` | Privacy group and result icons |
| `large-c-*.png` | Large-file category icons |
| `icon-*.png` | Application and category fallback icons, including WeChat |
| `status-warning.png` | Scan-result warning icon |
| `chat-rules.json` | Chat cache and attachment location rules |
| `popup-huorong.json` | Popup definitions and matching rules |

Forty unused images or vectors, including the old Home animation, replaced buttons and old QQ navigation icons, were moved to the local `dev/archive/assets/` directory and are excluded from publication. Current WeChat icons are retained. Icons used by project regression tests are also project resources.

Source/provenance records (`asset-sources.json`, `home/*manifest.json`, `privacy/sources.json`, and `brand/` metadata) document retained artwork and its history. Historical archive/member paths are research references, not files required to run the program. `SOURCE-MANIFEST.json` at the project root covers the current published bytes. See [`brand/HERO-ARTWORK.md`](brand/HERO-ARTWORK.md) for the static illustration behavior.

Retain third-party resource provenance; reorganizing directories does not change ownership or distribution terms. When adding images, update the purpose inventory, mappings and source integrity manifest. Keep preview screenshots and unused experimental artwork outside runtime resource directories.
