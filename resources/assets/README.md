# Image and rule resources

[`runtime-assets.json`](runtime-assets.json) lists every retained image or editable vector with its purpose in English. It contains 124 entries. Runtime PNG/ICO files are distinguished from editable SVG sources.

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
| `donation/wechat.png`, `donation/sources.json` | Owner-supplied WeChat QR image with a pixel-preserving conversion record |
| `chat-rules.json` | Chat cache and attachment location rules |
| `ui/*.svg` | Original code-native empty-state and mint System Slimming icons for the Rust interface |
| `popup-huorong.json` | Popup definitions and matching rules |

Forty unused images or vectors, including the old Home animation, replaced buttons and old QQ navigation icons, were moved to the local `dev/archive/assets/` directory and are excluded from publication. Current WeChat icons are retained. Icons used by project regression tests are also project resources.

Source/provenance records (`asset-sources.json`, `home/*manifest.json`, `privacy/sources.json`, and `brand/` metadata) document retained artwork and its history. Historical archive/member paths are research references, not files required to run the program. The committed Git tree records the current published bytes; see `docs/SOURCE_MAP.md` for the complete file inventory. See [`brand/HERO-ARTWORK.md`](brand/HERO-ARTWORK.md) for the static illustration behavior.

Retain third-party resource provenance; reorganizing directories does not change ownership or distribution terms. When adding images, update the purpose inventory, mappings and file purpose inventory. Keep preview screenshots and unused experimental artwork outside runtime resource directories.

PNG files and the PNG images embedded in the tray ICO contain an English `Purpose` text field. This metadata does not change image pixels or dimensions. Historical source digests describe the pre-annotation files; Git records current file bytes.

<!-- Purpose: Application icons, illustrations and cleanup rule resources -->
